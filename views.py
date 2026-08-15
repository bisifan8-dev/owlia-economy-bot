# views.py - Complete file with fixed market interaction and acceptance queue system

import io
import sqlite3
import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import discord
from database import get_db, execute_trade, get_user_share_weight, get_order_remaining_shares


class CompanyVoteView(discord.ui.View):

    def __init__(self, vote_id: int):
        super().__init__(timeout=None)
        self.vote_id = vote_id

    @discord.ui.button(label="✅ Vote YES", style=discord.ButtonStyle.success, custom_id="company_vote_yes")
    async def vote_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_vote(interaction, "YES")

    @discord.ui.button(label="❌ Vote NO", style=discord.ButtonStyle.danger, custom_id="company_vote_no")
    async def vote_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_vote(interaction, "NO")

    async def process_vote(self, interaction: discord.Interaction, choice: str):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM company_votes WHERE vote_id = ?", (self.vote_id,))
            vote = cursor.fetchone()

            if not vote or vote["status"] != "OPEN":
                await interaction.response.send_message("❌ This vote is closed or no longer available.", ephemeral=True)
                return

            company_id = vote["company_id"]
            cursor.execute("SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ?", (interaction.user.id, company_id))
            share_row = cursor.fetchone()
            shares_owned = share_row["shares_owned"] if share_row else 0.0

            if shares_owned <= 0:
                await interaction.response.send_message("❌ You must own shares in this company to vote!", ephemeral=True)
                return

            cursor.execute("SELECT * FROM company_ballots WHERE vote_id = ? AND user_id = ?", (self.vote_id, interaction.user.id))
            ballot = cursor.fetchone()

            if ballot:
                old_choice = ballot["vote_choice"]
                old_weight = ballot["weight"]
                if old_choice == "YES":
                    cursor.execute("UPDATE company_votes SET yes_votes = max(0, yes_votes - ?) WHERE vote_id = ?", (old_weight, self.vote_id))
                else:
                    cursor.execute("UPDATE company_votes SET no_votes = max(0, no_votes - ?) WHERE vote_id = ?", (old_weight, self.vote_id))

            cursor.execute(
                """
                INSERT INTO company_ballots (vote_id, user_id, vote_choice, weight) VALUES (?, ?, ?, ?)
                ON CONFLICT(vote_id, user_id) DO UPDATE SET vote_choice = ?, weight = ?
                """,
                (self.vote_id, interaction.user.id, choice, shares_owned, choice, shares_owned)
            )

            if choice == "YES":
                cursor.execute("UPDATE company_votes SET yes_votes = yes_votes + ? WHERE vote_id = ?", (shares_owned, self.vote_id))
            else:
                cursor.execute("UPDATE company_votes SET no_votes = no_votes + ? WHERE vote_id = ?", (shares_owned, self.vote_id))

            cursor.execute("SELECT yes_votes, no_votes FROM company_votes WHERE vote_id = ?", (self.vote_id,))
            updated_vote = cursor.fetchone()
            conn.commit()

        embed = interaction.message.embeds[0]
        embed.set_field_at(0, name="📊 Current Votes", value=f"✅ YES: `{updated_vote['yes_votes']:.2f}` votes\n❌ NO: `{updated_vote['no_votes']:.2f}` votes", inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message(f"✅ Cast **{shares_owned:.2f}** votes for **{choice}**!", ephemeral=True)


class BoardElectionView(discord.ui.View):

    def __init__(self, election_id: int, bot_instance):
        super().__init__(timeout=None)
        self.election_id = election_id
        self.bot = bot_instance


class StockBoardView(discord.ui.View):

    def __init__(self, parties, selected_parties=None, selected_days=7):
        super().__init__(timeout=None)
        self.all_parties = parties
        self.selected_parties = selected_parties or ([parties[0]["party_id"]] if parties else [])
        self.selected_days = selected_days

        party_options = [
            discord.SelectOption(
                label=p["name"],
                value=p["party_id"],
                description=f"ID: {p['party_id']}",
                default=(p["party_id"] in self.selected_parties)
            )
            for p in self.all_parties[:25]
        ]

        if party_options:
            party_select = discord.ui.Select(
                custom_id="stock_board_party_select",
                placeholder="Choose 1-4 entities to view on chart...",
                min_values=1,
                max_values=min(4, len(party_options)),
                options=party_options,
                row=0
            )
            party_select.callback = self.party_select_callback
            self.add_item(party_select)

        day_choices = [1, 2, 4, 7, 30, 60, 120]
        day_options = [
            discord.SelectOption(
                label=f"{d} Day{'s' if d > 1 else ''}",
                value=str(d),
                default=(d == self.selected_days)
            )
            for d in day_choices
        ]

        day_select = discord.ui.Select(
            custom_id="stock_board_day_select",
            placeholder="Choose timeframe...",
            min_values=1,
            max_values=1,
            options=day_options,
            row=1
        )
        day_select.callback = self.day_select_callback
        self.add_item(day_select)

    async def party_select_callback(self, interaction: discord.Interaction):
        self.selected_parties = interaction.data["values"]
        await self.render_updated_board(interaction)

    async def day_select_callback(self, interaction: discord.Interaction):
        self.selected_days = int(interaction.data["values"][0])
        await self.render_updated_board(interaction)

    async def render_updated_board(self, interaction: discord.Interaction):
        await interaction.response.defer()
        file, embed = self.generate_chart_file(self.selected_parties, self.selected_days)
        
        new_view = StockBoardView(self.all_parties, self.selected_parties, self.selected_days)
        if file and embed:
            await interaction.message.edit(embed=embed, attachments=[file], view=new_view)
        else:
            await interaction.message.edit(view=new_view)

    @staticmethod
    def generate_chart_file(selected_party_ids, days):
        if not selected_party_ids:
            return None, None

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT party_id, name, treasury, total_shares FROM parties")
            parties_dict = {p["party_id"]: p for p in cursor.fetchall()}

            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
            
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(8, 4), dpi=120)

            for p_id in selected_party_ids:
                p_info = parties_dict.get(p_id)
                p_name = p_info["name"] if p_info else p_id
                current_price = (p_info["treasury"] / p_info["total_shares"]) if p_info and p_info["total_shares"] > 0 else 0.0

                cursor.execute(
                    """
                    SELECT price, timestamp FROM stock_history 
                    WHERE party_id = ? AND timestamp >= ? 
                    ORDER BY timestamp ASC
                    """,
                    (p_id, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
                )
                rows = cursor.fetchall()

                timestamps = []
                prices = []
                for r in rows:
                    try:
                        timestamps.append(datetime.datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S"))
                        prices.append(r["price"])
                    except ValueError:
                        continue

                timestamps.append(datetime.datetime.utcnow())
                prices.append(current_price)

                if len(timestamps) == 1:
                    timestamps.insert(0, cutoff)
                    prices.insert(0, current_price)

                ax.plot(timestamps, prices, marker='o', label=p_name, linewidth=2, markersize=4)

            ax.set_title(f"Stock Price History ({days} Day{'s' if days > 1 else ''})", fontsize=12, pad=10)
            ax.set_ylabel("Share Price ($)", fontsize=10)
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.legend(loc="upper left")
            
            if days <= 2:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            elif days <= 7:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
            else:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))

            fig.autofmt_xdate()
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', transparent=False, facecolor='#2f3136')
            buf.seek(0)
            plt.close(fig)

            file = discord.File(buf, filename="stock_chart.png")
            embed = discord.Embed(
                title="📈 Stock Price History Chart",
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://stock_chart.png")
            return file, embed


class BuyFromBotView(discord.ui.View):

    def __init__(
        self,
        party_id: str,
        shares: float,
        price_per_share: float,
        user_id: int,
        refresh_callback,
    ):
        super().__init__(timeout=180)
        self.party_id = party_id
        self.shares = shares
        self.price_per_share = price_per_share
        self.user_id = user_id
        self.refresh_callback = refresh_callback

    @discord.ui.button(label="Buy Shares", style=discord.ButtonStyle.success)
    async def buy_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This purchase menu is not for you.", ephemeral=True
            )
            return

        total_cost = self.shares * self.price_per_share
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (interaction.user.id,),
            )
            user_row = cursor.fetchone()
            user_bal = user_row["balance"] if user_row else 0.0

            if user_bal < total_cost:
                await interaction.response.send_message(
                    f"❌ Insufficient funds! Total cost: **${total_cost:.2f}**, Balance: **${user_bal:.2f}**.",
                    ephemeral=True,
                )
                return

            # Deduct money from user
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                (total_cost, interaction.user.id),
            )
            
            # Give shares to user (bot's unissued shares)
            cursor.execute(
                """
                INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
                ON CONFLICT(user_id, party_id) DO UPDATE SET shares_owned = shares_owned + ?
                """,
                (interaction.user.id, self.party_id, self.shares, self.shares),
            )
            
            # Log transaction
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (interaction.user.id, "buy_from_bot", -total_cost, f"Purchased {self.shares:.2f} shares from bot at ${self.price_per_share:.2f} each", self.party_id)
            )
            
            # Get current price for display
            cursor.execute("SELECT treasury, total_shares FROM parties WHERE party_id = ?", (self.party_id,))
            p_data = cursor.fetchone()
            current_price = p_data["treasury"] / p_data["total_shares"] if p_data["total_shares"] > 0 else 0.0
            
            conn.commit()

        button.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Purchased **{self.shares:.2f}** share(s) for **${total_cost:.2f}** directly from the bot!\n"
                    f"💰 Treasury: **${p_data['treasury']:.2f}** | Price: **${current_price:.2f}**",
            view=self,
        )
        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)


class CounterOfferModal(discord.ui.Modal, title="Submit Counter-Offer"):

    price = discord.ui.TextInput(
        label="Your Price Per Share",
        style=discord.TextStyle.short,
        placeholder="e.g. 1.50",
        required=True,
    )

    def __init__(self, order, poster_id, bot_instance):
        super().__init__()
        self.order = order
        self.poster_id = poster_id
        self.bot = bot_instance

    async def on_submit(self, interaction: discord.Interaction):
        try:
            offer_price = float(self.price.value)
            if offer_price <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid price.", ephemeral=True
            )
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO counter_offers (order_id, user_id, price) VALUES (?, ?, ?)",
                (self.order["order_id"], interaction.user.id, offer_price),
            )
            conn.commit()

        await interaction.response.send_message(
            f"✅ Counter-offer of **${offer_price:.2f}** sent!", ephemeral=True
        )
        poster = self.bot.get_user(self.poster_id)
        if poster:
            try:
                order_type_str = (
                    "SELL" if self.order["order_type"] == "SELL" else "BUY"
                )
                await poster.send(
                    f"🔔 **New Counter-Offer!** You received an offer of **${offer_price:.2f}** per share on your {order_type_str} order `{self.order['order_id']:03d}`. Use `/manage_stock` to review."
                )
            except discord.Forbidden:
                pass


class OrderActionView(discord.ui.View):
    """View for interacting with an order (accept/counter) - DEPRECATED, kept for compatibility"""

    def __init__(self, order, bot_instance, refresh_callback):
        super().__init__(timeout=300)
        self.order = order
        self.poster_id = order["user_id"]
        self.bot = bot_instance
        self.refresh_callback = refresh_callback

    @discord.ui.button(label="✅ Will Accept", style=discord.ButtonStyle.success)
    async def accept_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        # This is now handled by the acceptance system - redirect user
        await interaction.response.send_message(
            "📋 This has been replaced by the acceptance queue system. Please use the 'Interact with Offers' button to express interest.",
            ephemeral=True
        )

    @discord.ui.button(
        label="📉 Will Accept If Lower",
        style=discord.ButtonStyle.secondary,
        custom_id="counter_btn",
    )
    async def counter_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id == self.poster_id:
            await interaction.response.send_message(
                "❌ You cannot counter-offer your own order.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            CounterOfferModal(self.order, self.poster_id, self.bot)
        )


class OrderSelectDropdown(discord.ui.Select):
    """Select an order to interact with - DEPRECATED, kept for compatibility"""

    def __init__(self, orders, bot_instance, refresh_callback):
        options = [
            discord.SelectOption(
                label=f"Order {o['order_id']:03d} - {o['party_id']}",
                description=f"{o['shares_count']:.2f} shares @ ${o['price_per_share']:.2f}",
                value=str(o["order_id"]),
            )
            for o in orders
        ]
        super().__init__(
            placeholder="Select an order to interact with...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.orders = {str(o["order_id"]): o for o in orders}
        self.bot = bot_instance
        self.refresh_callback = refresh_callback

    async def callback(self, interaction: discord.Interaction):
        selected = self.orders[self.values[0]]
        view = OrderActionView(selected, self.bot, self.refresh_callback)
        view.children[1].label = (
            "📉 Will Accept If Lower"
            if selected["order_type"] == "SELL"
            else "📈 Will Accept If Higher"
        )

        embed = discord.Embed(
            title=f"Interact with Order `{selected['order_id']:03d}`",
            description=f"**Poster:** <@{selected['user_id']}>\n**Shares:** {selected['shares_count']:.2f}\n**Price:** ${selected['price_per_share']:.2f}",
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class MarketInteractView(discord.ui.View):
    """View for interacting with market orders (buy/sell) - UPDATED with acceptance system"""

    def __init__(
        self, order_type: str, bot_instance=None, refresh_callback=None
    ):
        super().__init__(timeout=None)
        self.order_type = order_type
        self.bot = bot_instance
        self.refresh_callback = refresh_callback
        self.children[0].custom_id = f"market_interact_{order_type}"

    @discord.ui.button(
        label="Interact with Offers", style=discord.ButtonStyle.primary
    )
    async def interact_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT mo.*, mos.status as order_status
                FROM market_orders mo
                LEFT JOIN market_order_status mos ON mo.order_id = mos.order_id
                WHERE mo.order_type = ? AND mos.status IN ('OPEN', 'PARTIAL')
                ORDER BY mo.order_id ASC
                LIMIT 25
                """,
                (self.order_type,),
            )
            orders = cursor.fetchall()

        if not orders:
            await interaction.response.send_message(
                "❌ No active offers to interact with right now.", ephemeral=True
            )
            return

        view = discord.ui.View(timeout=300)
        view.add_item(
            MarketOrderSelectDropdown(orders, self.bot, self.refresh_callback)
        )
        await interaction.response.send_message(
            "Select an offer below to express interest:", view=view, ephemeral=True
        )


class MarketOrderSelectDropdown(discord.ui.Select):
    """Select an order to express interest in."""

    def __init__(self, orders, bot_instance, refresh_callback):
        options = []
        for o in orders[:25]:
            status_emoji = "🟢" if o['order_status'] == 'OPEN' else "🟡"
            
            # Check if user already has a pending acceptance
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) as count FROM market_acceptances WHERE order_id = ? AND user_id = ? AND status = 'PENDING'",
                    (o["order_id"], bot_instance.user.id if bot_instance else 0)
                )
                count_row = cursor.fetchone()
                has_pending = count_row["count"] > 0 if count_row else False
            
            status_indicator = "⏳" if has_pending else ""
            
            options.append(
                discord.SelectOption(
                    label=f"{status_emoji} {status_indicator} Order {o['order_id']:03d} - {o['party_id']}",
                    description=f"{o['shares_count']:.2f} shares @ ${o['price_per_share']:.2f}",
                    value=str(o["order_id"]),
                )
            )
        
        super().__init__(
            placeholder="Select an order to express interest...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.orders = {str(o["order_id"]): o for o in orders}
        self.bot = bot_instance
        self.refresh_callback = refresh_callback

    async def callback(self, interaction: discord.Interaction):
        selected = self.orders[self.values[0]]
        
        # Check if user is trying to interact with their own order
        if selected["user_id"] == interaction.user.id:
            await interaction.response.send_message(
                "❌ You cannot express interest in your own order.",
                ephemeral=True
            )
            return
        
        # Check if user already has a pending acceptance for this order
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM market_acceptances WHERE order_id = ? AND user_id = ? AND status = 'PENDING'",
                (selected["order_id"], interaction.user.id)
            )
            existing = cursor.fetchone()
            if existing:
                await interaction.response.send_message(
                    "⏳ You already have a pending acceptance for this order. Please wait for the owner to respond.",
                    ephemeral=True
                )
                return
        
        # Show modal to confirm acceptance
        modal = ConfirmAcceptanceModal(selected, self.bot, self.refresh_callback)
        await interaction.response.send_modal(modal)


class ConfirmAcceptanceModal(discord.ui.Modal, title="Express Interest in Offer"):
    """Modal for confirming interest in an offer."""

    def __init__(self, order, bot_instance, refresh_callback):
        super().__init__(timeout=120)
        self.order = order
        self.bot = bot_instance
        self.refresh_callback = refresh_callback
        
        # Calculate max shares
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(shares_count), 0) as total FROM market_acceptances WHERE order_id = ? AND status IN ('PENDING', 'ACCEPTED')",
                (order["order_id"],)
            )
            row = cursor.fetchone()
            accepted_so_far = row["total"] if row else 0.0
            self.max_shares = order["shares_count"] - accepted_so_far
        
        order_type_label = "BUY" if order["order_type"] == "BUY" else "SELL"
        self.add_item(discord.ui.TextInput(
            label=f"Shares (max {self.max_shares:.2f})",
            placeholder=f"Enter shares up to {self.max_shares:.2f}",
            required=True,
            style=discord.TextStyle.short,
            custom_id="shares_input"
        ))
        
        self.add_item(discord.ui.TextInput(
            label=f"Price: ${order['price_per_share']:.2f} per share",
            placeholder=f"This is the {order_type_label} price",
            required=False,
            style=discord.TextStyle.short,
            default=f"Total: ${self.max_shares * order['price_per_share']:.2f}",
            custom_id="info_input"
        ))

    async def on_submit(self, interaction: discord.Interaction):
        shares_input = None
        for child in self.children:
            if child.custom_id == "shares_input":
                shares_input = child
                break
        
        if not shares_input:
            await interaction.response.send_message(
                "❌ Error reading input.", ephemeral=True
            )
            return
        
        try:
            shares = float(shares_input.value)
            if shares <= 0:
                raise ValueError
            if shares > self.max_shares:
                await interaction.response.send_message(
                    f"❌ You can only accept up to {self.max_shares:.2f} shares.",
                    ephemeral=True
                )
                return
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid positive number.",
                ephemeral=True
            )
            return
        
        # Check if user has enough funds/shares
        with get_db() as conn:
            cursor = conn.cursor()
            
            if self.order["order_type"] == "SELL":
                # Buyer needs funds
                total_cost = shares * self.order["price_per_share"]
                cursor.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (interaction.user.id,)
                )
                row = cursor.fetchone()
                balance = row["balance"] if row else 0.0
                if balance < total_cost:
                    await interaction.response.send_message(
                        f"❌ Insufficient funds! You need **${total_cost:.2f}** but have **${balance:.2f}**.",
                        ephemeral=True
                    )
                    return
            else:
                # Seller needs shares
                cursor.execute(
                    "SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ?",
                    (interaction.user.id, self.order["party_id"])
                )
                row = cursor.fetchone()
                owned = row["shares_owned"] if row else 0.0
                if owned < shares:
                    await interaction.response.send_message(
                        f"❌ You don't have enough shares! You own **{owned:.2f}** but need **{shares:.2f}**.",
                        ephemeral=True
                    )
                    return
            
            # Create acceptance
            cursor.execute(
                """
                INSERT INTO market_acceptances (order_id, user_id, shares_count, price_per_share, status)
                VALUES (?, ?, ?, ?, 'PENDING')
                """,
                (self.order["order_id"], interaction.user.id, shares, self.order["price_per_share"])
            )
            acceptance_id = cursor.lastrowid
            
            # Log transaction
            cursor.execute(
                """
                INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                VALUES (?, 'accept_offer', 0, ?, ?)
                """,
                (
                    interaction.user.id,
                    f"Expressed interest in order #{self.order['order_id']} for {shares:.2f} shares at ${self.order['price_per_share']:.2f}",
                    self.order["party_id"]
                )
            )
            conn.commit()
        
        await interaction.response.send_message(
            f"✅ You've expressed interest in **{shares:.2f}** shares at **${self.order['price_per_share']:.2f}**.\n"
            f"📋 The order owner has been notified and can accept or reject your interest.\n"
            f"You'll be notified if they accept.",
            ephemeral=True
        )
        
        # Notify the order owner
        try:
            owner = await self.bot.fetch_user(self.order["user_id"])
            if owner:
                await owner.send(
                    f"🔔 **New Interest in Your Offer!**\n"
                    f"**Order #{self.order['order_id']}** - {self.order['party_id']}\n"
                    f"**User:** {interaction.user.display_name}\n"
                    f"**Shares:** {shares:.2f}\n"
                    f"**Price:** ${self.order['price_per_share']:.2f}\n"
                    f"**Total:** ${shares * self.order['price_per_share']:.2f}\n\n"
                    f"Use `/manage_stock` to accept or reject this interest."
                )
        except:
            pass
        
        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)


class PartySetupModal(discord.ui.Modal, title="Add Initial Party"):

    party_id = discord.ui.TextInput(
        label="Party ID (Short, e.g. corp)", required=True
    )
    name = discord.ui.TextInput(label="Full Display Name", required=True)
    role_id = discord.ui.TextInput(label="Discord Role ID", required=True)
    manager_role_id = discord.ui.TextInput(
        label="Manager Role ID (Blank for Auto-Create)", required=False
    )
    tax_and_shares = discord.ui.TextInput(
        label="Tax % & Starting Shares (Format: tax, shares)",
        placeholder="e.g. 0, 20  or  5, 50",
        default="0, 20",
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        clean_id = self.party_id.value.strip().lower()

        tax_shares_parts = [
            p.strip() for p in self.tax_and_shares.value.split(",")
        ]
        try:
            tax = float(tax_shares_parts[0])
            shares = (
                float(tax_shares_parts[1])
                if len(tax_shares_parts) > 1
                else 20.0
            )
        except (ValueError, IndexError):
            await interaction.response.send_message(
                "❌ Invalid format for Tax % and Shares. Use `tax, shares` format (e.g. `0, 20`).",
                ephemeral=True,
            )
            return

        m_role_id = self.manager_role_id.value.strip()
        if not m_role_id:
            role = await interaction.guild.create_role(
                name=f"{self.name.value} Manager"
            )
            m_role_id = str(role.id)

        tax_enabled = 1 if tax > 0 else 0
        with get_db() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    INSERT INTO parties (party_id, name, treasury, total_shares, tax_enabled, tax_percentage, role_id, manager_role_id, is_setup, is_company)
                    VALUES (?, ?, 50.0, ?, ?, ?, ?, ?, 1, 0)
                """,
                    (
                        clean_id,
                        self.name.value,
                        shares,
                        tax_enabled,
                        tax,
                        self.role_id.value.strip(),
                        m_role_id,
                    ),
                )
                init_price = 50.0 / shares if shares > 0 else 0.0
                cursor.execute(
                    "INSERT INTO stock_history (party_id, price) VALUES (?, ?)",
                    (clean_id, init_price),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                await interaction.response.send_message(
                    "❌ That Party ID already exists.", ephemeral=True
                )
                return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM parties WHERE is_company = 0")
            parties = cursor.fetchall()

        embed = interaction.message.embeds[0]
        desc = "**Registered Parties:**\n"
        for p in parties:
            desc += f"• **{p['name']}** (`{p['party_id']}`) - Role: `{p['role_id']}` | Mgr: `{p['manager_role_id']}` | Total Issued Shares: {p['total_shares']}\n"
        embed.description = desc
        await interaction.response.edit_message(embed=embed)


class SetupView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="➕ Add Initial Party", style=discord.ButtonStyle.primary
    )
    async def add_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PartySetupModal())

    @discord.ui.button(
        label="✅ Finish Setup", style=discord.ButtonStyle.success
    )
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE parties SET is_setup = 0 WHERE is_company = 0")
            conn.commit()

        embed = interaction.message.embeds[0]
        embed.title = "✅ Setup Complete"
        await interaction.response.edit_message(embed=embed, view=self)


class TreasuryPartySelect(discord.ui.Select):

    def __init__(self, managed_parties):
        options = [
            discord.SelectOption(
                label=p["name"],
                value=p["party_id"],
                description=f"Treasury: ${p['treasury']:.2f}",
            )
            for p in managed_parties
        ]
        super().__init__(
            placeholder="Select an entity treasury to manage...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.managed_parties = {p["party_id"]: p for p in managed_parties}

    async def callback(self, interaction: discord.Interaction):
        p = self.managed_parties[self.values[0]]
        embed = discord.Embed(
            title=f"🏛️ {p['name']} Treasury",
            description=f"Current Balance: **${p['treasury']:.2f}**\nTo spend treasury funds, use `/treasury_spend`.",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


class ManageStockOrderSelect(discord.ui.Select):
    """Select an order to manage with acceptance queue support."""

    def __init__(self, orders, refresh_callback):
        options = []
        for o in orders:
            order_status = o.get("order_status", "OPEN")
            status_emoji = "🟢" if order_status == "OPEN" else "🟡" if order_status == "PARTIAL" else "✅"
            
            # Get acceptance count
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) as count FROM market_acceptances WHERE order_id = ? AND status = 'PENDING'",
                    (o["order_id"],)
                )
                count_row = cursor.fetchone()
                pending_count = count_row["count"] if count_row else 0
            
            options.append(
                discord.SelectOption(
                    label=f"{status_emoji} Order {o['order_id']:03d} ({o['order_type']}) - {o['party_id']}",
                    description=f"{o['shares_count']:.2f} shares @ ${o['price_per_share']:.2f} [{pending_count} pending]",
                    value=str(o["order_id"]),
                )
            )
        
        super().__init__(
            placeholder="Select an active listing to inspect or cancel...",
            min_values=1,
            max_values=1,
            options=options[:25],
        )
        self.orders = {str(o["order_id"]): o for o in orders}
        self.refresh_callback = refresh_callback

    async def callback(self, interaction: discord.Interaction):
        selected = self.orders[self.values[0]]
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM counter_offers WHERE order_id = ?",
                (selected["order_id"],),
            )
            counters = cursor.fetchall()
            
            # Get pending acceptances
            cursor.execute(
                """
                SELECT * FROM market_acceptances 
                WHERE order_id = ? AND status = 'PENDING'
                ORDER BY created_at ASC
                """,
                (selected["order_id"],)
            )
            pending_acceptances = cursor.fetchall()
            
            # Get accepted acceptances
            cursor.execute(
                """
                SELECT * FROM market_acceptances 
                WHERE order_id = ? AND status = 'ACCEPTED'
                ORDER BY created_at ASC
                """,
                (selected["order_id"],)
            )
            accepted_acceptances = cursor.fetchall()
            
            # Get order status
            cursor.execute(
                "SELECT status FROM market_order_status WHERE order_id = ?",
                (selected["order_id"],)
            )
            status_row = cursor.fetchone()
            order_status = status_row["status"] if status_row else "OPEN"
            
            # Get total accepted shares
            cursor.execute(
                "SELECT COALESCE(SUM(shares_count), 0) as total FROM market_acceptances WHERE order_id = ? AND status IN ('PENDING', 'ACCEPTED')",
                (selected["order_id"],)
            )
            total_row = cursor.fetchone()
            total_accepted = total_row["total"] if total_row else 0.0

        embed = discord.Embed(
            title=f"📊 Listing `{selected['order_id']:03d}` Details",
            description=(
                f"**Type:** {selected['order_type']}\n"
                f"**Entity ID:** {selected['party_id']}\n"
                f"**Shares:** {selected['shares_count']:.2f}\n"
                f"**Price:** ${selected['price_per_share']:.2f}\n"
                f"**Status:** {order_status}\n"
                f"**Remaining:** {selected['shares_count'] - total_accepted:.2f}\n"
                f"**Counter Offers:** {len(counters)}\n"
                f"**Pending Acceptances:** {len(pending_acceptances)}\n"
                f"**Accepted:** {len(accepted_acceptances)}"
            ),
            color=discord.Color.purple(),
        )

        view = OrderManageView(selected, counters, pending_acceptances, accepted_acceptances, self.refresh_callback)
        await interaction.response.edit_message(
            embed=embed, view=view, content=None
        )


class OrderManageView(discord.ui.View):
    """View for managing a specific order with acceptance queue."""

    def __init__(self, order, counters, pending_acceptances, accepted_acceptances, refresh_callback):
        super().__init__(timeout=300)
        self.order = order
        self.refresh_callback = refresh_callback

        if counters:
            self.add_item(CounterSelect(order, counters, refresh_callback))
        
        if pending_acceptances:
            self.add_item(AcceptanceSelect(order, pending_acceptances, refresh_callback, "pending"))
        
        if accepted_acceptances:
            self.add_item(AcceptanceSelect(order, accepted_acceptances, refresh_callback, "accepted"))

    @discord.ui.button(
        label="✏️ Edit Price",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def edit_price(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Open modal to edit order price."""
        modal = EditPriceModal(self.order, self.refresh_callback)
        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="🗑️ Delete Listing",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def delete_order(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Cancel and delete the order."""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM market_orders WHERE order_id = ?",
                (self.order["order_id"],),
            )
            current_order = cursor.fetchone()

            if not current_order:
                await interaction.response.send_message(
                    "❌ Order no longer exists.", ephemeral=True
                )
                return

            # Refund shares or funds based on order type
            if current_order["order_type"] == "BUY":
                refund = current_order["shares_count"] * current_order["price_per_share"]
                cursor.execute(
                    "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                    (refund, interaction.user.id),
                )
            else:
                # Return shares to user
                cursor.execute(
                    """
                    INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
                    ON CONFLICT(user_id, party_id) DO UPDATE SET shares_owned = shares_owned + ?
                """,
                    (
                        interaction.user.id,
                        current_order["party_id"],
                        current_order["shares_count"],
                        current_order["shares_count"],
                    ),
                )

            # Delete everything related
            cursor.execute(
                "DELETE FROM market_orders WHERE order_id = ?",
                (self.order["order_id"],),
            )
            cursor.execute(
                "DELETE FROM counter_offers WHERE order_id = ?",
                (self.order["order_id"],),
            )
            cursor.execute(
                "DELETE FROM market_acceptances WHERE order_id = ?",
                (self.order["order_id"],),
            )
            cursor.execute(
                "DELETE FROM market_order_status WHERE order_id = ?",
                (self.order["order_id"],),
            )
            conn.commit()

        await interaction.response.edit_message(
            content=f"🗑️ Order `{self.order['order_id']:03d}` has been cancelled and deleted.",
            embed=None,
            view=None,
        )

        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)


class EditPriceModal(discord.ui.Modal, title="Edit Order Price"):
    """Modal for editing the price of an order."""
    
    new_price = discord.ui.TextInput(
        label="New Price Per Share",
        placeholder="e.g. 2.50",
        required=True,
        style=discord.TextStyle.short
    )
    
    def __init__(self, order, refresh_callback):
        super().__init__(timeout=120)
        self.order = order
        self.refresh_callback = refresh_callback
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_price = float(self.new_price.value)
            if new_price <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid positive number.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE market_orders SET price_per_share = ? WHERE order_id = ?",
                (new_price, self.order["order_id"])
            )
            cursor.execute(
                "UPDATE market_order_status SET updated_at = CURRENT_TIMESTAMP WHERE order_id = ?",
                (self.order["order_id"],)
            )
            conn.commit()
        
        await interaction.response.send_message(
            f"✅ Price updated to **${new_price:.2f}** for order #{self.order['order_id']:03d}.",
            ephemeral=True
        )
        
        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)


class AcceptanceSelect(discord.ui.Select):
    """Select an acceptance to accept or reject."""

    def __init__(self, order, acceptances, refresh_callback, acceptance_type="pending"):
        self.acceptance_type = acceptance_type
        
        if acceptance_type == "pending":
            label_prefix = "⏳ Pending"
            options = [
                discord.SelectOption(
                    label=f"{label_prefix} from User {a['user_id']}",
                    description=f"{a['shares_count']:.2f} shares @ ${a['price_per_share']:.2f}",
                    value=str(a["acceptance_id"]),
                )
                for a in acceptances[:25]
            ]
        else:
            label_prefix = "✅ Accepted"
            options = [
                discord.SelectOption(
                    label=f"{label_prefix} from User {a['user_id']}",
                    description=f"{a['shares_count']:.2f} shares @ ${a['price_per_share']:.2f}",
                    value=str(a["acceptance_id"]),
                )
                for a in acceptances[:25]
            ]
        
        super().__init__(
            placeholder=f"Select a {acceptance_type} acceptance to manage...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.order = order
        self.acceptances = {str(a["acceptance_id"]): a for a in acceptances}
        self.refresh_callback = refresh_callback

    async def callback(self, interaction: discord.Interaction):
        selected = self.acceptances[self.values[0]]
        
        # If pending, show accept/reject options
        if self.acceptance_type == "pending":
            view = PendingAcceptanceView(
                self.order,
                selected,
                self.refresh_callback
            )
            embed = discord.Embed(
                title=f"⏳ Pending Acceptance #{selected['acceptance_id']}",
                description=(
                    f"**User:** <@{selected['user_id']}>\n"
                    f"**Shares:** {selected['shares_count']:.2f}\n"
                    f"**Price:** ${selected['price_per_share']:.2f}\n"
                    f"**Total:** ${selected['shares_count'] * selected['price_per_share']:.2f}\n"
                    f"**Created:** <t:{int(datetime.datetime.fromisoformat(selected['created_at']).timestamp())}:R>"
                ),
                color=discord.Color.gold()
            )
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            # Show accepted acceptance details
            embed = discord.Embed(
                title=f"✅ Accepted Acceptance #{selected['acceptance_id']}",
                description=(
                    f"**User:** <@{selected['user_id']}>\n"
                    f"**Shares:** {selected['shares_count']:.2f}\n"
                    f"**Price:** ${selected['price_per_share']:.2f}\n"
                    f"**Total:** ${selected['shares_count'] * selected['price_per_share']:.2f}\n"
                    f"**Accepted:** <t:{int(datetime.datetime.fromisoformat(selected['created_at']).timestamp())}:R>"
                ),
                color=discord.Color.green()
            )
            await interaction.response.edit_message(embed=embed, view=None)


class PendingAcceptanceView(discord.ui.View):
    """View for accepting or rejecting a pending acceptance."""

    def __init__(self, order, acceptance, refresh_callback):
        super().__init__(timeout=300)
        self.order = order
        self.acceptance = acceptance
        self.refresh_callback = refresh_callback

    @discord.ui.button(label="✅ Accept Offer", style=discord.ButtonStyle.success)
    async def accept_offer(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Accept the buyer's offer and execute the trade."""
        # Check if user is the order owner
        if interaction.user.id != self.order["user_id"]:
            await interaction.response.send_message(
                "❌ Only the order owner can accept offers.",
                ephemeral=True
            )
            return
        
        # Check if order still has enough shares
        remaining = get_order_remaining_shares(self.order["order_id"])
        if remaining < self.acceptance["shares_count"]:
            await interaction.response.send_message(
                f"❌ Not enough shares remaining. Only {remaining:.2f} shares left.",
                ephemeral=True
            )
            return
        
        # Execute the trade
        with get_db() as conn:
            cursor = conn.cursor()
            
            buyer_id = self.acceptance["user_id"]
            seller_id = self.order["user_id"]
            
            # The order is from the seller (SELL order), the acceptance is from a buyer
            await execute_trade(
                conn,
                cursor,
                buyer_id,
                seller_id,
                self.order["party_id"],
                self.acceptance["shares_count"],
                self.order["price_per_share"],
                "SELL",
                self.order["order_id"],
                self.acceptance["acceptance_id"]
            )
            conn.commit()
        
        await interaction.response.edit_message(
            content=f"✅ Trade executed! {self.acceptance['shares_count']:.2f} shares sold to <@{buyer_id}> at ${self.order['price_per_share']:.2f}.",
            embed=None,
            view=None
        )
        
        # Notify the buyer
        try:
            buyer = await interaction.client.fetch_user(buyer_id)
            if buyer:
                await buyer.send(
                    f"✅ **Your offer was accepted!**\n"
                    f"**Order #{self.order['order_id']}** - {self.order['party_id']}\n"
                    f"**Shares:** {self.acceptance['shares_count']:.2f}\n"
                    f"**Price:** ${self.order['price_per_share']:.2f}\n"
                    f"**Total:** ${self.acceptance['shares_count'] * self.order['price_per_share']:.2f}"
                )
        except:
            pass
        
        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)

    @discord.ui.button(label="❌ Reject", style=discord.ButtonStyle.danger)
    async def reject_offer(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Reject the acceptance."""
        if interaction.user.id != self.order["user_id"]:
            await interaction.response.send_message(
                "❌ Only the order owner can reject offers.",
                ephemeral=True
            )
            return
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE market_acceptances SET status = 'REJECTED' WHERE acceptance_id = ?",
                (self.acceptance["acceptance_id"],)
            )
            conn.commit()
        
        # Notify the buyer
        try:
            buyer = await interaction.client.fetch_user(self.acceptance["user_id"])
            if buyer:
                await buyer.send(
                    f"❌ **Your offer was rejected.**\n"
                    f"**Order #{self.order['order_id']}** - {self.order['party_id']}\n"
                    f"The order owner has rejected your interest."
                )
        except:
            pass
        
        await interaction.response.edit_message(
            content=f"❌ Rejected acceptance from <@{self.acceptance['user_id']}>.",
            embed=None,
            view=None
        )
        
        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)


class CounterSelect(discord.ui.Select):
    """Select a counter-offer to accept."""

    def __init__(self, order, counter_offers, refresh_callback):
        options = [
            discord.SelectOption(
                label=f"User {c['user_id']} offered ${c['price']:.2f}",
                value=str(c["offer_id"]),
            )
            for c in counter_offers
        ]
        super().__init__(
            placeholder="Select a counter-offer to accept...",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.order = order
        self.counter_offers = {str(c["offer_id"]): c for c in counter_offers}
        self.refresh_callback = refresh_callback

    async def callback(self, interaction: discord.Interaction):
        selected_offer = self.counter_offers[self.values[0]]
        with get_db() as conn:
            cursor = conn.cursor()
            buyer_id, seller_id = (
                (selected_offer["user_id"], self.order["user_id"])
                if self.order["order_type"] == "SELL"
                else (self.order["user_id"], selected_offer["user_id"])
            )

            if self.order["order_type"] == "SELL":
                cursor.execute(
                    "SELECT balance FROM users WHERE user_id = ?", (buyer_id,)
                )
                user_row = cursor.fetchone()
                if (
                    not user_row
                    or user_row["balance"]
                    < selected_offer["price"] * self.order["shares_count"]
                ):
                    await interaction.response.send_message(
                        "❌ User no longer has enough funds.", ephemeral=True
                    )
                    return
            else:
                cursor.execute(
                    "SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ?",
                    (seller_id, self.order["party_id"]),
                )
                s_row = cursor.fetchone()
                own = s_row["shares_owned"] if s_row else 0.0
                if own < self.order["shares_count"]:
                    await interaction.response.send_message(
                        "❌ User no longer has enough shares.", ephemeral=True
                    )
                    return

            # Execute trade with the counter-offer price
            await execute_trade(
                conn,
                cursor,
                buyer_id,
                seller_id,
                self.order["party_id"],
                self.order["shares_count"],
                selected_offer["price"],
                self.order["order_type"],
                self.order["order_id"],
                None  # No acceptance ID for counter-offers
            )
            conn.commit()

        await interaction.response.edit_message(
            content=f"✅ Counter-offer from User {selected_offer['user_id']} Accepted at **${selected_offer['price']:.2f}**!",
            view=None,
            embed=None,
        )
        if interaction.guild_id:
            await self.refresh_callback(interaction.guild_id)


class HistoryView(discord.ui.View):
    """Pagination view for transaction history."""
    
    def __init__(self, transactions, current_page: int, total_pages: int, total_count: int, bot):
        super().__init__(timeout=120)
        self.transactions = transactions
        self.current_page = current_page
        self.total_pages = total_pages
        self.total_count = total_count
        self.bot = bot
        
    def get_embed(self) -> discord.Embed:
        """Generate the embed for the current page."""
        embed = discord.Embed(
            title="📜 Transaction History",
            description=f"Showing page {self.current_page} of {self.total_pages} ({self.total_count} total transactions)",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        
        if not self.transactions:
            embed.add_field(
                name="No Transactions",
                value="No transactions on this page.",
                inline=False
            )
            return embed
        
        for tx in self.transactions:
            # Determine emoji based on transaction type
            emoji_map = {
                "pay": "💸",
                "buy": "📈",
                "sell": "📉",
                "invest": "🏦",
                "treasury_spend": "🏛️",
                "party_bid": "⚔️",
                "paid_message": "💬",
                "loan": "💳",
                "repay_loan": "💰",
                "pay_debt": "💵",
                "admin_add_balance": "🛠️",
                "accept_offer": "🤝",
                "buy_from_bot": "🤖"
            }
            emoji = emoji_map.get(tx["transaction_type"], "📊")
            
            # Format amount with sign
            amount = tx["amount"]
            sign = "+" if amount > 0 else ""
            amount_str = f"{sign}${amount:.2f}" if amount != 0 else "$0.00"
            
            # Parse timestamp
            try:
                ts = datetime.datetime.strptime(tx["timestamp"], "%Y-%m-%d %H:%M:%S")
                time_str = f"<t:{int(ts.timestamp())}:R>"
            except:
                time_str = "Unknown time"
            
            # Build description
            desc = tx["description"] or tx["transaction_type"]
            if tx["party_id"]:
                desc += f" (ID: {tx['party_id']})"
            
            embed.add_field(
                name=f"{emoji} {tx['transaction_type'].title().replace('_', ' ')}",
                value=f"{desc}\n**Amount:** {amount_str} • **{time_str}**",
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.current_page} of {self.total_pages}")
        return embed
    
    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to previous page."""
        if self.current_page <= 1:
            await interaction.response.send_message("You're already on the first page.", ephemeral=True)
            return
        
        self.current_page -= 1
        await self._refresh(interaction)
    
    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Go to next page."""
        if self.current_page >= self.total_pages:
            await interaction.response.send_message("You're already on the last page.", ephemeral=True)
            return
        
        self.current_page += 1
        await self._refresh(interaction)
    
    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Refresh the current page."""
        await self._refresh(interaction)
    
    async def _refresh(self, interaction: discord.Interaction):
        """Refresh the view with new data."""
        await interaction.response.defer()
        
        per_page = 5
        offset = (self.current_page - 1) * per_page
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT log_id, transaction_type, amount, description, party_id, timestamp
                FROM transaction_log 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ? OFFSET ?
                """,
                (interaction.user.id, per_page, offset)
            )
            self.transactions = cursor.fetchall()
            
            # Update total count
            cursor.execute(
                "SELECT COUNT(*) as total FROM transaction_log WHERE user_id = ?",
                (interaction.user.id,)
            )
            total_row = cursor.fetchone()
            self.total_count = total_row["total"] if total_row else 0
            self.total_pages = (self.total_count + per_page - 1) // per_page
        
        embed = self.get_embed()
        await interaction.edit_original_response(embed=embed, view=self)
    
    async def on_timeout(self):
        """Disable buttons when view times out."""
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except:
            pass