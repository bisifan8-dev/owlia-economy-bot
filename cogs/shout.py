import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
from database import get_db, get_user_managed_parties
from safety import safety_wrapper, financial_safety, InputValidator
from utils.errors import SmartErrorMessages
from typing import List, Dict, Optional, Set

class ShoutCog(commands.Cog):
    """Shout system with 5-hour admin strike window and treasury support."""

    def __init__(self, bot):
        self.bot = bot
        self.active_shouts: Dict[int, Dict] = {}
        self.SHOUT_COST = 500.0
        self.OPT_OUT_COST = 20.0
        self.COOLDOWN_HOURS = 24
        self.STRIKE_WINDOW_HOURS = 5
        self.DM_RATE_LIMIT = 30
        self.PROGRESS_UPDATE_INTERVAL = 10
        self.strike_tasks: Dict[int, asyncio.Task] = {}
        # Superuser ID that bypasses all restrictions
        self.SUPERUSER_ID = 938992963403542580

    def _get_user_managed_parties(self, user_id: int) -> List[Dict]:
        """Get all parties/companies the user manages."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.party_id, p.name, p.manager_role_id, p.treasury, p.is_company, p.structure_type
                FROM parties p
                WHERE p.is_setup = 1
            """)
            all_parties = cursor.fetchall()
        
        managed = []
        # Get user's roles
        guild = None
        for g in self.bot.guilds:
            member = g.get_member(user_id)
            if member:
                guild = g
                break
        
        if not guild:
            return managed
        
        user_role_ids = {str(r.id) for r in guild.get_member(user_id).roles} if guild.get_member(user_id) else set()
        is_admin = guild.get_member(user_id).guild_permissions.administrator if guild.get_member(user_id) else False
        
        for p in all_parties:
            if is_admin:
                managed.append(p)
            elif p["manager_role_id"] and p["manager_role_id"] in user_role_ids:
                managed.append(p)
        
        return managed

    def _get_entity_by_id(self, party_id: str) -> Optional[Dict]:
        """Get a single entity by ID."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.party_id, p.name, p.manager_role_id, p.treasury, p.is_company, p.structure_type
                FROM parties p
                WHERE p.party_id = ? AND p.is_setup = 1
            """, (party_id,))
            return cursor.fetchone()

    async def _send_shout_as_entity(
        self,
        guild: discord.Guild,
        shout_id: int,
        message: str,
        include_bots: bool,
        sender: discord.User,
        entity_name: str,
        entity_id: str,
        is_company: bool
    ):
        """Process a shout sent on behalf of an entity."""
        try:
            members = [m for m in guild.members if include_bots or not m.bot]
            blacklisted = await self.get_blacklisted_users()
            members = [m for m in members if m.id not in blacklisted]
            
            total_targeted = len(members)
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE shout_log 
                    SET total_targeted = ?, status = 'SENDING'
                    WHERE shout_id = ?
                """, (total_targeted, shout_id))
                conn.commit()
            
            progress_channel = await self._get_progress_channel(guild)
            progress_msg = None
            if progress_channel:
                embed = self.create_progress_embed(shout_id, 0, total_targeted, message)
                progress_msg = await progress_channel.send(embed=embed)
            
            sent_count = 0
            failed_count = 0
            
            # Determine entity type for display
            entity_type = "🏬 Company" if is_company else "🏢 Party"
            
            for i, member in enumerate(members):
                try:
                    shout_message = (
                        f"📢 **{entity_type} SHOUT #{shout_id}**\n"
                        f"From: **{entity_name}** ({entity_id})\n"
                        f"Sent by: {sender.display_name}\n"
                        f"\n{message}\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 To opt out: `/shout_opt_out` (${self.OPT_OUT_COST:.2f})"
                    )
                    await member.send(shout_message)
                    sent_count += 1
                    
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO shout_messages (shout_id, user_id, status)
                            VALUES (?, ?, 'SENT')
                        """, (shout_id, member.id))
                        conn.commit()
                    
                except discord.Forbidden:
                    failed_count += 1
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO shout_messages (shout_id, user_id, status)
                            VALUES (?, ?, 'FAILED')
                        """, (shout_id, member.id))
                        conn.commit()
                    
                except Exception:
                    failed_count += 1
                
                if i % self.DM_RATE_LIMIT == 0 and i > 0:
                    await asyncio.sleep(1)
                
                if i % self.PROGRESS_UPDATE_INTERVAL == 0 and progress_msg:
                    embed = self.create_progress_embed(
                        shout_id, 
                        sent_count + failed_count, 
                        total_targeted, 
                        message,
                        sent_count,
                        failed_count
                    )
                    await progress_msg.edit(embed=embed)
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE shout_log 
                    SET total_sent = ?, total_failed = ?, status = 'COMPLETED', completed_at = ?
                    WHERE shout_id = ?
                """, (
                    sent_count,
                    failed_count,
                    datetime.now().isoformat(),
                    shout_id
                ))
                conn.commit()
            
            if progress_msg:
                embed = self.create_progress_embed(
                    shout_id,
                    total_targeted,
                    total_targeted,
                    message,
                    sent_count,
                    failed_count,
                    completed=True
                )
                await progress_msg.edit(embed=embed)
            
            self.active_shouts.pop(shout_id, None)
            
        except Exception as e:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE shout_log 
                    SET status = 'FAILED', completed_at = ?
                    WHERE shout_id = ?
                """, (datetime.now().isoformat(), shout_id))
                conn.commit()
            
            print(f"❌ Shout #{shout_id} failed: {e}")
            self.active_shouts.pop(shout_id, None)

    @app_commands.command(
        name="shout",
        description="📢 Propose a shout (personal funds, 5hr admin review)"
    )
    @app_commands.describe(
        message="The message you want to shout to everyone",
        include_bots="Include bot accounts in the shout (default: False)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=True)
    async def shout(
        self,
        interaction: discord.Interaction,
        message: str,
        include_bots: bool = False
    ):
        """Propose a shout with a 5-hour admin review window (personal funds)."""
        await interaction.response.defer(ephemeral=True)
        
        if len(message) > 2000:
            await interaction.followup.send(
                "❌ Message too long! Maximum 2000 characters.",
                ephemeral=True
            )
            return
        
        # Check if user is the superuser - bypass blacklist check
        is_superuser = interaction.user.id == self.SUPERUSER_ID
        if not is_superuser and await self.is_user_blacklisted(interaction.user.id):
            await interaction.followup.send(
                "❌ You have opted out of shouts. Use `/shout_opt_in` to rejoin.",
                ephemeral=True
            )
            return
        
        # Superuser bypasses cooldown
        if not is_superuser and await self.is_on_cooldown(interaction.user.id):
            remaining = await self.get_cooldown_remaining(interaction.user.id)
            await interaction.followup.send(
                f"⏳ You're on cooldown! Try again in **{remaining}**.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (interaction.user.id,))
            row = cursor.fetchone()
            balance = row["balance"] if row else 0.0
            
            if balance < self.SHOUT_COST:
                await interaction.followup.send(
                    SmartErrorMessages.insufficient_funds(
                        balance, 
                        self.SHOUT_COST, 
                        "shout"
                    ),
                    ephemeral=True
                )
                return
            
            # For superuser, bypass the strike window entirely - post immediately
            if is_superuser:
                status = 'RUNNING'
                strike_window_end = None
            else:
                status = 'PENDING_STRIKE'
                strike_window_end = (datetime.now() + timedelta(hours=self.STRIKE_WINDOW_HOURS)).isoformat()
            
            cursor.execute("""
                INSERT INTO shout_log (
                    user_id, guild_id, message, cost, status, 
                    total_targeted, total_sent, total_failed,
                    strike_window_end, include_bots, entity_id, entity_name, is_company_shout
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, NULL, NULL, 0)
            """, (
                interaction.user.id,
                interaction.guild_id,
                message,
                self.SHOUT_COST,
                status,
                strike_window_end,
                1 if include_bots else 0
            ))
            shout_id = cursor.lastrowid
            
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                (self.SHOUT_COST, interaction.user.id)
            )
            
            cursor.execute("""
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'shout_pending', ?, ?, ?)
            """, (
                interaction.user.id,
                -self.SHOUT_COST,
                f"Personal Shout #{shout_id} {'immediate' if is_superuser else 'pending strike review'}: {message[:50]}...",
                None
            ))
            
            # Only set cooldown for non-superusers
            if not is_superuser:
                cursor.execute("""
                    INSERT INTO shout_cooldowns (user_id, last_shout_at) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET last_shout_at = ?
                """, (
                    interaction.user.id,
                    datetime.now().isoformat(),
                    datetime.now().isoformat()
                ))
            conn.commit()
        
        audit_channel = await self._get_audit_channel(interaction.guild)
        
        if is_superuser:
            # Superuser shout - execute immediately
            await interaction.followup.send(
                f"🚀 **Superuser Shout #{shout_id} is being sent!**\n"
                f"📋 Message: {message[:100]}{'...' if len(message) > 100 else ''}",
                ephemeral=True
            )
            
            # Store in active shouts
            self.active_shouts[shout_id] = {
                "status": "RUNNING",
                "user_id": interaction.user.id,
                "message": message,
                "include_bots": include_bots,
                "guild": interaction.guild,
                "is_superuser": True
            }
            
            # Execute immediately
            await self._execute_shout(shout_id, interaction.guild)
            
            # Log in audit channel
            if audit_channel:
                embed = discord.Embed(
                    title=f"📢 **SUPERUSER SHOUT #{shout_id}**",
                    description=f"**Proposed by:** {interaction.user.mention}\n"
                               f"**Message:**\n```\n{message}\n```\n"
                               f"**Cost:** ${self.SHOUT_COST:.2f}\n"
                               f"**Includes Bots:** {'Yes' if include_bots else 'No'}\n\n"
                               f"⚡ **IMMEDIATE EXECUTION** (Superuser bypass)",
                    color=discord.Color.gold(),
                    timestamp=datetime.now()
                )
                await audit_channel.send(embed=embed)
        else:
            # Normal user - pending strike window
            if audit_channel:
                embed = discord.Embed(
                    title=f"📢 **PENDING SHOUT #{shout_id}**",
                    description=f"**Proposed by:** {interaction.user.mention}\n"
                               f"**Message:**\n```\n{message}\n```\n"
                               f"**Cost:** ${self.SHOUT_COST:.2f}\n"
                               f"**Includes Bots:** {'Yes' if include_bots else 'No'}\n\n"
                               f"⏳ **Strike Window:** {self.STRIKE_WINDOW_HOURS} hours\n"
                               f"**Expires:** <t:{int((datetime.now() + timedelta(hours=self.STRIKE_WINDOW_HOURS)).timestamp())}:R>",
                    color=discord.Color.yellow(),
                    timestamp=datetime.now()
                )
                embed.set_footer(text="Admins/Mods: Click Strike to cancel this shout and refund the user")
                
                view = StrikeView(shout_id, self.bot)
                strike_msg = await audit_channel.send(embed=embed, view=view)
                
                self.active_shouts[shout_id] = {
                    "status": "PENDING_STRIKE",
                    "user_id": interaction.user.id,
                    "message": message,
                    "include_bots": include_bots,
                    "guild": interaction.guild,
                    "audit_message_id": strike_msg.id,
                    "audit_channel_id": audit_channel.id
                }
                
                task = asyncio.create_task(
                    self._auto_post_timer(shout_id, interaction.guild)
                )
                self.strike_tasks[shout_id] = task
            else:
                # Fallback if no audit channel
                self.active_shouts[shout_id] = {
                    "status": "PENDING_STRIKE",
                    "user_id": interaction.user.id,
                    "message": message,
                    "include_bots": include_bots,
                    "guild": interaction.guild
                }
                task = asyncio.create_task(
                    self._auto_post_timer(shout_id, interaction.guild)
                )
                self.strike_tasks[shout_id] = task

            await interaction.followup.send(
                f"🚀 **Shout #{shout_id} submitted for review!**\n"
                f"⏳ Mods have **{self.STRIKE_WINDOW_HOURS} hours** to strike it down.\n"
                f"📋 Check the audit log for the pending shout.\n\n"
                f"*If not struck down, it will automatically post.*",
                ephemeral=True
            )

    @app_commands.command(
        name="shout_treasury",
        description="🏛️ Shout on behalf of a party or company using treasury funds"
    )
    @app_commands.describe(
        entity_id="Party or Company ID to shout as",
        message="The message you want to shout to everyone",
        include_bots="Include bot accounts in the shout (default: False)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def shout_treasury(
        self,
        interaction: discord.Interaction,
        entity_id: str,
        message: str,
        include_bots: bool = False
    ):
        """Shout on behalf of a party or company using treasury funds."""
        await interaction.response.defer(ephemeral=True)
        
        if len(message) > 2000:
            await interaction.followup.send(
                "❌ Message too long! Maximum 2000 characters.",
                ephemeral=True
            )
            return
        
        clean_id = entity_id.strip().lower()
        
        # Get all entities the user manages
        managed_entities = self._get_user_managed_parties(interaction.user.id)
        
        # Find the specific entity
        entity = None
        for e in managed_entities:
            if e["party_id"] == clean_id:
                entity = e
                break
        
        if not entity:
            # Check if entity exists but user doesn't manage it
            db_entity = self._get_entity_by_id(clean_id)
            if db_entity:
                await interaction.followup.send(
                    f"❌ You don't have permission to manage `{clean_id}`. You need the manager role for this entity.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"❌ Entity `{clean_id}` not found.",
                    ephemeral=True
                )
            return
        
        # Check if treasury has enough funds
        if entity["treasury"] < self.SHOUT_COST:
            entity_type = "Company" if entity["is_company"] else "Party"
            await interaction.followup.send(
                SmartErrorMessages.insufficient_treasury(
                    entity["treasury"],
                    self.SHOUT_COST,
                    entity["name"]
                ),
                ephemeral=True
            )
            return
        
        # Check if user is blacklisted (treasury shouts still respect blacklist for the sender)
        if await self.is_user_blacklisted(interaction.user.id):
            await interaction.followup.send(
                "❌ You have opted out of shouts. Use `/shout_opt_in` to rejoin.",
                ephemeral=True
            )
            return
        
        # Cooldown check (treasury shouts have a separate cooldown per entity)
        if await self.is_entity_on_cooldown(clean_id):
            remaining = await self.get_entity_cooldown_remaining(clean_id)
            await interaction.followup.send(
                f"⏳ This entity is on cooldown! Try again in **{remaining}**.",
                ephemeral=True
            )
            return
        
        # Deduct from treasury and create shout
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Deduct from treasury
            cursor.execute(
                "UPDATE parties SET treasury = treasury - ? WHERE party_id = ?",
                (self.SHOUT_COST, clean_id)
            )
            
            # Log the treasury deduction
            cursor.execute("""
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'treasury_shout', ?, ?, ?)
            """, (
                interaction.user.id,
                -self.SHOUT_COST,
                f"Treasury shout from {entity['name']} ({clean_id}): {message[:50]}...",
                clean_id
            ))
            
            # Get updated treasury after deduction
            cursor.execute(
                "SELECT treasury FROM parties WHERE party_id = ?",
                (clean_id,)
            )
            updated_row = cursor.fetchone()
            new_treasury = updated_row["treasury"] if updated_row else 0.0
            
            # Create shout log entry
            cursor.execute("""
                INSERT INTO shout_log (
                    user_id, guild_id, message, cost, status, 
                    total_targeted, total_sent, total_failed,
                    strike_window_end, include_bots, entity_id, entity_name, is_company_shout
                ) VALUES (?, ?, ?, ?, 'RUNNING', 0, 0, 0, NULL, ?, ?, ?, ?)
            """, (
                interaction.user.id,
                interaction.guild_id,
                message,
                self.SHOUT_COST,
                1 if include_bots else 0,
                clean_id,
                entity["name"],
                1 if entity["is_company"] else 0
            ))
            shout_id = cursor.lastrowid
            
            # Set entity cooldown
            cursor.execute("""
                INSERT INTO shout_entity_cooldowns (entity_id, last_shout_at) VALUES (?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET last_shout_at = ?
            """, (
                clean_id,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.commit()
        
        # Execute the shout immediately (no strike window for treasury shouts)
        entity_type = "Company" if entity["is_company"] else "Party"
        
        self.active_shouts[shout_id] = {
            "status": "RUNNING",
            "user_id": interaction.user.id,
            "message": message,
            "include_bots": include_bots,
            "guild": interaction.guild,
            "entity_id": clean_id,
            "entity_name": entity["name"],
            "is_company": entity["is_company"],
            "is_treasury_shout": True
        }
        
        await interaction.followup.send(
            f"🚀 **Treasury Shout #{shout_id} is being sent!**\n"
            f"🏛️ **{entity['name']}** ({entity_type}) spent **${self.SHOUT_COST:.2f}** from treasury\n"
            f"💰 Remaining treasury: **${new_treasury:.2f}**\n"
            f"📋 Message: {message[:100]}{'...' if len(message) > 100 else ''}",
            ephemeral=True
        )
        
        # Log in audit channel
        audit_channel = await self._get_audit_channel(interaction.guild)
        if audit_channel:
            embed = discord.Embed(
                title=f"🏛️ **TREASURY SHOUT #{shout_id}**",
                description=f"**Entity:** {entity['name']} (`{clean_id}`)\n"
                           f"**Entity Type:** {entity_type}\n"
                           f"**Sent by:** {interaction.user.mention}\n"
                           f"**Message:**\n```\n{message}\n```\n"
                           f"**Cost:** ${self.SHOUT_COST:.2f}\n"
                           f"**New Treasury:** ${new_treasury:.2f}\n"
                           f"**Includes Bots:** {'Yes' if include_bots else 'No'}",
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            embed.set_footer(text="Treasury shout - executed immediately")
            await audit_channel.send(embed=embed)
        
        # Execute the shout
        await self._send_shout_as_entity(
            interaction.guild,
            shout_id,
            message,
            include_bots,
            interaction.user,
            entity["name"],
            clean_id,
            entity["is_company"]
        )

    @app_commands.command(
        name="shout_manage_entities",
        description="📋 List all parties and companies you can shout for"
    )
    async def shout_manage_entities(self, interaction: discord.Interaction):
        """List all entities the user can shout on behalf of."""
        await interaction.response.defer(ephemeral=True)
        
        managed = self._get_user_managed_parties(interaction.user.id)
        
        if not managed:
            await interaction.followup.send(
                "❌ You don't manage any parties or companies.\n\n"
                "To shout on behalf of an entity, you need:\n"
                "• The manager role for that entity, or\n"
                "• Administrator permissions",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="🏛️ Entities You Can Shout For",
            description="Use `/shout_treasury entity_id:<id> message:<text>` to shout on behalf of an entity.",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        for e in managed:
            entity_type = "🏬 Company" if e["is_company"] else "🏢 Party"
            can_afford = e["treasury"] >= self.SHOUT_COST
            cost_status = "✅" if can_afford else "❌"
            
            # Check if entity is on cooldown
            on_cooldown = await self.is_entity_on_cooldown(e["party_id"])
            cooldown_status = "⏳" if on_cooldown else "✅"
            
            embed.add_field(
                name=f"{entity_type} {e['name']}",
                value=f"**ID:** `{e['party_id']}`\n"
                      f"**Treasury:** ${e['treasury']:.2f}\n"
                      f"**Can Afford:** {cost_status} (need ${self.SHOUT_COST:.2f})\n"
                      f"**Cooldown:** {cooldown_status} (24h)",
                inline=False
            )
        
        embed.set_footer(text=f"Total entities: {len(managed)}")
        await interaction.followup.send(embed=embed)

    async def _auto_post_timer(self, shout_id: int, guild: discord.Guild):
        """Timer that auto-posts the shout after strike window expires."""
        await asyncio.sleep(self.STRIKE_WINDOW_HOURS * 3600)
        
        if shout_id in self.active_shouts:
            shout_data = self.active_shouts.get(shout_id)
            if shout_data and shout_data["status"] == "PENDING_STRIKE":
                # Check if it's a treasury shout (these are executed immediately, so shouldn't happen)
                if shout_data.get("is_treasury_shout", False):
                    # This shouldn't happen, but just in case
                    self.active_shouts.pop(shout_id, None)
                    return
                await self._execute_shout(shout_id, guild)
        
        self.strike_tasks.pop(shout_id, None)

    async def _execute_shout(self, shout_id: int, guild: discord.Guild):
        """Execute the actual shout (called after strike window expires or manually)."""
        shout_data = self.active_shouts.get(shout_id)
        if not shout_data:
            return
        
        # Check if it's a treasury shout - these are handled differently
        if shout_data.get("is_treasury_shout", False):
            # This shouldn't happen since treasury shouts execute immediately
            self.active_shouts.pop(shout_id, None)
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE shout_log 
                SET status = 'RUNNING', 
                    total_targeted = ?,
                    completed_at = NULL
                WHERE shout_id = ?
            """, (len([m for m in guild.members]), shout_id))
            conn.commit()
        
        await self.process_shout(
            guild,
            shout_id,
            shout_data["message"],
            shout_data["include_bots"],
            guild.get_member(shout_data["user_id"]) or await self.bot.fetch_user(shout_data["user_id"])
        )
        
        self.active_shouts.pop(shout_id, None)

    async def _get_audit_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Get the audit log channel for the guild."""
        for channel in guild.channels:
            if channel.name == "audit-logs" and isinstance(channel, discord.TextChannel):
                return channel
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT audit_channel_id FROM guild_config WHERE guild_id = ?",
                (guild.id,)
            )
            row = cursor.fetchone()
            if row and row["audit_channel_id"]:
                channel = guild.get_channel(row["audit_channel_id"])
                if channel:
                    return channel
        
        try:
            category = None
            for cat in guild.categories:
                if "audit" in cat.name.lower():
                    category = cat
                    break
            
            if not category:
                category = await guild.create_category("🔒 Audit Logs")
            
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)
            }
            
            channel = await guild.create_text_channel(
                "audit-logs",
                category=category,
                overwrites=overwrites,
                reason="Created for shout strike system"
            )
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO guild_config (guild_id, audit_channel_id) VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET audit_channel_id = ?
                """, (guild.id, channel.id, channel.id))
                conn.commit()
            
            return channel
        except Exception as e:
            print(f"❌ Could not create audit channel: {e}")
            return None

    async def process_shout(
        self,
        guild: discord.Guild,
        shout_id: int,
        message: str,
        include_bots: bool,
        sender: discord.User
    ):
        """Process the shout."""
        try:
            # Check if it's a treasury shout
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT entity_id, entity_name, is_company_shout FROM shout_log WHERE shout_id = ?
                """, (shout_id,))
                shout_info = cursor.fetchone()
            
            is_treasury = shout_info and shout_info["entity_id"] is not None
            
            members = [m for m in guild.members if include_bots or not m.bot]
            blacklisted = await self.get_blacklisted_users()
            members = [m for m in members if m.id not in blacklisted]
            
            total_targeted = len(members)
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE shout_log 
                    SET total_targeted = ?, status = 'SENDING'
                    WHERE shout_id = ?
                """, (total_targeted, shout_id))
                conn.commit()
            
            progress_channel = await self._get_progress_channel(guild)
            progress_msg = None
            if progress_channel:
                embed = self.create_progress_embed(shout_id, 0, total_targeted, message)
                progress_msg = await progress_channel.send(embed=embed)
            
            sent_count = 0
            failed_count = 0
            
            # Determine shout type for display
            if is_treasury:
                entity_type = "🏬 Company" if shout_info["is_company_shout"] else "🏢 Party"
                from_line = f"From: **{shout_info['entity_name']}** ({shout_info['entity_id']})\nSent by: {sender.display_name}"
            else:
                entity_type = "📢"
                from_line = f"From: {sender.display_name}"
            
            for i, member in enumerate(members):
                try:
                    shout_message = (
                        f"{entity_type} **SHOUT #{shout_id}**\n"
                        f"{from_line}\n"
                        f"\n{message}\n\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔄 To opt out: `/shout_opt_out` (${self.OPT_OUT_COST:.2f})"
                    )
                    await member.send(shout_message)
                    sent_count += 1
                    
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO shout_messages (shout_id, user_id, status)
                            VALUES (?, ?, 'SENT')
                        """, (shout_id, member.id))
                        conn.commit()
                    
                except discord.Forbidden:
                    failed_count += 1
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO shout_messages (shout_id, user_id, status)
                            VALUES (?, ?, 'FAILED')
                        """, (shout_id, member.id))
                        conn.commit()
                    
                except Exception:
                    failed_count += 1
                
                if i % self.DM_RATE_LIMIT == 0 and i > 0:
                    await asyncio.sleep(1)
                
                if i % self.PROGRESS_UPDATE_INTERVAL == 0 and progress_msg:
                    embed = self.create_progress_embed(
                        shout_id, 
                        sent_count + failed_count, 
                        total_targeted, 
                        message,
                        sent_count,
                        failed_count
                    )
                    await progress_msg.edit(embed=embed)
            
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE shout_log 
                    SET total_sent = ?, total_failed = ?, status = 'COMPLETED', completed_at = ?
                    WHERE shout_id = ?
                """, (
                    sent_count,
                    failed_count,
                    datetime.now().isoformat(),
                    shout_id
                ))
                conn.commit()
            
            if progress_msg:
                embed = self.create_progress_embed(
                    shout_id,
                    total_targeted,
                    total_targeted,
                    message,
                    sent_count,
                    failed_count,
                    completed=True
                )
                await progress_msg.edit(embed=embed)
            
            self.active_shouts.pop(shout_id, None)
            
        except Exception as e:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE shout_log 
                    SET status = 'FAILED', completed_at = ?
                    WHERE shout_id = ?
                """, (datetime.now().isoformat(), shout_id))
                conn.commit()
            
            print(f"❌ Shout #{shout_id} failed: {e}")
            self.active_shouts.pop(shout_id, None)

    async def _get_progress_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Get a channel to show progress in."""
        for channel in guild.channels:
            if channel.name in ["shout-progress", "shout-status"] and isinstance(channel, discord.TextChannel):
                return channel
        
        for channel in guild.channels:
            if channel.name in ["general", "chat", "main"] and isinstance(channel, discord.TextChannel):
                if channel.permissions_for(guild.me).send_messages:
                    return channel
        
        for channel in guild.channels:
            if isinstance(channel, discord.TextChannel) and channel.permissions_for(guild.me).send_messages:
                return channel
        
        return None

    def create_progress_embed(self, shout_id: int, current: int, total: int, message: str, 
                             sent: int = 0, failed: int = 0, completed: bool = False):
        """Create a progress embed for the shout."""
        progress = (current / total * 100) if total > 0 else 0
        
        color = discord.Color.green() if completed else discord.Color.blue()
        title = "✅ Shout Completed!" if completed else f"📢 Shout #{shout_id} in Progress"
        
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=datetime.now()
        )
        
        bar_length = 20
        filled = int(bar_length * progress / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        embed.add_field(
            name="Progress",
            value=f"`{bar}` {progress:.1f}% ({current:,}/{total:,} members)",
            inline=False
        )
        
        if sent > 0 or failed > 0:
            embed.add_field(
                name="Results",
                value=f"✅ Sent: {sent:,}\n❌ Failed: {failed:,}",
                inline=True
            )
        
        embed.add_field(
            name="Message Preview",
            value=f"```\n{message[:200]}{'...' if len(message) > 200 else ''}\n```",
            inline=False
        )
        
        if not completed:
            embed.set_footer(text="⏳ Shout in progress... This may take a few minutes")
        
        return embed

    async def strike_shout(self, shout_id: int, moderator: discord.Member, reason: str = None):
        """Strike down a pending shout and refund the user."""
        shout_data = self.active_shouts.get(shout_id)
        if not shout_data:
            return False, "Shout not found or already processed."
        
        # Superuser shouts cannot be struck
        if shout_data.get("is_superuser", False):
            return False, "❌ Superuser shouts cannot be struck down."
        
        # Treasury shouts cannot be struck (they execute immediately)
        if shout_data.get("is_treasury_shout", False):
            return False, "❌ Treasury shouts cannot be struck down (they execute immediately)."
        
        if shout_data["status"] != "PENDING_STRIKE":
            return False, f"Shout is already {shout_data['status']}."
        
        if shout_id in self.strike_tasks:
            self.strike_tasks[shout_id].cancel()
            self.strike_tasks.pop(shout_id, None)
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE shout_log 
                SET status = 'STRUCK', 
                    completed_at = ?,
                    strike_reason = ?,
                    struck_by = ?
                WHERE shout_id = ?
            """, (
                datetime.now().isoformat(),
                reason or "No reason provided",
                moderator.id,
                shout_id
            ))
            
            cursor.execute("""
                UPDATE users SET balance = balance + ? WHERE user_id = ?
            """, (self.SHOUT_COST, shout_data["user_id"]))
            
            cursor.execute("""
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'shout_refund', ?, ?, ?)
            """, (
                shout_data["user_id"],
                self.SHOUT_COST,
                f"Shout #{shout_id} refunded (struck by {moderator.display_name})",
                None
            ))
            
            cursor.execute("""
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'shout_strike', 0, ?, ?)
            """, (
                moderator.id,
                f"Struck shout #{shout_id}: {reason or 'No reason'}",
                None
            ))
            conn.commit()
        
        shout_data["status"] = "STRUCK"
        self.active_shouts[shout_id] = shout_data
        
        try:
            audit_channel = self.bot.get_channel(shout_data["audit_channel_id"])
            if audit_channel:
                try:
                    msg = await audit_channel.fetch_message(shout_data["audit_message_id"])
                    embed = msg.embeds[0] if msg.embeds else None
                    if embed:
                        embed.color = discord.Color.red()
                        embed.title = f"❌ SHOUT #{shout_id} STRUCK DOWN"
                        embed.description = (
                            f"**Proposed by:** <@{shout_data['user_id']}>\n"
                            f"**Struck by:** {moderator.mention}\n"
                            f"**Reason:** {reason or 'No reason provided'}\n\n"
                            f"💰 **${self.SHOUT_COST:.2f} has been refunded to the user.**"
                        )
                        embed.set_footer(text="Shout was struck down by a moderator")
                        
                        view = StrikeView(shout_id, self.bot, disabled=True)
                        await msg.edit(embed=embed, view=view)
                except:
                    pass
        except:
            pass
        
        try:
            user = await self.bot.fetch_user(shout_data["user_id"])
            await user.send(
                f"❌ **Your shout #{shout_id} was struck down!**\n"
                f"**Reason:** {reason or 'No reason provided'}\n"
                f"💰 **${self.SHOUT_COST:.2f} has been refunded to your balance.**\n\n"
                f"*You can try again with a different message.*"
            )
        except:
            pass
        
        return True, f"Shout #{shout_id} struck down. User refunded ${self.SHOUT_COST:.2f}."

    @app_commands.command(
        name="shout_status",
        description="📊 Check the status of a shout"
    )
    @app_commands.describe(
        shout_id="The ID of the shout to check"
    )
    async def shout_status(
        self,
        interaction: discord.Interaction,
        shout_id: int
    ):
        """Check the status of a shout."""
        await interaction.response.defer(ephemeral=True)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM shout_log 
                WHERE shout_id = ?
            """, (shout_id,))
            shout = cursor.fetchone()
            
            if not shout:
                await interaction.followup.send(
                    f"❌ Shout #{shout_id} not found.",
                    ephemeral=True
                )
                return
            
            is_owner = shout["user_id"] == interaction.user.id
            is_admin = interaction.user.guild_permissions.administrator
            is_superuser = interaction.user.id == self.SUPERUSER_ID
            
            # Check if user manages the entity (if it's a treasury shout)
            is_entity_manager = False
            if shout["entity_id"]:
                managed = self._get_user_managed_parties(interaction.user.id)
                is_entity_manager = any(e["party_id"] == shout["entity_id"] for e in managed)
            
            if not (is_owner or is_admin or is_superuser or is_entity_manager):
                await interaction.followup.send(
                    "❌ You don't have permission to view this shout's status.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"📊 Shout #{shout_id} Status",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            status_emoji = {
                "PENDING_STRIKE": "⏳",
                "SENDING": "🔄",
                "RUNNING": "🔄",
                "COMPLETED": "✅",
                "FAILED": "❌",
                "STRUCK": "🚫"
            }.get(shout["status"], "❓")
            
            embed.add_field(
                name="Status",
                value=f"{status_emoji} {shout['status']}",
                inline=True
            )
            
            # Show sender info
            if shout["entity_id"]:
                entity_type = "Company" if shout["is_company_shout"] else "Party"
                embed.add_field(
                    name="Sender",
                    value=f"{shout['entity_name']} ({entity_type}) via <@{shout['user_id']}>",
                    inline=True
                )
            else:
                embed.add_field(
                    name="Sender",
                    value=f"<@{shout['user_id']}>",
                    inline=True
                )
            
            embed.add_field(
                name="Cost",
                value=f"${shout['cost']:.2f}",
                inline=True
            )
            
            if shout["entity_id"]:
                embed.add_field(
                    name="Entity ID",
                    value=f"`{shout['entity_id']}`",
                    inline=True
                )
            
            # Show superuser flag if applicable
            if interaction.user.id == self.SUPERUSER_ID and shout["user_id"] == self.SUPERUSER_ID:
                embed.add_field(
                    name="⚡ Superuser",
                    value="This shout bypassed all restrictions",
                    inline=True
                )
            
            if shout["strike_reason"]:
                embed.add_field(
                    name="Strike Reason",
                    value=shout["strike_reason"],
                    inline=False
                )
            
            if shout["struck_by"]:
                embed.add_field(
                    name="Struck By",
                    value=f"<@{shout['struck_by']}>",
                    inline=True
                )
            
            if shout["total_targeted"] > 0:
                embed.add_field(
                    name="Targeted",
                    value=f"{shout['total_targeted']:,} members",
                    inline=True
                )
            
            if shout["total_sent"] > 0:
                embed.add_field(
                    name="Sent",
                    value=f"{shout['total_sent']:,} messages",
                    inline=True
                )
            
            if shout["total_failed"] > 0:
                embed.add_field(
                    name="Failed",
                    value=f"{shout['total_failed']:,} members",
                    inline=True
                )
            
            embed.add_field(
                name="Message",
                value=f"```\n{shout['message'][:500]}{'...' if len(shout['message']) > 500 else ''}\n```",
                inline=False
            )
            
            if shout["created_at"]:
                created = datetime.fromisoformat(shout["created_at"])
                embed.add_field(
                    name="Created",
                    value=f"<t:{int(created.timestamp())}:R>",
                    inline=True
                )
            
            if shout["completed_at"]:
                completed = datetime.fromisoformat(shout["completed_at"])
                embed.add_field(
                    name="Completed",
                    value=f"<t:{int(completed.timestamp())}:R>",
                    inline=True
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="shout_opt_out",
        description="🚫 Opt out of receiving future shouts (costs $20 to rejoin later)"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=True)
    async def shout_opt_out(self, interaction: discord.Interaction):
        """Opt out of receiving future shouts."""
        await interaction.response.defer(ephemeral=True)
        
        # Superuser cannot opt out (or rather, it doesn't matter since they bypass)
        if interaction.user.id == self.SUPERUSER_ID:
            await interaction.followup.send(
                "⚡ As a superuser, you bypass shout restrictions. You cannot opt out.",
                ephemeral=True
            )
            return
        
        if await self.is_user_blacklisted(interaction.user.id):
            await interaction.followup.send(
                "ℹ️ You're already opted out of shouts.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT balance FROM users WHERE user_id = ?", (interaction.user.id,))
            row = cursor.fetchone()
            balance = row["balance"] if row else 0.0
            
            if balance < self.OPT_OUT_COST:
                await interaction.followup.send(
                    SmartErrorMessages.insufficient_funds(
                        balance,
                        self.OPT_OUT_COST,
                        "shout_opt_out"
                    ),
                    ephemeral=True
                )
                return
            
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                (self.OPT_OUT_COST, interaction.user.id)
            )
            
            cursor.execute("""
                INSERT INTO shout_blacklist (user_id, refund_amount)
                VALUES (?, ?)
            """, (
                interaction.user.id,
                self.OPT_OUT_COST
            ))
            
            cursor.execute("""
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'shout_opt_out', ?, ?, ?)
            """, (
                interaction.user.id,
                -self.OPT_OUT_COST,
                "Opted out of shout system",
                None
            ))
            conn.commit()
        
        await interaction.followup.send(
            f"✅ You've opted out of receiving future shouts. You paid **${self.OPT_OUT_COST:.2f}**.\n"
            f"To rejoin, use `/shout_opt_in` (this will **not** refund your $20).",
            ephemeral=True
        )

    @app_commands.command(
        name="shout_opt_in",
        description="✅ Opt back in to receive shouts (no refund)"
    )
    async def shout_opt_in(self, interaction: discord.Interaction):
        """Opt back in to receive shouts."""
        await interaction.response.defer(ephemeral=True)
        
        if interaction.user.id == self.SUPERUSER_ID:
            await interaction.followup.send(
                "⚡ As a superuser, you bypass shout restrictions.",
                ephemeral=True
            )
            return
        
        if not await self.is_user_blacklisted(interaction.user.id):
            await interaction.followup.send(
                "ℹ️ You're not opted out of shouts.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM shout_blacklist WHERE user_id = ?",
                (interaction.user.id,)
            )
            conn.commit()
        
        await interaction.followup.send(
            "✅ You've opted back in to receive shouts!",
            ephemeral=True
        )

    @app_commands.command(
        name="shout_delete",
        description="🗑️ Delete all messages sent by a shout (Admin only)"
    )
    @app_commands.describe(
        shout_id="The ID of the shout to delete"
    )
    @app_commands.default_permissions(administrator=True)
    @safety_wrapper("admin")
    async def shout_delete(
        self,
        interaction: discord.Interaction,
        shout_id: int
    ):
        """Delete all messages sent by a shout (Admin only)."""
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user.guild_permissions.administrator and interaction.user.id != self.SUPERUSER_ID:
            await interaction.followup.send(
                "❌ Only administrators or superusers can delete shout messages.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM shout_log WHERE shout_id = ?
            """, (shout_id,))
            shout = cursor.fetchone()
            
            if not shout:
                await interaction.followup.send(
                    f"❌ Shout #{shout_id} not found.",
                    ephemeral=True
                )
                return
            
            cursor.execute("""
                SELECT message_id, user_id FROM shout_messages 
                WHERE shout_id = ? AND status = 'SENT'
            """, (shout_id,))
            messages = cursor.fetchall()
        
        if not messages:
            await interaction.followup.send(
                f"ℹ️ Shout #{shout_id} has no sent messages to delete.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE shout_messages 
                SET status = 'DELETED' 
                WHERE shout_id = ?
            """, (shout_id,))
            conn.commit()
        
        deleted_count = len(messages)
        
        await interaction.followup.send(
            f"✅ Marked **{deleted_count:,}** messages from Shout #{shout_id} as deleted.\n"
            f"⚠️ Note: Discord doesn't allow bot deletion of DMs, but all messages have been flagged as deleted in the system.",
            ephemeral=True
        )
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'shout_delete', 0, ?, ?)
            """, (
                interaction.user.id,
                f"Deleted shout #{shout_id} messages ({deleted_count} messages)",
                None
            ))
            conn.commit()

    @app_commands.command(
        name="shout_history",
        description="📜 View your shout history"
    )
    @app_commands.describe(
        user="View a specific user's shout history (Admin only)"
    )
    @safety_wrapper("default")
    async def shout_history(
        self,
        interaction: discord.Interaction,
        user: discord.User = None
    ):
        """View shout history."""
        await interaction.response.defer(ephemeral=True)
        
        target_user = user or interaction.user
        is_admin = interaction.user.guild_permissions.administrator
        is_superuser = interaction.user.id == self.SUPERUSER_ID
        
        if user and not (is_admin or is_superuser):
            await interaction.followup.send(
                "❌ Only administrators or superusers can view other users' shout history.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM shout_log 
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 10
            """, (target_user.id,))
            shouts = cursor.fetchall()
            
            if not shouts:
                await interaction.followup.send(
                    f"📭 {target_user.mention} has no shout history.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title=f"📜 Shout History for {target_user.display_name}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            for i, shout in enumerate(shouts):
                status_emoji = {
                    "PENDING_STRIKE": "⏳",
                    "SENDING": "🔄",
                    "RUNNING": "🔄",
                    "COMPLETED": "✅",
                    "FAILED": "❌",
                    "STRUCK": "🚫"
                }.get(shout["status"], "❓")
                
                if shout["total_targeted"] > 0:
                    success_rate = (shout["total_sent"] / shout["total_targeted"] * 100)
                    results = f"Sent: {shout['total_sent']:,}/{shout['total_targeted']:,} ({success_rate:.1f}%)"
                else:
                    results = "No targets"
                
                created = datetime.fromisoformat(shout["created_at"])
                
                # Show entity info if treasury shout
                entity_info = ""
                if shout["entity_id"]:
                    entity_type = "Company" if shout["is_company_shout"] else "Party"
                    entity_info = f"\nEntity: **{shout['entity_name']}** ({entity_type})"
                
                embed.add_field(
                    name=f"#{shout['shout_id']} - {status_emoji} {shout['status']}",
                    value=(
                        f"Message: ```\n{shout['message'][:100]}{'...' if len(shout['message']) > 100 else ''}\n```"
                        f"Results: {results}\n"
                        f"Cost: ${shout['cost']:.2f}\n"
                        f"Time: <t:{int(created.timestamp())}:R>"
                        + entity_info
                        + (f"\nStruck by: <@{shout['struck_by']}>" if shout['struck_by'] else "")
                        + (f"\nReason: {shout['strike_reason']}" if shout['strike_reason'] else "")
                    ),
                    inline=False
                )
            
            embed.set_footer(text=f"Showing last {len(shouts)} shouts")
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ─── ENTITY COOLDOWN HELPERS ──────────────────────────────────────────────

    async def is_entity_on_cooldown(self, entity_id: str) -> bool:
        """Check if an entity is on cooldown for treasury shouts."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT last_shout_at FROM shout_entity_cooldowns 
                WHERE entity_id = ?
            """, (entity_id,))
            row = cursor.fetchone()
            
            if not row:
                return False
            
            last_shout = datetime.fromisoformat(row["last_shout_at"])
            cooldown_end = last_shout + timedelta(hours=self.COOLDOWN_HOURS)
            return datetime.now() < cooldown_end

    async def get_entity_cooldown_remaining(self, entity_id: str) -> str:
        """Get time remaining for an entity's cooldown."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT last_shout_at FROM shout_entity_cooldowns 
                WHERE entity_id = ?
            """, (entity_id,))
            row = cursor.fetchone()
            
            if not row:
                return "No cooldown"
            
            last_shout = datetime.fromisoformat(row["last_shout_at"])
            cooldown_end = last_shout + timedelta(hours=self.COOLDOWN_HOURS)
            remaining = cooldown_end - datetime.now()
            
            if remaining.total_seconds() <= 0:
                return "Cooldown expired"
            
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            
            if hours > 0:
                return f"{hours}h {minutes}m"
            return f"{minutes}m"

    # ─── USER HELPER METHODS ──────────────────────────────────────────────────

    async def is_user_blacklisted(self, user_id: int) -> bool:
        """Check if a user is blacklisted from shouts."""
        # Superuser is never blacklisted
        if user_id == self.SUPERUSER_ID:
            return False
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id FROM shout_blacklist WHERE user_id = ?",
                (user_id,)
            )
            return cursor.fetchone() is not None

    async def get_blacklisted_users(self) -> List[int]:
        """Get all blacklisted users."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM shout_blacklist")
            return [row["user_id"] for row in cursor.fetchall()]

    async def is_on_cooldown(self, user_id: int) -> bool:
        """Check if a user is on cooldown for personal shouts."""
        # Superuser has no cooldown
        if user_id == self.SUPERUSER_ID:
            return False
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT last_shout_at FROM shout_cooldowns 
                WHERE user_id = ?
            """, (user_id,))
            row = cursor.fetchone()
            
            if not row:
                return False
            
            last_shout = datetime.fromisoformat(row["last_shout_at"])
            cooldown_end = last_shout + timedelta(hours=self.COOLDOWN_HOURS)
            return datetime.now() < cooldown_end

    async def get_cooldown_remaining(self, user_id: int) -> str:
        """Get time remaining for a user's cooldown."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT last_shout_at FROM shout_cooldowns 
                WHERE user_id = ?
            """, (user_id,))
            row = cursor.fetchone()
            
            if not row:
                return "No cooldown"
            
            last_shout = datetime.fromisoformat(row["last_shout_at"])
            cooldown_end = last_shout + timedelta(hours=self.COOLDOWN_HOURS)
            remaining = cooldown_end - datetime.now()
            
            if remaining.total_seconds() <= 0:
                return "Cooldown expired"
            
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            
            if hours > 0:
                return f"{hours}h {minutes}m"
            return f"{minutes}m"


# ─── STRIKE VIEW (Button) ──────────────────────────────────────────────────────

class StrikeView(discord.ui.View):
    """View with a strike button for moderators."""

    def __init__(self, shout_id: int, bot, disabled: bool = False):
        super().__init__(timeout=None)
        self.shout_id = shout_id
        self.bot = bot
        
        if disabled:
            self.strike_button.disabled = True

    @discord.ui.button(
        label="🚫 STRIKE SHOUT",
        style=discord.ButtonStyle.danger,
        custom_id="strike_shout_button",
        row=0
    )
    async def strike_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Strike down the shout."""
        
        # Check if superuser - cannot strike superuser shouts
        shout_cog = self.bot.get_cog("ShoutCog")
        if shout_cog:
            shout_data = shout_cog.active_shouts.get(self.shout_id)
            if shout_data and shout_data.get("is_superuser", False):
                await interaction.response.send_message(
                    "❌ Superuser shouts cannot be struck down!",
                    ephemeral=True
                )
                return
            if shout_data and shout_data.get("is_treasury_shout", False):
                await interaction.response.send_message(
                    "❌ Treasury shouts cannot be struck down! They execute immediately.",
                    ephemeral=True
                )
                return
        
        has_mod_perms = (
            interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.manage_messages or
            interaction.user.guild_permissions.manage_channels or
            interaction.user.guild_permissions.manage_roles or
            interaction.user.guild_permissions.kick_members or
            interaction.user.guild_permissions.ban_members or
            interaction.user.guild_permissions.manage_nicknames or
            interaction.user.guild_permissions.manage_webhooks or
            interaction.channel.permissions_for(interaction.user).manage_messages
        )
        
        if not has_mod_perms:
            await interaction.response.send_message(
                "❌ You need moderation permissions to strike shouts! (Manage Messages, Admin, etc.)",
                ephemeral=True
            )
            return
        
        modal = StrikeReasonModal(self.shout_id, self.bot)
        await interaction.response.send_modal(modal)


class StrikeReasonModal(discord.ui.Modal, title="Strike Shout"):
    """Modal for providing a strike reason."""

    def __init__(self, shout_id: int, bot):
        super().__init__(timeout=120)
        self.shout_id = shout_id
        self.bot = bot

    reason = discord.ui.TextInput(
        label="Reason for striking this shout",
        placeholder="Why is this shout being struck down?",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        shout_cog = self.bot.get_cog("ShoutCog")
        if not shout_cog:
            await interaction.followup.send(
                "❌ Shout system not available.",
                ephemeral=True
            )
            return
        
        success, message = await shout_cog.strike_shout(
            self.shout_id,
            interaction.user,
            self.reason.value or None
        )
        
        if success:
            await interaction.followup.send(
                f"✅ {message}",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ {message}",
                ephemeral=True
            )


# ─── SETUP FUNCTION ────────────────────────────────────────────────────────────

async def setup(bot):
    """Required setup function for the cog."""
    await bot.add_cog(ShoutCog(bot))