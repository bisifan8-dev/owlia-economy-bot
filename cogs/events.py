import discord
from database import get_db
from discord.ext import commands, tasks
from views import StockBoardView
import datetime


class EventsCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.update_stock_boards.start()
        self.process_paychecks_and_votes.start()
        self.process_board_elections.start()

    def cog_unload(self):
        self.update_stock_boards.cancel()
        self.process_paychecks_and_votes.cancel()
        self.process_board_elections.cancel()

    async def process_party_leave(
        self, user_id: int, party_id: str, guild_id: int = None
    ):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT total_messages, treasury, is_company FROM parties WHERE party_id = ?",
                (party_id,),
            )
            party = cursor.fetchone()
            if not party or party["is_company"]:
                return

            cursor.execute(
                "SELECT messages_sent FROM party_member_stats WHERE user_id = ? AND party_id = ?",
                (user_id, party_id),
            )
            row = cursor.fetchone()
            if not row or row["messages_sent"] <= 0:
                return

            user_msgs = row["messages_sent"]
            treasury = party["treasury"]
            total_msgs = party["total_messages"]

            if treasury <= 0 or total_msgs <= 0:
                return

            msg_value_total = total_msgs * 0.002
            msg_pct_now = (
                min(1.0, max(0.0, msg_value_total / treasury))
                if treasury > 0
                else 0.0
            )

            loss = user_msgs * msg_pct_now * msg_pct_now
            new_treasury = max(0.0, treasury - loss)

            cursor.execute(
                "UPDATE parties SET treasury = ? WHERE party_id = ?",
                (new_treasury, party_id),
            )
            cursor.execute(
                "DELETE FROM party_member_stats WHERE user_id = ? AND party_id = ?",
                (user_id, party_id),
            )
            
            cursor.execute("SELECT total_shares FROM parties WHERE party_id = ?", (party_id,))
            p_shares = cursor.fetchone()["total_shares"]
            new_price = new_treasury / p_shares if p_shares > 0 else 0.0
            cursor.execute("INSERT INTO stock_history (party_id, price) VALUES (?, ?)", (party_id, new_price))

            conn.commit()

        if guild_id:
            await self.bot.refresh_market_embeds(guild_id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT party_id, role_id FROM parties")
            parties = cursor.fetchall()
            for p in parties:
                if p["role_id"] and any(
                    str(r.id) == p["role_id"] for r in member.roles
                ):
                    await self.process_party_leave(
                        member.id, p["party_id"], member.guild.id
                    )

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ):
        removed_roles = set(before.roles) - set(after.roles)
        added_roles = set(after.roles) - set(before.roles)

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT party_id, role_id, is_company FROM parties")
            entities = cursor.fetchall()

            if removed_roles:
                removed_role_ids = {str(r.id) for r in removed_roles}
                for p in entities:
                    if p["role_id"] in removed_role_ids:
                        await self.process_party_leave(
                            after.id, p["party_id"], after.guild.id
                        )

            # Sync company channel permissions if shares/roles modified
            if added_roles:
                added_role_ids = {str(r.id) for r in added_roles}
                econ_cog = self.bot.get_cog("EconomyCog")
                for p in entities:
                    if p["is_company"] and p["role_id"] in added_role_ids and econ_cog:
                        await econ_cog.sync_company_permissions(p["party_id"], after.guild)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id
        if guild_id in self.bot.MARKET_CHANNELS:
            designated_str = self.bot.MARKET_CHANNELS[guild_id].get(
                "designated_channels", ""
            )
            if designated_str:
                valid_channels = designated_str.split(",")
                if str(message.channel.id) not in valid_channels:
                    return

        if len(message.content.strip()) > 4:
            with get_db() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO users (user_id, balance, message_count, premium_credits)
                    VALUES (?, 0, 0, 0)
                    ON CONFLICT(user_id) DO NOTHING
                    """,
                    (message.author.id,),
                )

                cursor.execute(
                    "SELECT party_id, role_id, tax_enabled, tax_percentage FROM parties WHERE is_company = 0"
                )
                parties = cursor.fetchall()

                user_party = None
                for p in parties:
                    if p["role_id"] and any(
                        str(r.id) == p["role_id"] for r in message.author.roles
                    ):
                        user_party = p
                        break

                cursor.execute(
                    "SELECT message_count, balance FROM users WHERE user_id = ?",
                    (message.author.id,),
                )
                row = cursor.fetchone()

                msg_count = row["message_count"] + 1
                balance = row["balance"]

                if msg_count >= 50:
                    msg_count = 0
                    base_payout = 1.00
                    tax_deducted = 0.00

                    if user_party and user_party["tax_enabled"]:
                        tax_pct = user_party["tax_percentage"] / 100.0
                        tax_deducted = base_payout * tax_pct
                        
                        cursor.execute(
                            "UPDATE parties SET treasury = treasury + ? WHERE party_id = ?",
                            (tax_deducted, user_party["party_id"]),
                        )

                    actual_payout = base_payout - tax_deducted
                    balance += actual_payout

                    try:
                        if tax_deducted > 0:
                            await message.author.send(
                                f"💸 You hit 50 non-spam messages! Earned **${actual_payout:.2f}** "
                                f"(**${tax_deducted:.2f}** was deducted as party tax)."
                            )
                        else:
                            await message.author.send(
                                "💸 You hit 50 non-spam messages in designated channels and earned **$1.00**!"
                            )
                    except discord.Forbidden:
                        pass

                cursor.execute(
                    "UPDATE users SET message_count = ?, balance = ? WHERE user_id = ?",
                    (msg_count, balance, message.author.id),
                )

                if user_party:
                    party_id = user_party["party_id"]
                    base_grant = 0.002

                    cursor.execute(
                        "UPDATE parties SET total_messages = total_messages + 1, treasury = treasury + ? WHERE party_id = ?",
                        (base_grant, party_id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO party_member_stats (user_id, party_id, messages_sent) VALUES (?, ?, 1)
                        ON CONFLICT(user_id, party_id) DO UPDATE SET messages_sent = messages_sent + 1
                        """,
                        (message.author.id, party_id),
                    )

                    cursor.execute("SELECT treasury, total_shares FROM parties WHERE party_id = ?", (party_id,))
                    p_info = cursor.fetchone()
                    cur_price = p_info["treasury"] / p_info["total_shares"] if p_info["total_shares"] > 0 else 0.0
                    cursor.execute("INSERT INTO stock_history (party_id, price) VALUES (?, ?)", (party_id, cur_price))

                conn.commit()

    @tasks.loop(seconds=60.0)
    async def process_board_elections(self):
        """Process board election status changes and conclude elections."""
        with get_db() as conn:
            cursor = conn.cursor()
            now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            
            # Move from NOMINATION to VOTING when nomination period ends
            cursor.execute(
                """
                UPDATE board_elections 
                SET status = 'VOTING' 
                WHERE status = 'NOMINATION' AND nomination_end <= ?
                """,
                (now,)
            )
            
            # Move from VOTING to CLOSED when voting period ends
            cursor.execute(
                """
                UPDATE board_elections 
                SET status = 'CLOSED' 
                WHERE status = 'VOTING' AND voting_end <= ?
                """,
                (now,)
            )
            
            # Process closed elections - determine winners and assign board seats
            cursor.execute(
                """
                SELECT e.election_id, e.company_id, e.seat_number
                FROM board_elections e
                WHERE e.status = 'CLOSED' AND e.election_id NOT IN (
                    SELECT DISTINCT election_id FROM board_elections WHERE election_id IN (
                        SELECT election_id FROM board_elections WHERE status = 'CLOSED'
                    ) AND election_id NOT IN (
                        SELECT election_id FROM board_members WHERE company_id = e.company_id AND seat_number = e.seat_number
                    )
                )
                """
            )
            closed_elections = cursor.fetchall()
            
            for election in closed_elections:
                # Check if this election has already been processed (winner assigned)
                cursor.execute(
                    "SELECT * FROM board_members WHERE company_id = ? AND seat_number = ?",
                    (election["company_id"], election["seat_number"])
                )
                if cursor.fetchone():
                    continue
                
                # Get vote totals for candidates
                cursor.execute(
                    """
                    SELECT c.user_id, COALESCE(SUM(v.weight), 0) as total_votes
                    FROM board_candidates c
                    LEFT JOIN board_votes v ON c.candidate_id = v.candidate_id
                    WHERE c.election_id = ?
                    GROUP BY c.candidate_id, c.user_id
                    ORDER BY total_votes DESC
                    LIMIT 1
                    """,
                    (election["election_id"],)
                )
                winner = cursor.fetchone()
                
                if winner and winner["total_votes"] > 0:
                    # Assign winner to board seat
                    cursor.execute(
                        """
                        INSERT INTO board_members (company_id, user_id, seat_number)
                        VALUES (?, ?, ?)
                        """,
                        (election["company_id"], winner["user_id"], election["seat_number"])
                    )
                    
                    # Sync permissions for this company
                    guild = self.bot.get_guild(1533217862100062238)  # TODO: Get from config
                    if guild:
                        company_cog = self.bot.get_cog("CompanyCog")
                        if company_cog:
                            await company_cog.sync_company_permissions(election["company_id"], guild)
                    
                    # Notify in vote channel
                    cursor.execute(
                        "SELECT name, vote_channel_id FROM parties WHERE party_id = ?",
                        (election["company_id"],)
                    )
                    company = cursor.fetchone()
                    if company and company["vote_channel_id"]:
                        channel = self.bot.get_channel(company["vote_channel_id"])
                        if channel:
                            await channel.send(
                                f"👑 **Election Complete!** <@{winner['user_id']}> has been elected to Seat #{election['seat_number']} of **{company['name']}** with **{winner['total_votes']:.2f}** votes!"
                            )
            
            conn.commit()

    @tasks.loop(seconds=60.0)
    async def process_paychecks_and_votes(self):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM company_paychecks")
            paychecks = cursor.fetchall()

            for pay in paychecks:
                salary = pay["salary"]
                comp_id = pay["company_id"]
                u_id = pay["user_id"]

                cursor.execute("SELECT treasury FROM parties WHERE party_id = ?", (comp_id,))
                comp = cursor.fetchone()
                if comp and comp["treasury"] >= salary and salary > 0:
                    cursor.execute("UPDATE parties SET treasury = treasury - ? WHERE party_id = ?", (salary, comp_id))
                    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (salary, u_id))

            cursor.execute("SELECT * FROM company_votes WHERE status = 'OPEN'")
            votes = cursor.fetchall()

            for vote in votes:
                v_id = vote["vote_id"]
                comp_id = vote["company_id"]
                yes_v = vote["yes_votes"]
                no_v = vote["no_votes"]

                cursor.execute("SELECT total_shares, treasury, name FROM parties WHERE party_id = ?", (comp_id,))
                company = cursor.fetchone()
                if not company:
                    continue

                if (yes_v + no_v) >= (company["total_shares"] * 0.51):
                    cursor.execute("UPDATE company_votes SET status = 'CLOSED' WHERE vote_id = ?", (v_id,))
                    if yes_v > no_v:
                        if vote["vote_type"] == "issue_shares":
                            shares_add = vote["shares_to_create"]
                            sell_price = vote["sell_price_per_share"]
                            new_shares = company["total_shares"] + shares_add

                            cursor.execute("UPDATE parties SET total_shares = ? WHERE party_id = ?", (new_shares, comp_id))
                            cursor.execute(
                                """
                                INSERT INTO market_orders (user_id, party_id, order_type, shares_count, price_per_share)
                                VALUES (0, ?, 'SELL', ?, ?)
                                """,
                                (comp_id, shares_add, sell_price)
                            )
                        elif vote["vote_type"] == "delete_company":
                            cursor.execute("DELETE FROM parties WHERE party_id = ?", (comp_id,))
                            cursor.execute("DELETE FROM shares WHERE party_id = ?", (comp_id,))
                            cursor.execute("DELETE FROM market_orders WHERE party_id = ?", (comp_id,))

            conn.commit()

    @tasks.loop(seconds=10.0)
    async def update_stock_boards(self):
        if not self.bot.MARKET_CHANNELS:
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT party_id, name, treasury, total_shares, total_messages, tax_enabled, tax_percentage, is_company FROM parties WHERE creation_pending = 0"
            )
            parties = cursor.fetchall()

        board_text = (
            "🏛️ **LIVE STOCK MARKET BOARD** (Auto-updates continuously)\n"
            + ("=" * 45)
            + "\n"
        )
        if not parties:
            board_text += "*No registered entities found yet. Use `/setup` or `/register_company` to add some!*"
        else:
            for (
                p_id,
                name,
                treasury,
                total_shares,
                total_messages,
                tax_enabled,
                tax_pct,
                is_company,
            ) in parties:
                price = treasury / total_shares if total_shares > 0 else 0
                tag = "🏬 Company" if is_company else "🏢 Party"
                tax_str = (
                    f" | Tax Rate: `{tax_pct:.1f}%`"
                    if tax_enabled
                    else " | Tax: `Disabled`"
                )
                board_text += f"{tag} **{name}** (`{p_id}`)\n└ Treasury: `${treasury:.2f}` | Share Price: `${price:.2f}` | Messages: `{total_messages}`{tax_str}\n\n"

        for guild_id, channel_data in list(self.bot.MARKET_CHANNELS.items()):
            board_channel_id = channel_data.get("board_channel_id")
            if not board_channel_id:
                continue

            message_id = channel_data.get("board_message_id")
            channel = self.bot.get_channel(board_channel_id)
            if not channel:
                continue

            try:
                view = StockBoardView(parties)
                file, chart_embed = StockBoardView.generate_chart_file(
                    [p["party_id"] for p in parties[:1]], days=7
                )

                if message_id:
                    try:
                        message = await channel.fetch_message(message_id)
                        if file and chart_embed:
                            await message.edit(content=board_text, embed=chart_embed, attachments=[file], view=view)
                        else:
                            await message.edit(content=board_text, view=view)
                    except discord.NotFound:
                        if file and chart_embed:
                            sent_msg = await channel.send(content=board_text, embed=chart_embed, file=file, view=view)
                        else:
                            sent_msg = await channel.send(content=board_text, view=view)
                        self.bot.MARKET_CHANNELS[guild_id]["board_message_id"] = sent_msg.id
                else:
                    if file and chart_embed:
                        sent_msg = await channel.send(content=board_text, embed=chart_embed, file=file, view=view)
                    else:
                        sent_msg = await channel.send(content=board_text, view=view)
                    self.bot.MARKET_CHANNELS[guild_id]["board_message_id"] = sent_msg.id
            except Exception as e:
                print(f"❌ Error updating stock board chart: {e}")

            await self.bot.refresh_market_embeds(guild_id)

    @update_stock_boards.before_loop
    async def before_update_stock_boards(self):
        await self.bot.wait_until_ready()

    @process_paychecks_and_votes.before_loop
    async def before_process_paychecks_and_votes(self):
        await self.bot.wait_until_ready()

    @process_board_elections.before_loop
    async def before_process_board_elections(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
