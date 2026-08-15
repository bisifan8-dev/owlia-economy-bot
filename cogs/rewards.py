# rewards.py - Updated with additive multipliers and consistent rewards

import re
import discord
from discord import app_commands
from discord.ext import commands
import datetime
import random
import string
from database import get_db
from safety import safety_wrapper, financial_safety, InputValidator
from utils.errors import SmartErrorMessages


class RewardsCog(commands.Cog):
    """Rewards system with daily, weekly, monthly, yearly claims and special coupons."""

    def __init__(self, bot):
        self.bot = bot
        # Reward amounts - SMALLER, more reasonable
        self.DAILY_REWARD = 0.25      # Was 0.50 - now half
        self.WEEKLY_REWARD = 1.00     # Was 2.00 - now half
        self.MONTHLY_REWARD = 4.00    # Was 10.00 - now less than half
        self.YEARLY_REWARD = 20.00    # Was 50.00 - now less than half
        
        # Cooldown periods in hours
        self.DAILY_COOLDOWN = 24
        self.WEEKLY_COOLDOWN = 168  # 7 days
        self.MONTHLY_COOLDOWN = 720  # 30 days
        self.YEARLY_COOLDOWN = 8760  # 365 days
        
        # Transaction thresholds for ADDITIVE multipliers (not multiplicative)
        # Each tier gives +0.1x, capped at 2.0x total
        self.TRANSACTION_MULTIPLIERS = [
            (10, 0.1),
            (25, 0.1),
            (50, 0.1),
            (75, 0.1),
            (100, 0.1),
            (150, 0.1),
            (200, 0.1),
            (300, 0.1),
            (400, 0.1),
            (500, 0.1),
            (750, 0.1),
            (1000, 0.1),
        ]
        # Max additional multiplier from transactions: 1.2x (total 2.0x)
        # So base 1.0 + up to 1.2 = 2.2x max with shout bonus
        
        # Shout opt-in bonus: +0.25x (was 2.0x multiplicative, now additive)
        self.SHOUT_OPT_IN_BONUS = 0.25
        
        # Shout threshold for special coupon
        self.SHOUTS_FOR_COUPON = 10
        self.COUPON_REWARD = 100.00  # Was 300.00 - reduced significantly

    def _get_transaction_multiplier(self, transaction_count: int) -> float:
        """Get the ADDITIVE multiplier based on transaction count."""
        # Start at 1.0 (base)
        total_add = 0.0
        for threshold, add in self.TRANSACTION_MULTIPLIERS:
            if transaction_count >= threshold:
                total_add += add
        # Cap at 1.2 additional (2.2x total)
        return min(1.2, total_add)

    def _generate_coupon_code(self) -> str:
        """Generate a unique coupon code."""
        chars = string.ascii_uppercase + string.digits
        blocks = []
        for _ in range(3):
            block = ''.join(random.choices(chars, k=3))
            blocks.append(block)
        return '-'.join(blocks)

    async def _get_user_transaction_count(self, user_id: int) -> int:
        """Get total number of transactions for a user."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as count FROM transaction_log WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            return row["count"] if row else 0

    async def _is_user_opted_in_to_shouts(self, user_id: int) -> bool:
        """Check if user is opted in to shouts."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id FROM shout_blacklist WHERE user_id = ?",
                (user_id,)
            )
            return cursor.fetchone() is None

    async def _get_user_shout_count(self, user_id: int) -> int:
        """Get total number of shouts received by a user."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(DISTINCT shout_id) as count 
                FROM shout_messages 
                WHERE user_id = ? AND status = 'SENT'
                """,
                (user_id,)
            )
            row = cursor.fetchone()
            return row["count"] if row else 0

    async def _calculate_total_multiplier(self, user_id: int) -> tuple:
        """Calculate total ADDITIVE multiplier for a user."""
        # Start at 1.0 (base)
        total_mult = 1.0
        
        # Get transaction multiplier (additive)
        tx_count = await self._get_user_transaction_count(user_id)
        tx_add = self._get_transaction_multiplier(tx_count)
        total_mult += tx_add
        
        # Get shout opt-in bonus (additive)
        is_opted_in = await self._is_user_opted_in_to_shouts(user_id)
        if is_opted_in:
            total_mult += self.SHOUT_OPT_IN_BONUS
        
        return total_mult, tx_add, self.SHOUT_OPT_IN_BONUS if is_opted_in else 0.0, tx_count, is_opted_in

    def _check_cooldown(self, last_claim: str, cooldown_hours: int) -> tuple:
        """Check if cooldown has expired."""
        if not last_claim:
            return True, 0
        
        last_time = datetime.datetime.fromisoformat(last_claim)
        next_available = last_time + datetime.timedelta(hours=cooldown_hours)
        now = datetime.datetime.now()
        
        if now >= next_available:
            return True, 0
        
        remaining = next_available - now
        return False, remaining.total_seconds()

    def _format_time_remaining(self, seconds: float) -> str:
        """Format seconds into a readable string."""
        if seconds <= 0:
            return "Available now!"
        
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds_remaining = int(seconds % 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds_remaining > 0 and not parts:
            parts.append(f"{seconds_remaining}s")
        
        return " ".join(parts) if parts else "Less than a minute"

    async def _claim_reward(self, interaction: discord.Interaction, reward_type: str, 
                           base_amount: float, cooldown_hours: int, 
                           column_name: str) -> bool:
        """Generic reward claim function."""
        total_mult, tx_add, shout_add, tx_count, is_opted_in = await self._calculate_total_multiplier(
            interaction.user.id
        )
        
        # Calculate final reward
        final_amount = base_amount * total_mult
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                f"SELECT {column_name} FROM users WHERE user_id = ?",
                (interaction.user.id,)
            )
            row = cursor.fetchone()
            last_claim = row[column_name] if row else None
            
            can_claim, remaining = self._check_cooldown(last_claim, cooldown_hours)
            
            if not can_claim:
                time_str = self._format_time_remaining(remaining)
                reward_names = {
                    "last_daily": "Daily",
                    "last_weekly": "Weekly", 
                    "last_monthly": "Monthly",
                    "last_yearly": "Yearly"
                }
                await interaction.followup.send(
                    f"⏳ **{reward_names.get(column_name, 'Reward')} Reward Cooldown!**\n"
                    f"Please wait **{time_str}** before claiming again.",
                    ephemeral=True
                )
                return False
            
            cursor.execute(
                """
                INSERT INTO users (user_id, balance, message_count, premium_credits) 
                VALUES (?, 0, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET 
                    balance = balance + ?,
                    {column_name} = ?
                """.format(column_name=column_name),
                (interaction.user.id, final_amount, datetime.datetime.now().isoformat())
            )
            
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    interaction.user.id,
                    f"{column_name}_reward",
                    final_amount,
                    f"{column_name.replace('_', ' ').title()} reward: ${final_amount:.2f} (base: ${base_amount:.2f} x {total_mult:.2f}x multiplier)",
                    None
                )
            )
            conn.commit()
        
        reward_names = {
            "last_daily": ("Daily", "🌅"),
            "last_weekly": ("Weekly", "📅"),
            "last_monthly": ("Monthly", "🌙"),
            "last_yearly": ("Yearly", "🎆")
        }
        name, emoji = reward_names.get(column_name, ("Reward", "💰"))
        
        embed = discord.Embed(
            title=f"{emoji} {name} Reward Claimed!",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.add_field(
            name="💰 Reward Amount",
            value=f"**${final_amount:.2f}**",
            inline=True
        )
        embed.add_field(
            name="📊 Base Amount",
            value=f"${base_amount:.2f}",
            inline=True
        )
        embed.add_field(
            name="✨ Total Multiplier",
            value=f"**{total_mult:.2f}x**",
            inline=True
        )
        embed.add_field(
            name="📈 Transaction Bonus",
            value=f"+{tx_add:.2f}x ({tx_count} transactions)",
            inline=True
        )
        embed.add_field(
            name="🔊 Shout Opt-in Bonus",
            value=f"+{shout_add:.2f}x ({'✅ Opted in' if is_opted_in else '❌ Opted out'})",
            inline=True
        )
        embed.set_footer(
            text=f"Claimed by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        await interaction.followup.send(embed=embed)
        return True

    @app_commands.command(
        name="daily",
        description="🌅 Claim your daily reward ($0.25 base, with multipliers!)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def daily(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._claim_reward(
            interaction,
            "daily",
            self.DAILY_REWARD,
            self.DAILY_COOLDOWN,
            "last_daily"
        )

    @app_commands.command(
        name="weekly",
        description="📅 Claim your weekly reward ($1.00 base, with multipliers!)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._claim_reward(
            interaction,
            "weekly",
            self.WEEKLY_REWARD,
            self.WEEKLY_COOLDOWN,
            "last_weekly"
        )

    @app_commands.command(
        name="monthly",
        description="🌙 Claim your monthly reward ($4.00 base, with multipliers!)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def monthly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._claim_reward(
            interaction,
            "monthly",
            self.MONTHLY_REWARD,
            self.MONTHLY_COOLDOWN,
            "last_monthly"
        )

    @app_commands.command(
        name="yearly",
        description="🎆 Claim your yearly reward ($20.00 base, with multipliers!)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def yearly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._claim_reward(
            interaction,
            "yearly",
            self.YEARLY_REWARD,
            self.YEARLY_COOLDOWN,
            "last_yearly"
        )

    @app_commands.command(
        name="rewards_info",
        description="📊 Check your reward multipliers and cooldown status"
    )
    async def rewards_info(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        total_mult, tx_add, shout_add, tx_count, is_opted_in = await self._calculate_total_multiplier(
            interaction.user.id
        )
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_daily, last_weekly, last_monthly, last_yearly FROM users WHERE user_id = ?",
                (interaction.user.id,)
            )
            row = cursor.fetchone()
            
            cooldowns = {
                "daily": (row["last_daily"] if row else None, self.DAILY_COOLDOWN, self.DAILY_REWARD),
                "weekly": (row["last_weekly"] if row else None, self.WEEKLY_COOLDOWN, self.WEEKLY_REWARD),
                "monthly": (row["last_monthly"] if row else None, self.MONTHLY_COOLDOWN, self.MONTHLY_REWARD),
                "yearly": (row["last_yearly"] if row else None, self.YEARLY_COOLDOWN, self.YEARLY_REWARD),
            }
        
        embed = discord.Embed(
            title="📊 Reward Information",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        
        embed.add_field(
            name="✨ Current Multipliers",
            value=(
                f"**Total:** {total_mult:.2f}x\n"
                f"**Base:** 1.00x\n"
                f"**Transaction Bonus:** +{tx_add:.2f}x ({tx_count} transactions)\n"
                f"**Shout Opt-in:** +{shout_add:.2f}x ({'✅ Opted in' if is_opted_in else '❌ Opted out'})"
            ),
            inline=False
        )
        
        cooldown_text = ""
        for name, (last, cooldown, amount) in cooldowns.items():
            can_claim, remaining = self._check_cooldown(last, cooldown)
            status = "✅ Available now!" if can_claim else f"⏳ {self._format_time_remaining(remaining)}"
            cooldown_text += f"**{name.title()}** (${amount:.2f} base): {status}\n"
        
        embed.add_field(
            name="⏰ Cooldown Status",
            value=cooldown_text,
            inline=False
        )
        
        threshold_text = ""
        for threshold, add in self.TRANSACTION_MULTIPLIERS:
            status = "✅" if tx_count >= threshold else "❌"
            threshold_text += f"{status} {threshold}+ transactions → +{add:.1f}x\n"
        threshold_text += f"\n*Max bonus: +1.2x (2.2x total)*"
        
        embed.add_field(
            name="📈 Transaction Bonus Tiers",
            value=threshold_text,
            inline=False
        )
        
        shout_count = await self._get_user_shout_count(interaction.user.id)
        embed.add_field(
            name="🔊 Shout Progress",
            value=(
                f"**Shouts Received:** {shout_count}/{self.SHOUTS_FOR_COUPON}\n"
                f"**Progress:** {'█' * min(shout_count, self.SHOUTS_FOR_COUPON)}{'░' * (self.SHOUTS_FOR_COUPON - min(shout_count, self.SHOUTS_FOR_COUPON))}\n"
                f"{'🎉 You qualify for a special coupon!' if shout_count >= self.SHOUTS_FOR_COUPON else f'{self.SHOUTS_FOR_COUPON - shout_count} more shouts needed for a coupon'}"
            ),
            inline=False
        )
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="special_coupon",
        description="🎫 Claim your special coupon reward (requires 10 shouts received)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def special_coupon(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        shout_count = await self._get_user_shout_count(interaction.user.id)
        
        if shout_count < self.SHOUTS_FOR_COUPON:
            await interaction.followup.send(
                f"❌ You need **{self.SHOUTS_FOR_COUPON}** shouts to claim a special coupon!\n"
                f"You have received **{shout_count}** shouts so far.\n"
                f"Keep participating in the community to receive more shouts!",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT last_coupon FROM users WHERE user_id = ?",
                (interaction.user.id,)
            )
            row = cursor.fetchone()
            last_coupon = row["last_coupon"] if row else None
            
            can_claim, remaining = self._check_cooldown(last_coupon, 720)  # 30 day cooldown
            
            if not can_claim:
                time_str = self._format_time_remaining(remaining)
                await interaction.followup.send(
                    f"⏳ **Coupon Cooldown!**\n"
                    f"You already claimed a special coupon recently.\n"
                    f"Please wait **{time_str}** before claiming another one.",
                    ephemeral=True
                )
                return
            
            coupon_code = self._generate_coupon_code()
            
            cursor.execute(
                """
                INSERT INTO special_coupons (code, user_id, amount, claimed_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    coupon_code,
                    interaction.user.id,
                    self.COUPON_REWARD,
                    datetime.datetime.now().isoformat(),
                    (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
                )
            )
            
            cursor.execute(
                """
                INSERT INTO users (user_id, balance, message_count, premium_credits) 
                VALUES (?, 0, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET 
                    last_coupon = ?
                """,
                (interaction.user.id, datetime.datetime.now().isoformat())
            )
            
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'coupon_generated', ?, ?, ?)
                """,
                (
                    interaction.user.id,
                    self.COUPON_REWARD,
                    f"Special coupon generated: {coupon_code}",
                    None
                )
            )
            conn.commit()
        
        embed = discord.Embed(
            title="🎫 Special Coupon Generated!",
            description=(
                f"Congratulations! You've received a special coupon for receiving **{self.SHOUTS_FOR_COUPON}** shouts!\n\n"
                f"**Your Coupon Code:**\n"
                f"`{coupon_code}`\n\n"
                f"Use `/redeem_coupon code:{coupon_code}` to claim **${self.COUPON_REWARD:.2f}**!\n\n"
                f"⚠️ This coupon expires in 30 days."
            ),
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now()
        )
        embed.set_footer(
            text=f"Claimed by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="redeem_coupon",
        description="🎫 Redeem a special coupon code for money"
    )
    @app_commands.describe(
        code="The coupon code to redeem"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def redeem_coupon(
        self,
        interaction: discord.Interaction,
        code: str
    ):
        await interaction.response.defer(ephemeral=True)
        
        clean_code = code.strip().upper()
        
        if not re.match(r'^[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}$', clean_code):
            await interaction.followup.send(
                "❌ Invalid coupon format! Use the format: `XXX-XXX-XXX`\n"
                "Example: `ABC-123-DEF`",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                SELECT * FROM special_coupons 
                WHERE code = ? AND redeemed_by IS NULL
                """,
                (clean_code,)
            )
            coupon = cursor.fetchone()
            
            if not coupon:
                cursor.execute(
                    "SELECT * FROM special_coupons WHERE code = ?",
                    (clean_code,)
                )
                existing = cursor.fetchone()
                if existing and existing["redeemed_by"] is not None:
                    await interaction.followup.send(
                        f"❌ This coupon has already been redeemed by <@{existing['redeemed_by']}>.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "❌ Invalid coupon code! Please check the code and try again.",
                        ephemeral=True
                    )
                return
            
            if coupon["expires_at"]:
                expires = datetime.datetime.fromisoformat(coupon["expires_at"])
                if datetime.datetime.now() > expires:
                    await interaction.followup.send(
                        f"❌ This coupon expired on <t:{int(expires.timestamp())}:F>.",
                        ephemeral=True
                    )
                    return
            
            cursor.execute(
                """
                UPDATE special_coupons 
                SET redeemed_by = ?, redeemed_at = ?
                WHERE code = ?
                """,
                (interaction.user.id, datetime.datetime.now().isoformat(), clean_code)
            )
            
            cursor.execute(
                """
                INSERT INTO users (user_id, balance, message_count, premium_credits) 
                VALUES (?, 0, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
                """,
                (interaction.user.id, coupon["amount"])
            )
            
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'coupon_redeemed', ?, ?, ?)
                """,
                (
                    interaction.user.id,
                    coupon["amount"],
                    f"Redeemed coupon {clean_code} for ${coupon['amount']:.2f}",
                    None
                )
            )
            conn.commit()
        
        embed = discord.Embed(
            title="🎫 Coupon Redeemed!",
            description=f"Successfully redeemed coupon **{clean_code}** for **${coupon['amount']:.2f}**!",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        embed.set_footer(
            text=f"Redeemed by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="admin_give_coupon",
        description="🎫 Admin: Generate a special coupon for a user"
    )
    @app_commands.describe(
        user="The user to give the coupon to",
        amount="Amount the coupon is worth (default: $100)"
    )
    @app_commands.default_permissions(administrator=True)
    @safety_wrapper("admin")
    async def admin_give_coupon(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        amount: float = 100.00
    ):
        await interaction.response.defer(ephemeral=True)
        
        if amount <= 0:
            await interaction.followup.send(
                "❌ Amount must be greater than zero.",
                ephemeral=True
            )
            return
        
        coupon_code = self._generate_coupon_code()
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO special_coupons (code, user_id, amount, claimed_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    coupon_code,
                    user.id,
                    amount,
                    datetime.datetime.now().isoformat(),
                    (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
                )
            )
            
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'admin_coupon_generated', ?, ?, ?)
                """,
                (
                    interaction.user.id,
                    amount,
                    f"Admin generated coupon {coupon_code} for {user.display_name}",
                    None
                )
            )
            conn.commit()
        
        embed = discord.Embed(
            title="🎫 Admin Coupon Generated",
            description=(
                f"**Coupon Code:** `{coupon_code}`\n"
                f"**User:** {user.mention}\n"
                f"**Amount:** ${amount:.2f}\n"
                f"**Expires:** 30 days from now\n\n"
                f"Tell {user.mention} to use `/redeem_coupon code:{coupon_code}` to claim their reward!"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now()
        )
        embed.set_footer(
            text=f"Generated by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        await interaction.followup.send(embed=embed)
        
        try:
            await user.send(
                f"🎫 **You've received a special coupon!**\n"
                f"**Code:** `{coupon_code}`\n"
                f"**Amount:** ${amount:.2f}\n"
                f"**Expires:** 30 days from now\n\n"
                f"Use `/redeem_coupon code:{coupon_code}` in the server to claim your reward!"
            )
        except discord.Forbidden:
            pass


async def setup(bot):
    await bot.add_cog(RewardsCog(bot))