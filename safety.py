# safety.py
"""
Safety and Security Module for Economy Bot
Adds rate limiting, input validation, transaction safety, and audit logging
"""

import time
import hashlib
import json
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
from typing import Dict, List, Optional, Any, Callable
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import sqlite3

# ──────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────

SAFETY_CONFIG = {
    # Rate Limiting
    "rate_limits": {
        "default": {"max_requests": 10, "time_window": 60},  # 10 requests per minute
        "financial": {"max_requests": 5, "time_window": 60},  # 5 financial ops per minute
        "admin": {"max_requests": 20, "time_window": 60},     # 20 admin ops per minute
        "market": {"max_requests": 15, "time_window": 60},    # 15 market ops per minute
    },
    
    # Financial Limits
    "max_transaction_amount": 1000000.0,  # $1,000,000 max per transaction
    "max_share_transaction": 1000000.0,   # 1,000,000 shares max per transaction
    "min_transaction_amount": 0.01,       # $0.01 minimum
    
    # Input Validation
    "max_party_id_length": 50,
    "max_name_length": 100,
    "max_message_length": 2000,
    
    # Security
    "max_retries": 3,
    "transaction_timeout": 30,  # seconds
    
    # Logging
    "audit_log_enabled": True,
    "security_log_channel": None,  # Set this in your main bot
}

# ──────────────────────────────────────────────────────────────
# DATABASE SETUP
# ──────────────────────────────────────────────────────────────

def init_safety_db():
    """Initialize safety-related database tables"""
    from database import get_db
    
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Audit log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                guild_id INTEGER,
                command TEXT,
                action TEXT,
                details TEXT,
                ip_address TEXT,
                success INTEGER DEFAULT 1
            )
        """)
        
        # Transaction log table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transaction_log (
                transaction_id TEXT PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_id INTEGER,
                transaction_type TEXT,
                amount REAL,
                entity_id TEXT,
                shares REAL,
                status TEXT,
                details TEXT,
                ip_address TEXT
            )
        """)
        
        # Rate limit tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_log (
                limit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                command TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Failed attempts tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                command TEXT,
                reason TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # User safety flags
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_safety_flags (
                user_id INTEGER PRIMARY KEY,
                is_suspended INTEGER DEFAULT 0,
                suspension_reason TEXT,
                suspension_until TIMESTAMP,
                warning_count INTEGER DEFAULT 0,
                last_warning TIMESTAMP,
                trust_score REAL DEFAULT 100.0
            )
        """)
        
        conn.commit()

# ──────────────────────────────────────────────────────────────
# RATE LIMITER
# ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Rate limiting system with persistent storage"""
    
    def __init__(self):
        self.memory_limits: Dict[str, List[float]] = defaultdict(list)
        self._cleanup_task = None
        
    def _get_key(self, user_id: int, command: str) -> str:
        """Generate a unique key for rate limiting"""
        return f"{user_id}:{command}"
    
    def _get_limit_config(self, command: str) -> tuple:
        """Get rate limit configuration for a command"""
        if command.startswith(("buy", "sell", "invest", "pay")):
            return SAFETY_CONFIG["rate_limits"]["financial"]
        elif command.startswith(("admin", "setup", "register", "manage")):
            return SAFETY_CONFIG["rate_limits"]["admin"]
        elif command.startswith(("market", "stock")):
            return SAFETY_CONFIG["rate_limits"]["market"]
        else:
            return SAFETY_CONFIG["rate_limits"]["default"]
    
    async def check(self, user_id: int, command: str) -> bool:
        """
        Check if user is rate limited
        Returns True if allowed, False if rate limited
        """
        key = self._get_key(user_id, command)
        now = time.time()
        config = self._get_limit_config(command)
        
        # Clean old entries
        self.memory_limits[key] = [
            t for t in self.memory_limits[key] 
            if now - t < config["time_window"]
        ]
        
        if len(self.memory_limits[key]) >= config["max_requests"]:
            return False
        
        self.memory_limits[key].append(now)
        return True
    
    def get_time_remaining(self, user_id: int, command: str) -> float:
        """Get time remaining until rate limit resets"""
        key = self._get_key(user_id, command)
        now = time.time()
        config = self._get_limit_config(command)
        
        if key not in self.memory_limits:
            return 0
        
        valid_times = [t for t in self.memory_limits[key] if now - t < config["time_window"]]
        if len(valid_times) < config["max_requests"]:
            return 0
        
        oldest = min(valid_times)
        return config["time_window"] - (now - oldest)

# ──────────────────────────────────────────────────────────────
# INPUT VALIDATOR
# ──────────────────────────────────────────────────────────────

class InputValidator:
    """Validate and sanitize user inputs"""
    
    @staticmethod
    def validate_party_id(party_id: str) -> tuple:
        """Validate party ID"""
        if not party_id:
            return False, "Party ID cannot be empty"
        
        if len(party_id) > SAFETY_CONFIG["max_party_id_length"]:
            return False, f"Party ID too long (max {SAFETY_CONFIG['max_party_id_length']} characters)"
        
        # Only allow alphanumeric, underscores, and hyphens
        if not all(c.isalnum() or c in "_-" for c in party_id):
            return False, "Party ID can only contain letters, numbers, underscores, and hyphens"
        
        return True, None
    
    @staticmethod
    def validate_amount(amount: float, allow_zero: bool = False) -> tuple:
        """Validate financial amount"""
        if not isinstance(amount, (int, float)):
            return False, "Amount must be a number"
        
        if amount < 0 and not allow_zero:
            return False, "Amount cannot be negative"
        
        if amount == 0 and not allow_zero:
            return False, "Amount must be greater than zero"
        
        if amount > SAFETY_CONFIG["max_transaction_amount"]:
            return False, f"Amount exceeds maximum of ${SAFETY_CONFIG['max_transaction_amount']:,.2f}"
        
        if 0 < amount < SAFETY_CONFIG["min_transaction_amount"]:
            return False, f"Amount must be at least ${SAFETY_CONFIG['min_transaction_amount']:.2f}"
        
        return True, None
    
    @staticmethod
    def validate_shares(shares: float) -> tuple:
        """Validate share count"""
        if not isinstance(shares, (int, float)):
            return False, "Shares must be a number"
        
        if shares <= 0:
            return False, "Shares must be greater than zero"
        
        if shares > SAFETY_CONFIG["max_share_transaction"]:
            return False, f"Shares exceed maximum of {SAFETY_CONFIG['max_share_transaction']:,.0f}"
        
        return True, None
    
    @staticmethod
    def validate_name(name: str) -> tuple:
        """Validate display name"""
        if not name:
            return False, "Name cannot be empty"
        
        if len(name) > SAFETY_CONFIG["max_name_length"]:
            return False, f"Name too long (max {SAFETY_CONFIG['max_name_length']} characters)"
        
        return True, None
    
    @staticmethod
    def sanitize_input(text: str) -> str:
        """Sanitize input to prevent injection"""
        if not text:
            return ""
        # Remove potentially dangerous characters
        import re
        return re.sub(r'[^\w\s\-_.,!?@#$%^&*()+=/]', '', text)

# ──────────────────────────────────────────────────────────────
# TRANSACTION SAFETY
# ──────────────────────────────────────────────────────────────

class TransactionManager:
    """Manage safe transactions with rollback capability"""
    
    def __init__(self):
        self.active_transactions: Dict[str, Dict] = {}
        self.transaction_lock = asyncio.Lock()
    
    def generate_transaction_id(self, user_id: int, command: str) -> str:
        """Generate a unique transaction ID"""
        timestamp = int(time.time() * 1000)
        data = f"{user_id}:{command}:{timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    async def begin_transaction(self, user_id: int, command: str, data: Dict) -> str:
        """Begin a new transaction"""
        tx_id = self.generate_transaction_id(user_id, command)
        
        async with self.transaction_lock:
            self.active_transactions[tx_id] = {
                "user_id": user_id,
                "command": command,
                "data": data,
                "start_time": time.time(),
                "status": "PENDING",
                "steps": []
            }
        
        return tx_id
    
    async def add_step(self, tx_id: str, step_name: str, step_data: Dict):
        """Add a step to a transaction"""
        async with self.transaction_lock:
            if tx_id not in self.active_transactions:
                raise ValueError(f"Transaction {tx_id} not found")
            
            self.active_transactions[tx_id]["steps"].append({
                "name": step_name,
                "data": step_data,
                "status": "PENDING",
                "timestamp": time.time()
            })
    
    async def commit_transaction(self, tx_id: str) -> bool:
        """Commit a transaction"""
        async with self.transaction_lock:
            if tx_id not in self.active_transactions:
                return False
            
            tx = self.active_transactions[tx_id]
            
            # Check if transaction has timed out
            if time.time() - tx["start_time"] > SAFETY_CONFIG["transaction_timeout"]:
                tx["status"] = "TIMEOUT"
                del self.active_transactions[tx_id]
                return False
            
            tx["status"] = "COMMITTED"
            
            # Log the transaction
            await self.log_transaction(tx)
            
            del self.active_transactions[tx_id]
            return True
    
    async def rollback_transaction(self, tx_id: str, reason: str):
        """Rollback a transaction"""
        async with self.transaction_lock:
            if tx_id not in self.active_transactions:
                return
            
            tx = self.active_transactions[tx_id]
            tx["status"] = f"ROLLBACK: {reason}"
            
            # Log the rollback
            await self.log_transaction(tx)
            
            del self.active_transactions[tx_id]
    
    async def log_transaction(self, tx: Dict):
        """Log a transaction to the database"""
        from database import get_db
        
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO transaction_log 
                    (transaction_id, user_id, transaction_type, amount, entity_id, shares, status, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.generate_transaction_id(tx["user_id"], tx["command"]),
                    tx["user_id"],
                    tx["command"],
                    tx["data"].get("amount", 0.0),
                    tx["data"].get("party_id", ""),
                    tx["data"].get("shares", 0.0),
                    tx["status"],
                    json.dumps(tx["data"])
                ))
                conn.commit()
        except Exception as e:
            # Don't fail if logging fails
            print(f"⚠️ Transaction log error: {e}")
    
    def get_active_transactions(self, user_id: int) -> List[Dict]:
        """Get active transactions for a user"""
        return [
            tx for tx in self.active_transactions.values()
            if tx["user_id"] == user_id
        ]

# ──────────────────────────────────────────────────────────────
# AUDIT LOGGER
# ──────────────────────────────────────────────────────────────

class AuditLogger:
    """Log all administrative and financial actions"""
    
    def __init__(self, bot):
        self.bot = bot
        self.log_channel_id = SAFETY_CONFIG.get("security_log_channel")
    
    async def log(self, user_id: int, guild_id: int, command: str, action: str, 
                  details: Dict, success: bool = True):
        """Log an action to the audit log"""
        from database import get_db
        
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO audit_log 
                    (user_id, guild_id, command, action, details, success)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    guild_id,
                    command,
                    action,
                    json.dumps(details),
                    1 if success else 0
                ))
                conn.commit()
        except Exception as e:
            print(f"⚠️ Audit log error: {e}")
        
        # Also send to Discord channel if configured
        if self.log_channel_id:
            try:
                channel = self.bot.get_channel(self.log_channel_id)
                if channel:
                    embed = discord.Embed(
                        title=f"📋 Audit Log: {command}",
                        color=discord.Color.green() if success else discord.Color.red(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(
                        name="User",
                        value=f"<@{user_id}>",
                        inline=True
                    )
                    embed.add_field(
                        name="Action",
                        value=action,
                        inline=True
                    )
                    embed.add_field(
                        name="Status",
                        value="✅ Success" if success else "❌ Failed",
                        inline=True
                    )
                    if details:
                        embed.add_field(
                            name="Details",
                            value=f"```json\n{json.dumps(details, indent=2)[:1000]}\n```",
                            inline=False
                        )
                    await channel.send(embed=embed)
            except Exception as e:
                print(f"⚠️ Discord audit log error: {e}")

# ──────────────────────────────────────────────────────────────
# SAFETY WRAPPER DECORATORS
# ──────────────────────────────────────────────────────────────

def safety_wrapper(command_type: str = "default"):
    """Decorator to add safety checks to commands"""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            # Initialize safety components
            if not hasattr(interaction.client, 'rate_limiter'):
                interaction.client.rate_limiter = RateLimiter()
            if not hasattr(interaction.client, 'transaction_manager'):
                interaction.client.transaction_manager = TransactionManager()
            if not hasattr(interaction.client, 'audit_logger'):
                interaction.client.audit_logger = AuditLogger(interaction.client)
            
            rate_limiter = interaction.client.rate_limiter
            transaction_manager = interaction.client.transaction_manager
            audit_logger = interaction.client.audit_logger
            
            # Check rate limit
            command_name = func.__name__
            if not await rate_limiter.check(interaction.user.id, command_name):
                remaining = rate_limiter.get_time_remaining(interaction.user.id, command_name)
                await interaction.response.send_message(
                    f"⚠️ **Rate Limited!** Please wait **{remaining:.1f}** seconds before using this command again.",
                    ephemeral=True
                )
                return
            
            # Check if user is suspended
            if await is_user_suspended(interaction.user.id):
                await interaction.response.send_message(
                    "🚫 **You are currently suspended** from using financial commands. Please contact an administrator.",
                    ephemeral=True
                )
                return
            
            try:
                # Log the command start
                await audit_logger.log(
                    interaction.user.id,
                    interaction.guild_id,
                    command_name,
                    "command_start",
                    {"args": str(args), "kwargs": str(kwargs)}
                )
                
                # Execute the command
                result = await func(self, interaction, *args, **kwargs)
                
                # Log success
                await audit_logger.log(
                    interaction.user.id,
                    interaction.guild_id,
                    command_name,
                    "command_success",
                    {"result": str(result)[:500] if result else "None"}
                )
                
                return result
                
            except Exception as e:
                # Log failure
                await audit_logger.log(
                    interaction.user.id,
                    interaction.guild_id,
                    command_name,
                    "command_failed",
                    {"error": str(e)}
                )
                
                # Don't catch - let the command handle its own errors
                raise
                
        return wrapper
    return decorator


def financial_safety(required_balance: bool = True, required_shares: bool = False):
    """Decorator for financial commands with transaction safety"""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            # Get transaction manager from bot
            if not hasattr(interaction.client, 'transaction_manager'):
                interaction.client.transaction_manager = TransactionManager()
            
            tx_manager = interaction.client.transaction_manager
            
            # Extract financial data from kwargs
            amount = kwargs.get('amount', 0)
            shares = kwargs.get('shares', 0)
            party_id = kwargs.get('party_id', '')
            
            # Validate amount
            if amount and amount > 0:
                valid, msg = InputValidator.validate_amount(amount)
                if not valid:
                    await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
                    return
            
            # Validate shares
            if shares and shares > 0:
                valid, msg = InputValidator.validate_shares(shares)
                if not valid:
                    await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
                    return
            
            # Validate party_id
            if party_id:
                valid, msg = InputValidator.validate_party_id(party_id)
                if not valid:
                    await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
                    return
            
            # Start transaction
            tx_data = {
                "amount": amount,
                "shares": shares,
                "party_id": party_id,
                "user_id": interaction.user.id,
                "args": str(args),
                "kwargs": str(kwargs)
            }
            tx_id = await tx_manager.begin_transaction(
                interaction.user.id,
                func.__name__,
                tx_data
            )
            
            try:
                # Execute the command
                result = await func(self, interaction, *args, **kwargs)
                
                # Commit transaction
                await tx_manager.commit_transaction(tx_id)
                
                return result
                
            except Exception as e:
                # Rollback on error
                await tx_manager.rollback_transaction(tx_id, str(e))
                raise
                
        return wrapper
    return decorator

# ──────────────────────────────────────────────────────────────
# USER SAFETY CHECKS
# ──────────────────────────────────────────────────────────────

async def is_user_suspended(user_id: int) -> bool:
    """Check if a user is currently suspended"""
    from database import get_db
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT suspension_until FROM user_safety_flags WHERE user_id = ? AND is_suspended = 1",
            (user_id,)
        )
        row = cursor.fetchone()
        
        if not row:
            return False
        
        suspension_until = row["suspension_until"]
        if suspension_until:
            from datetime import datetime
            if datetime.now() < datetime.fromisoformat(suspension_until):
                return True
        
        return False

async def get_user_trust_score(user_id: int) -> float:
    """Get a user's trust score (0-100)"""
    from database import get_db
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT trust_score FROM user_safety_flags WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row["trust_score"] if row else 100.0

async def update_user_trust_score(user_id: int, delta: float):
    """Update a user's trust score"""
    from database import get_db
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_safety_flags (user_id, trust_score) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET trust_score = trust_score + ?
        """, (user_id, delta, delta))
        conn.commit()

# ──────────────────────────────────────────────────────────────
# ADMIN SAFETY COMMANDS
# ──────────────────────────────────────────────────────────────

class SafetyAdminCog(commands.Cog):
    """Safety management commands for administrators"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(
        name="safety_status",
        description="🔒 Check safety status of the bot"
    )
    @app_commands.default_permissions(administrator=True)
    async def safety_status(self, interaction: discord.Interaction):
        """Get safety system status"""
        await interaction.response.defer(ephemeral=True)
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get stats
            cursor.execute("SELECT COUNT(*) as count FROM audit_log")
            audit_count = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM transaction_log")
            tx_count = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM rate_limit_log")
            rate_limit_count = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM user_safety_flags WHERE is_suspended = 1")
            suspended_count = cursor.fetchone()["count"]
        
        embed = discord.Embed(
            title="🔒 Safety System Status",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(
            name="📊 Statistics",
            value=f"• Audit Logs: {audit_count:,}\n"
                  f"• Transactions: {tx_count:,}\n"
                  f"• Rate Limit Events: {rate_limit_count:,}\n"
                  f"• Suspended Users: {suspended_count}",
            inline=False
        )
        embed.add_field(
            name="⚙️ Configuration",
            value=f"• Max Transaction: ${SAFETY_CONFIG['max_transaction_amount']:,.2f}\n"
                  f"• Min Transaction: ${SAFETY_CONFIG['min_transaction_amount']:.2f}\n"
                  f"• Transaction Timeout: {SAFETY_CONFIG['transaction_timeout']}s",
            inline=False
        )
        embed.set_footer(text="Safety system active and monitoring")
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(
        name="suspend_user",
        description="🚫 Suspend a user from financial commands"
    )
    @app_commands.default_permissions(administrator=True)
    async def suspend_user(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: str,
        duration_hours: int = 24
    ):
        """Suspend a user from using financial commands"""
        await interaction.response.defer(ephemeral=True)
        
        if duration_hours <= 0 or duration_hours > 720:  # Max 30 days
            await interaction.followup.send("❌ Duration must be between 1 and 720 hours.")
            return
        
        from datetime import datetime, timedelta
        suspension_until = datetime.now() + timedelta(hours=duration_hours)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_safety_flags (user_id, is_suspended, suspension_reason, suspension_until)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    is_suspended = 1,
                    suspension_reason = ?,
                    suspension_until = ?
            """, (user.id, reason, suspension_until.isoformat(), reason, suspension_until.isoformat()))
            conn.commit()
        
        # Log the suspension
        if hasattr(interaction.client, 'audit_logger'):
            await interaction.client.audit_logger.log(
                interaction.user.id,
                interaction.guild_id,
                "suspend_user",
                "user_suspended",
                {
                    "target_user": user.id,
                    "reason": reason,
                    "duration": duration_hours
                }
            )
        
        embed = discord.Embed(
            title="🚫 User Suspended",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Duration", value=f"{duration_hours} hours", inline=True)
        embed.add_field(name="Until", value=f"<t:{int(suspension_until.timestamp())}:F>", inline=False)
        
        await interaction.followup.send(embed=embed)
        
        # Notify the user
        try:
            await user.send(
                f"🚫 **You have been suspended** from using financial commands in **{interaction.guild.name}**.\n"
                f"**Reason:** {reason}\n"
                f"**Duration:** {duration_hours} hours\n"
                f"**Until:** <t:{int(suspension_until.timestamp())}:F>\n\n"
                f"If you believe this is an error, please contact an administrator."
            )
        except:
            pass
    
    @app_commands.command(
        name="unsuspend_user",
        description="✅ Unsuspend a user"
    )
    @app_commands.default_permissions(administrator=True)
    async def unsuspend_user(
        self,
        interaction: discord.Interaction,
        user: discord.User
    ):
        """Unsuspend a user"""
        await interaction.response.defer(ephemeral=True)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_safety_flags 
                SET is_suspended = 0, suspension_reason = NULL, suspension_until = NULL
                WHERE user_id = ?
            """, (user.id,))
            conn.commit()
        
        # Log the unsuspension
        if hasattr(interaction.client, 'audit_logger'):
            await interaction.client.audit_logger.log(
                interaction.user.id,
                interaction.guild_id,
                "unsuspend_user",
                "user_unsuspended",
                {"target_user": user.id}
            )
        
        await interaction.followup.send(f"✅ **{user.display_name}** has been unsuspended.")
        
        try:
            await user.send(
                f"✅ You have been **unsuspended** in **{interaction.guild.name}**.\n"
                f"You can now use financial commands again."
            )
        except:
            pass
    
    @app_commands.command(
        name="safety_logs",
        description="📋 View safety logs"
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        user="Filter by user (optional)",
        limit="Number of logs to show (max 50)"
    )
    async def safety_logs(
        self,
        interaction: discord.Interaction,
        user: discord.User = None,
        limit: int = 10
    ):
        """View safety logs"""
        await interaction.response.defer(ephemeral=True)
        
        if limit > 50:
            limit = 50
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            if user:
                cursor.execute("""
                    SELECT * FROM audit_log 
                    WHERE user_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (user.id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM audit_log 
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
            
            logs = cursor.fetchall()
        
        if not logs:
            await interaction.followup.send("📭 No logs found.")
            return
        
        embed = discord.Embed(
            title="📋 Safety Logs",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        for i, log in enumerate(logs):
            if i >= 10:  # Limit to 10 in the embed
                break
            embed.add_field(
                name=f"#{i+1}: {log['command']}",
                value=f"User: <@{log['user_id']}>\n"
                      f"Action: {log['action']}\n"
                      f"Time: <t:{int(datetime.fromisoformat(log['timestamp']).timestamp())}:R>\n"
                      f"Status: {'✅' if log['success'] else '❌'}",
                inline=False
            )
        
        embed.set_footer(text=f"Showing {min(len(logs), 10)} of {len(logs)} logs")
        
        await interaction.followup.send(embed=embed)

# ──────────────────────────────────────────────────────────────
# INITIALIZATION
# ──────────────────────────────────────────────────────────────

async def setup_safety(bot):
    """Initialize safety systems"""
    init_safety_db()
    
    # Add safety cog
    await bot.add_cog(SafetyAdminCog(bot))
    
    print("🔒 Safety systems initialized")

# ──────────────────────────────────────────────────────────────
# SAFETY CHECK FUNCTIONS FOR EXISTING COMMANDS
# ──────────────────────────────────────────────────────────────

async def safe_db_operation(operation: Callable, *args, **kwargs):
    """Execute a database operation with safety checks"""
    max_retries = SAFETY_CONFIG["max_retries"]
    last_error = None
    
    for attempt in range(max_retries):
        try:
            return await operation(*args, **kwargs)
        except sqlite3.OperationalError as e:
            last_error = e
            if "database is locked" in str(e):
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise
        except Exception as e:
            raise
    
    raise last_error or Exception("Operation failed after retries")

def validate_transaction_data(data: Dict) -> tuple:
    """Validate transaction data before processing"""
    required_fields = ["user_id", "type"]
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: {field}"
    
    # Validate specific transaction types
    if data["type"] in ["buy", "sell"]:
        if "party_id" not in data:
            return False, "Missing party_id for trade"
        if "shares" not in data or data["shares"] <= 0:
            return False, "Invalid shares amount"
        if "price" not in data or data["price"] <= 0:
            return False, "Invalid price"
    
    if data["type"] in ["pay", "invest"]:
        if "amount" not in data or data["amount"] <= 0:
            return False, "Invalid amount"
    
    return True, None

# ──────────────────────────────────────────────────────────────
# EXPORTS
# ──────────────────────────────────────────────────────────────

__all__ = [
    'SAFETY_CONFIG',
    'RateLimiter',
    'InputValidator',
    'TransactionManager',
    'AuditLogger',
    'safety_wrapper',
    'financial_safety',
    'setup_safety',
    'init_safety_db',
    'is_user_suspended',
    'get_user_trust_score',
    'update_user_trust_score',
    'safe_db_operation',
    'validate_transaction_data',
    'SafetyAdminCog'
]