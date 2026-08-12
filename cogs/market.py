import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from views import BuyFromBotView, ManageStockOrderSelect
from utils.errors import SmartErrorMessages
from cogs.modals import ConfirmPurchaseModal, ConfirmSellModal


class MarketCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="buy", description="Create a buy order for shares of an entity."
    )
    @app_commands.describe(
        party_id="Target entity ID",
        shares="Shares count",
        use_suggested="Use auto-calculated price? (Y/N)",
        price_per_share="Custom offered price",
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        party_id: str,
        shares: float,
        use_suggested: str,
        price_per_share: float = 0.0,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if shares <= 0:
            await interaction.followup.send(
                SmartErrorMessages.invalid_amount(shares, 0.01, 1000000.0),
                ephemeral=True
            )
            return

        clean_party_id = party_id.strip().lower()
        clean_suggested = use_suggested.strip().upper()

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, treasury, total_shares, is_setup, is_company FROM parties WHERE party_id = ?",
                (clean_party_id,),
            )
            party = cursor.fetchone()

            if not party:
                await interaction.followup.send(
                    SmartErrorMessages.party_not_found(clean_party_id),
                    ephemeral=True
                )
                return

            standard_cost = (
                (party["treasury"] / party["total_shares"])
                if party["total_shares"] > 0
                else 0.0
            )
            cursor.execute(
                "SELECT SUM(shares_owned) as h_shares FROM shares WHERE party_id = ?",
                (clean_party_id,),
            )
            h_row = cursor.fetchone()
            human_shares = h_row["h_shares"] if h_row and h_row["h_shares"] else 0.0
            ai_shares = party["total_shares"] - human_shares

            if not party["is_company"] and party["is_setup"] == 1 and 0 < human_shares < party["total_shares"]:
                embed = discord.Embed(
                    title=f"Buy from bot? (Price: ${standard_cost:.2f})",
                    description=f"Party in setup phase. Share cost: **${standard_cost:.2f}**.",
                    color=discord.Color.gold(),
                )
                view = BuyFromBotView(
                    clean_party_id,
                    shares,
                    standard_cost,
                    interaction.user.id,
                    self.bot.refresh_market_embeds,
                )
                await interaction.followup.send(embed=embed, view=view)
                return

            actual_price = (
                standard_cost
                if clean_suggested in ["Y", "YES"]
                else price_per_share
            )

            if not party["is_company"] and human_shares == 0 and actual_price != standard_cost:
                await interaction.followup.send(
                    f"❌ AI-managed party. Must buy at standard cost (`${standard_cost:.2f}`) with use_suggested=Y."
                )
                return

            total_cost = shares * actual_price
            cursor.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (interaction.user.id,),
            )
            user_row = cursor.fetchone()
            user_bal = user_row["balance"] if user_row else 0.0

            if user_bal < total_cost:
                await interaction.followup.send(
                    SmartErrorMessages.insufficient_funds(user_bal, total_cost, "buy"),
                    ephemeral=True
                )
                return

            if not party["is_company"] and ai_shares >= shares and actual_price == standard_cost:
                # Show confirmation modal for direct purchase
                async def execute_direct_purchase(modal_interaction: discord.Interaction):
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                            (total_cost, interaction.user.id),
                        )
                        cursor.execute(
                            """
                            INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
                            ON CONFLICT(user_id, party_id) DO UPDATE SET shares_owned = shares_owned + ?
                        """,
                            (interaction.user.id, clean_party_id, shares, shares),
                        )
                        
                        # Log transaction
                        cursor.execute(
                            """
                            INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (interaction.user.id, "buy", -total_cost, f"Purchased {shares:.2f} shares of {party['name']}", clean_party_id)
                        )
                        conn.commit()
                        
                    await modal_interaction.response.send_message(
                        f"✅ Purchased {shares:.2f} shares of **{party['name']}** directly from AI at **${actual_price:.2f}** each!",
                        ephemeral=True
                    )
                    if guild_id:
                        await self.bot.refresh_market_embeds(guild_id)
                
                modal = ConfirmPurchaseModal(
                    party['name'],
                    shares,
                    actual_price,
                    total_cost,
                    execute_direct_purchase
                )
                await interaction.followup.send_modal(modal)
                return

            # Show confirmation for market order
            async def execute_market_order(modal_interaction: discord.Interaction):
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                        (total_cost, interaction.user.id),
                    )
                    cursor.execute(
                        """
                        INSERT INTO market_orders (user_id, party_id, order_type, shares_count, price_per_share)
                        VALUES (?, ?, 'BUY', ?, ?)
                    """,
                        (interaction.user.id, clean_party_id, shares, actual_price),
                    )
                    
                    # Log transaction
                    cursor.execute(
                        """
                        INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (interaction.user.id, "buy", -total_cost, f"Buy order for {shares:.2f} shares of {party['name']}", clean_party_id)
                    )
                    conn.commit()

                if guild_id:
                    await self.bot.refresh_market_embeds(guild_id)
                await modal_interaction.response.send_message(
                    f"✅ Posted buy offer for **{shares:.2f}** share(s) of **{party['name']}** at **${actual_price:.2f}** each!",
                    ephemeral=True
                )

            modal = ConfirmPurchaseModal(
                party['name'],
                shares,
                actual_price,
                total_cost,
                execute_market_order
            )
            await interaction.followup.send_modal(modal)

    @app_commands.command(
        name="sell", description="Create a sell order for shares you own."
    )
    @app_commands.describe(
        party_id="Target entity ID",
        shares="Shares count",
        price_per_share="Asking price per share",
    )
    async def sell(
        self,
        interaction: discord.Interaction,
        party_id: str,
        shares: float,
        price_per_share: float,
    ):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        if shares <= 0 or price_per_share <= 0:
            await interaction.followup.send(
                SmartErrorMessages.invalid_amount(shares if shares <= 0 else price_per_share, 0.01, 1000000.0),
                ephemeral=True
            )
            return

        clean_party_id = party_id.strip().lower()
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM parties WHERE party_id = ?",
                (clean_party_id,),
            )
            party = cursor.fetchone()

            if not party:
                await interaction.followup.send(
                    SmartErrorMessages.party_not_found(clean_party_id),
                    ephemeral=True
                )
                return

            cursor.execute(
                "SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ?",
                (interaction.user.id, clean_party_id),
            )
            share_row = cursor.fetchone()
            owned = share_row["shares_owned"] if share_row else 0.0

            if owned < shares:
                await interaction.followup.send(
                    SmartErrorMessages.insufficient_shares(owned, shares, party['name']),
                    ephemeral=True
                )
                return

        # Show confirmation modal
        total_value = shares * price_per_share
        
        async def execute_sell(modal_interaction: discord.Interaction):
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE shares SET shares_owned = shares_owned - ? WHERE user_id = ? AND party_id = ?",
                    (shares, interaction.user.id, clean_party_id),
                )
                cursor.execute(
                    """
                    INSERT INTO market_orders (user_id, party_id, order_type, shares_count, price_per_share)
                    VALUES (?, ?, 'SELL', ?, ?)
                """,
                    (interaction.user.id, clean_party_id, shares, price_per_share),
                )
                
                # Log transaction
                cursor.execute(
                    """
                    INSERT INTO transaction_log (user_id, transaction_type, amount, description, party_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (interaction.user.id, "sell", shares * price_per_share, f"Sell order for {shares:.2f} shares of {party['name']}", clean_party_id)
                )
                conn.commit()

            if guild_id:
                await self.bot.refresh_market_embeds(guild_id)
            await modal_interaction.response.send_message(
                f"✅ Posted sell offer for **{shares:.2f}** share(s) of **{party['name']}** at **${price_per_share:.2f}** each!",
                ephemeral=True
            )

        modal = ConfirmSellModal(
            party['name'],
            shares,
            price_per_share,
            total_value,
            execute_sell
        )
        await interaction.followup.send_modal(modal)

    @app_commands.command(
        name="manage_stock",
        description="Inspect counter-offers or cancel your active stock listings.",
    )
    async def manage_stock(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM market_orders WHERE user_id = ?",
                (interaction.user.id,),
            )
            orders = cursor.fetchall()

        if not orders:
            await interaction.followup.send(
                "❌ You have no active listings on the market."
            )
            return

        view = discord.ui.View(timeout=300)
        view.add_item(
            ManageStockOrderSelect(orders, self.bot.refresh_market_embeds)
        )
        await interaction.followup.send(
            "Select one of your active listings to manage:", view=view
        )


async def setup(bot):
    await bot.add_cog(MarketCog(bot))