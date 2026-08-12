import asyncio
import datetime
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

    @app_commands.command(
        name="info",
        description="📊 View your portfolio, net worth, and balance information."
    )
    @app_commands.describe(
        user="Optional: View another user's info (defaults to yourself)"
    )
    @safety_wrapper("default")
    async def info(
        self,
        interaction: discord.Interaction,
        user: discord.User = None
    ):
        """Display comprehensive user financial information."""
        await interaction.response.defer(ephemeral=True)
        
        target_user = user or interaction.user
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get user balance and debt
            cursor.execute(
                "SELECT balance, debt, message_count FROM users WHERE user_id = ?",
                (target_user.id,)
            )
            user_data = cursor.fetchone()
            
            if not user_data:
                if target_user.id == interaction.user.id:
                    await interaction.followup.send(
                        "❌ You don't have any financial data yet. Start by sending messages in designated channels!"
                    )
                else:
                    await interaction.followup.send(
                        f"❌ {target_user.display_name} doesn't have any financial data yet."
                    )
                return
            
            balance = user_data["balance"] or 0.0
            debt = user_data["debt"] or 0.0
            message_count = user_data["message_count"] or 0
            
            # Get all shares the user owns
            cursor.execute(
                """
                SELECT s.party_id, s.shares_owned, p.name, p.treasury, p.total_shares, p.is_company
                FROM shares s
                JOIN parties p ON s.party_id = p.party_id
                WHERE s.user_id = ? AND s.shares_owned > 0
                ORDER BY (p.treasury / p.total_shares) * s.shares_owned DESC
                """,
                (target_user.id,)
            )
            holdings = cursor.fetchall()
            
            # Calculate total stock value
            total_stock_value = 0.0
            for h in holdings:
                price = h["treasury"] / h["total_shares"] if h["total_shares"] > 0 else 0.0
                total_stock_value += price * h["shares_owned"]
            
            # Get total shares of each company for percentage calculation
            company_totals = {}
            for h in holdings:
                cursor.execute(
                    "SELECT SUM(shares_owned) as total FROM shares WHERE party_id = ?",
                    (h["party_id"],)
                )
                total_row = cursor.fetchone()
                company_totals[h["party_id"]] = total_row["total"] if total_row else 0.0
            
            # Get user's roles for party membership
            member = interaction.guild.get_member(target_user.id)
            user_roles = {str(r.id) for r in member.roles} if member else set()
            
            # Check if user is in any parties
            cursor.execute(
                """
                SELECT party_id, name, role_id FROM parties 
                WHERE is_company = 0 AND role_id IS NOT NULL
                """
            )
            all_parties = cursor.fetchall()
            user_parties = []
            for p in all_parties:
                if p["role_id"] in user_roles:
                    user_parties.append(p)
            
            # Get active loans
            cursor.execute(
                """
                SELECT request_id, company_id, amount, interest_rate, status, due_time
                FROM loan_requests
                WHERE user_id = ? AND status IN ('PENDING', 'APPROVED')
                ORDER BY request_time DESC
                """,
                (target_user.id,)
            )
            loans = cursor.fetchall()
            
            # Get pending loan requests (as borrower)
            cursor.execute(
                """
                SELECT request_id, company_id, amount, interest_rate, status
                FROM loan_requests
                WHERE user_id = ? AND status = 'PENDING'
                """,
                (target_user.id,)
            )
            pending_loans = cursor.fetchall()
        
        # Calculate net worth
        net_worth = balance + total_stock_value - debt
        
        # Build the embed
        is_self = target_user.id == interaction.user.id
        title = f"📊 {target_user.display_name}'s Portfolio" if not is_self else "📊 Your Portfolio"
        
        embed = discord.Embed(
            title=title,
            color=discord.Color.green() if net_worth >= 0 else discord.Color.red(),
            timestamp=datetime.datetime.now()
        )
        
        # Add user avatar if available
        if target_user.avatar:
            embed.set_thumbnail(url=target_user.avatar.url)
        
        # Financial Summary
        embed.add_field(
            name="💰 Financial Summary",
            value=(
                f"**Balance:** ${balance:.2f}\n"
                f"**Stock Value:** ${total_stock_value:.2f}\n"
                f"**Debt:** ${debt:.2f}\n"
                f"**Net Worth:** **${net_worth:.2f}**"
            ),
            inline=False
        )
        
        # Message Progress
        remaining = max(0, 50 - message_count)
        embed.add_field(
            name="💬 Message Progress",
            value=f"**{message_count}/50** messages\n*(**{remaining}** more until next $1.00 payout)*",
            inline=True
        )
        
        # Party Membership
        if user_parties:
            party_names = ", ".join([f"**{p['name']}**" for p in user_parties])
            embed.add_field(
                name="🏛️ Party Membership",
                value=party_names,
                inline=True
            )
        else:
            embed.add_field(
                name="🏛️ Party Membership",
                value="*No party membership*",
                inline=True
            )
        
        # Stock Holdings
        if holdings:
            holdings_text = ""
            total_shares_owned = 0
            for h in holdings:
                price = h["treasury"] / h["total_shares"] if h["total_shares"] > 0 else 0.0
                value = price * h["shares_owned"]
                total_shares_owned += h["shares_owned"]
                
                # Calculate ownership percentage
                total_company_shares = company_totals.get(h["party_id"], 1.0)
                pct = (h["shares_owned"] / total_company_shares * 100) if total_company_shares > 0 else 0
                
                tag = "🏬" if h["is_company"] else "🏢"
                holdings_text += f"{tag} **{h['name']}**: {h['shares_owned']:.2f} shares (${value:.2f}) - {pct:.1f}% of company\n"
            
            # Truncate if too long
            if len(holdings_text) > 1000:
                holdings_text = holdings_text[:997] + "..."
            
            embed.add_field(
                name=f"📈 Stock Holdings ({len(holdings)} entities)",
                value=holdings_text,
                inline=False
            )
            
            embed.add_field(
                name="📊 Total Shares Owned",
                value=f"{total_shares_owned:.2f} shares",
                inline=True
            )
        else:
            embed.add_field(
                name="📈 Stock Holdings",
                value="*No stock holdings*",
                inline=False
            )
        
        # Loans
        if loans or pending_loans:
            loan_text = ""
            for l in loans:
                status_emoji = {
                    "PENDING": "⏳",
                    "APPROVED": "✅",
                    "REJECTED": "❌",
                    "REPAID": "💚",
                    "DEFAULTED": "💀"
                }.get(l["status"], "❓")
                
                total_owed = l["amount"] + (l["amount"] * l["interest_rate"] / 100)
                loan_text += f"{status_emoji} ${l['amount']:.2f} from **{l['company_id']}** (Owes: ${total_owed:.2f})"
                if l["status"] == "APPROVED" and l["due_time"]:
                    due = datetime.datetime.strptime(l["due_time"], "%Y-%m-%d %H:%M:%S")
                    loan_text += f" - Due <t:{int(due.timestamp())}:R>"
                loan_text += "\n"
            
            if loan_text:
                embed.add_field(
                    name="💳 Loans",
                    value=loan_text,
                    inline=False
                )
        
        # Debt warning
        if debt > 0:
            embed.add_field(
                name="⚠️ Debt Warning",
                value=f"You have **${debt:.2f}** in debt! Use `/manage_debt` to view and pay off your debt.",
                inline=False
            )
        
        # Footer with timestamp
        embed.set_footer(
            text=f"Requested by {interaction.user.display_name}",
            icon_url=interaction.user.avatar.url if interaction.user.avatar else None
        )
        
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))