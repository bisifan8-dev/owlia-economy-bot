import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db, get_user_managed_parties
from views import TreasuryPartySelect
from safety import safety_wrapper, financial_safety, InputValidator


class EconomyCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def conclude_bidding_war(self, guild_id: int):
        bid_state = self.bot.ACTIVE_BIDS.get(guild_id)
        if not bid_state:
            return

        winning_party_id = bid_state["party_id"]
        winning_bid = bid_state["amount"]
        guild = self.bot.get_guild(guild_id)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM parties WHERE party_id = ?", (winning_party_id,)
            )
            party = cursor.fetchone()
            cursor.execute(
                "SELECT paid_channel_id, bids_channel_id FROM guild_config WHERE guild_id = ?",
                (guild_id,),
            )
            cfg = cursor.fetchone()

        if party and cfg and cfg["paid_channel_id"]:
            ads_channel = guild.get_channel(cfg["paid_channel_id"])
            bids_channel = guild.get_channel(cfg["bids_channel_id"])
            party_role = (
                guild.get_role(int(party["role_id"])) if party["role_id"] else None
            )

            if bids_channel:
                await bids_channel.send(
                    f"🏆 **BIDDING WAR CONCLUDED!**\n**{party['name']}** won the bid with **${winning_bid:.2f}**! Advertising channel locked for 5 minutes."
                )

            if ads_channel and party_role:
                await ads_channel.set_permissions(
                    guild.default_role, send_messages=False, view_channel=True
                )
                await ads_channel.set_permissions(
                    party_role, send_messages=True, view_channel=True
                )
                await ads_channel.send(
                    f"🔒 Channel reserved for **{party['name']}** ({party_role.mention}) for the next 5 minutes!"
                )

                async def reset_channel():
                    await asyncio.sleep(300)
                    await ads_channel.set_permissions(
                        party_role, overwrite=None
                    )
                    await ads_channel.set_permissions(
                        guild.default_role, send_messages=False, view_channel=True
                    )
                    await ads_channel.send(
                        "🔓 Paid channel reservation has expired."
                    )

                self.bot.loop.create_task(reset_channel())

        self.bot.ACTIVE_BIDS.pop(guild_id, None)

    @app_commands.command(
        name="pay",
        description="💸 Send money from your balance to another user."
    )
    @app_commands.describe(
        target="The user you want to pay",
        amount="Amount to send"
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=True)
    async def pay(
        self,
        interaction: discord.Interaction,
        target: discord.User,
        amount: float
    ):
        await interaction.response.defer(ephemeral=True)

        # Safety: Validate amount
        valid, msg = InputValidator.validate_amount(amount, allow_zero=False)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return

        if amount <= 0:
            await interaction.followup.send("❌ Amount must be greater than zero.")
            return

        if target.id == interaction.user.id:
            await interaction.followup.send("❌ You cannot send money to yourself.")
            return

        if target.bot:
            await interaction.followup.send("❌ You cannot send money to bots.")
            return

        with get_db() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (interaction.user.id,)
            )
            sender_row = cursor.fetchone()
            sender_bal = sender_row["balance"] if sender_row else 0.0

            if sender_bal < amount:
                await interaction.followup.send(
                    f"❌ Insufficient funds! You have **${sender_bal:.2f}**, but tried to send **${amount:.2f}**."
                )
                return

            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                (amount, interaction.user.id)
            )

            cursor.execute(
                """
                INSERT INTO users (user_id, balance, message_count, premium_credits) VALUES (?, ?, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
            """,
                (target.id, amount, amount)
            )
            conn.commit()

        await interaction.followup.send(
            f"✅ Successfully sent **${amount:.2f}** to {target.mention}!"
        )

        try:
            await target.send(
                f"💸 **{interaction.user.display_name}** sent you **${amount:.2f}**!"
            )
        except discord.Forbidden:
            pass

    @app_commands.command(
        name="messages",
        description="📊 Check your total message count and party contribution stats."
    )
    @safety_wrapper("default")
    async def messages(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        with get_db() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT message_count FROM users WHERE user_id = ?",
                (interaction.user.id,)
            )
            user_row = cursor.fetchone()
            msg_count = user_row["message_count"] if user_row else 0

            cursor.execute(
                """
                SELECT p.name, p.party_id, s.messages_sent 
                FROM party_member_stats s
                JOIN parties p ON s.party_id = p.party_id
                WHERE s.user_id = ?
                """,
                (interaction.user.id,)
            )
            party_stats = cursor.fetchall()

        embed = discord.Embed(
            title=f"📊 Message Stats for {interaction.user.display_name}",
            color=discord.Color.blue()
        )

        remaining = max(0, 50 - msg_count)
        embed.add_field(
            name="💵 Income Progress",
            value=f"**{msg_count}/50** messages sent\n*(**{remaining}** more until next $1.00 payout)*",
            inline=False
        )

        if party_stats:
            party_desc = ""
            for p in party_stats:
                party_desc += f"• **{p['name']}** (`{p['party_id']}`): `{p['messages_sent']}` messages\n"
            embed.add_field(
                name="🏛️ Party Contributions",
                value=party_desc,
                inline=False
            )
        else:
            embed.add_field(
                name="🏛️ Party Contributions",
                value="*No party message contributions recorded yet.*",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="treasury", description="🏛️ View and manage party/company Treasury funds."
    )
    @safety_wrapper("default")
    async def treasury(self, interaction: discord.Interaction):
        managed = get_user_managed_parties(interaction.user)
        if not managed:
            await interaction.response.send_message(
                "❌ You are not a manager or admin of any entity.", ephemeral=True
            )
            return

        if len(managed) == 1:
            p = managed[0]
            embed = discord.Embed(
                title=f"🏛️ {p['name']} Treasury",
                description=f"Current Balance: **${p['treasury']:.2f}**\nUse `/treasury_spend` to transfer funds.",
                color=discord.Color.gold(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            view = discord.ui.View(timeout=300)
            view.add_item(TreasuryPartySelect(managed))
            await interaction.response.send_message(
                "You manage multiple entities. Select one:",
                view=view,
                ephemeral=True,
            )

    @app_commands.command(
        name="treasury_spend",
        description="💸 Spend/transfer money from entity Treasury.",
    )
    @app_commands.describe(
        party_id="Target entity ID",
        target="Recipient user",
        amount="Amount to send",
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def treasury_spend(
        self,
        interaction: discord.Interaction,
        party_id: str,
        target: discord.User,
        amount: float,
    ):
        await interaction.response.defer(ephemeral=True)
        
        # Safety: Validate party_id
        valid, msg = InputValidator.validate_party_id(party_id)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        # Safety: Validate amount
        valid, msg = InputValidator.validate_amount(amount, allow_zero=False)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        if amount <= 0:
            await interaction.followup.send("❌ Amount must be greater than zero.")
            return

        clean_party_id = party_id.strip().lower()
        managed = get_user_managed_parties(interaction.user)
        party = next(
            (p for p in managed if p["party_id"] == clean_party_id), None
        )

        if not party:
            await interaction.followup.send(
                f"❌ You do not manage entity `{clean_party_id}`."
            )
            return

        if party["treasury"] < amount:
            await interaction.followup.send(
                f"❌ Insufficient treasury funds. Treasury has **${party['treasury']:.2f}**."
            )
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE parties SET treasury = treasury - ? WHERE party_id = ?",
                (amount, clean_party_id),
            )
            cursor.execute(
                """
                INSERT INTO users (user_id, balance, message_count, premium_credits) VALUES (?, ?, 0, 0)
                ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?
            """,
                (target.id, amount, amount),
            )
            conn.commit()

        await interaction.followup.send(
            f"✅ Transferred **${amount:.2f}** from **{party['name']}** Treasury to {target.mention}."
        )

    @app_commands.command(
        name="party_bid",
        description="⚔️ Bid from Treasury to reserve the paid channel for 5 minutes.",
    )
    @app_commands.describe(
        party_id="Your party ID",
        bid_amount="Bid amount (Leave 0 for default starting bid)",
    )
    @safety_wrapper("financial")
    @financial_safety(required_balance=False)
    async def party_bid(
        self,
        interaction: discord.Interaction,
        party_id: str,
        bid_amount: float = 0.0,
    ):
        await interaction.response.defer(ephemeral=True)
        
        # Safety: Validate party_id
        valid, msg = InputValidator.validate_party_id(party_id)
        if not valid:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return
        
        # Safety: Validate bid amount
        if bid_amount > 0:
            valid, msg = InputValidator.validate_amount(bid_amount, allow_zero=False)
            if not valid:
                await interaction.followup.send(f"❌ {msg}", ephemeral=True)
                return
        
        guild_id = interaction.guild_id
        clean_party_id = party_id.strip().lower()

        managed = get_user_managed_parties(interaction.user)
        party = next(
            (p for p in managed if p["party_id"] == clean_party_id), None
        )

        if not party:
            await interaction.followup.send(
                f"❌ You do not manage party `{clean_party_id}`."
            )
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(treasury) as max_val FROM parties")
            max_treasury = cursor.fetchone()["max_val"] or 50.0
            cursor.execute(
                "SELECT bids_channel_id FROM guild_config WHERE guild_id = ?",
                (guild_id,),
            )
            bids_channel_id = cursor.fetchone()["bids_channel_id"]

        min_start_bid = max_treasury / 6.0
        current_bid = self.bot.ACTIVE_BIDS.get(guild_id)

        if not current_bid:
            offer = max(bid_amount, min_start_bid)
        else:
            if bid_amount <= current_bid["amount"]:
                await interaction.followup.send(
                    f"❌ Counter-bid must be higher than current highest bid of **${current_bid['amount']:.2f}**."
                )
                return
            offer = bid_amount

        if party["treasury"] < offer:
            await interaction.followup.send(
                f"❌ Insufficient treasury funds. Party treasury: **${party['treasury']:.2f}**."
            )
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE parties SET treasury = treasury - ? WHERE party_id = ?",
                (offer, clean_party_id),
            )
            conn.commit()

        if current_bid and current_bid.get("task"):
            current_bid["task"].cancel()

        async def timer_task():
            await asyncio.sleep(60)
            await self.conclude_bidding_war(guild_id)

        task = self.bot.loop.create_task(timer_task())
        self.bot.ACTIVE_BIDS[guild_id] = {
            "party_id": clean_party_id,
            "amount": offer,
            "task": task,
        }

        bids_channel = (
            interaction.guild.get_channel(bids_channel_id)
            if bids_channel_id
            else None
        )
        if bids_channel:
            if current_bid:
                await bids_channel.send(
                    f"⚔️ **COUNTER BID!** **{party['name']}** raised the bid to **${offer:.2f}**! Counter-bid timer reset to 1 minute!"
                )
            else:
                await bids_channel.send(
                    f"📢 **NEW BID!** **{party['name']}** placed a starting bid of **${offer:.2f}** for the paid channel! 1 minute remaining for counter-offers!"
                )

        await interaction.followup.send(
            f"✅ Placed bid of **${offer:.2f}** for **{party['name']}**."
        )

    @app_commands.command(
        name="paid_message",
        description="💬 Spend $1.00 to send a message in the paid channel.",
    )
    @app_commands.describe(message_text="The message you want to post")
    @safety_wrapper("financial")
    @financial_safety(required_balance=True)
    async def paid_message(
        self, interaction: discord.Interaction, message_text: str
    ):
        await interaction.response.defer(ephemeral=True)
        
        # Safety: Validate message length
        if len(message_text) > 2000:
            await interaction.followup.send("❌ Message too long (max 2000 characters).", ephemeral=True)
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (interaction.user.id,),
            )
            row = cursor.fetchone()
            user_bal = row["balance"] if row else 0.0

            if user_bal < 1.0:
                await interaction.followup.send(
                    f"❌ You need at least **$1.00** to post a paid message. Balance: **${user_bal:.2f}**."
                )
                return

            cursor.execute(
                "SELECT paid_channel_id FROM guild_config WHERE guild_id = ?",
                (interaction.guild_id,),
            )
            cfg = cursor.fetchone()
            if not cfg or not cfg["paid_channel_id"]:
                await interaction.followup.send(
                    "❌ Paid channel is not configured."
                )
                return

            chan = interaction.guild.get_channel(cfg["paid_channel_id"])
            if not chan:
                await interaction.followup.send(
                    "❌ Could not locate paid channel."
                )
                return

            cursor.execute(
                "UPDATE users SET balance = balance - 1.0 WHERE user_id = ?",
                (interaction.user.id,),
            )
            conn.commit()

        await chan.send(
            f"📣 **Paid Message from {interaction.user.mention}:**\n{message_text}"
        )
        await interaction.followup.send(
            "✅ Paid message posted successfully ($1.00 deducted)!"
        )


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
