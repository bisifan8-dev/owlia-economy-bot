import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from views import BuyFromBotView, ManageStockOrderSelect


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
            await interaction.followup.send("❌ Shares must be positive.")
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
                    f"❌ Entity `{clean_party_id}` does not exist."
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
                    f"❌ Insufficient funds! Cost: **${total_cost:.2f}**, Balance: **${user_bal:.2f}**."
                )
                return

            if not party["is_company"] and ai_shares >= shares and actual_price == standard_cost:
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
                conn.commit()
                await interaction.followup.send(
                    f"✅ Purchased {shares:.2f} shares of **{party['name']}** directly from AI at **${actual_price:.2f}** each!"
                )
                if guild_id:
                    await self.bot.refresh_market_embeds(guild_id)
                return

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
            conn.commit()

        if guild_id:
            await self.bot.refresh_market_embeds(guild_id)
        await interaction.followup.send(
            f"✅ Posted buy offer for **{shares:.2f}** share(s) of **{party['name']}** at **${actual_price:.2f}** each!"
        )

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
                "❌ Shares and price must be positive."
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
                    f"❌ Entity `{clean_party_id}` does not exist."
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
                    f"❌ You only own **{owned:.2f}** shares."
                )
                return

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
            conn.commit()

        if guild_id:
            await self.bot.refresh_market_embeds(guild_id)
        await interaction.followup.send(
            f"✅ Posted sell offer for **{shares:.2f}** share(s) of **{party['name']}** at **${price_per_share:.2f}** each!"
        )

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