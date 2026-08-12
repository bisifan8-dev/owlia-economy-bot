import os
import asyncio
import discord
from discord.ext import commands
from database import init_db, get_db
from views import MarketInteractView, StockBoardView
from dotenv import load_dotenv
from aiohttp import web
import json
from datetime import datetime
from safety import setup_safety, SAFETY_CONFIG

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class EconomyBot(commands.Bot):

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.MARKET_CHANNELS = {}
        self.ACTIVE_BIDS = {}
        self.GUILD_ID = 1533217862100062238

    def load_market_channels(self):
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM guild_config")
            rows = cursor.fetchall()
            for row in rows:
                self.MARKET_CHANNELS[row["guild_id"]] = {
                    "board_channel_id": row["board_channel_id"],
                    "board_message_id": row["board_msg_id"],
                    "buy_channel_id": row["buy_channel_id"],
                    "buy_message_id": row["buy_msg_id"],
                    "sell_channel_id": row["sell_channel_id"],
                    "sell_message_id": row["sell_msg_id"],
                    "paid_channel_id": row["paid_channel_id"],
                    "bids_channel_id": row["bids_channel_id"],
                    "designated_channels": row["designated_channels"],
                }

    async def refresh_market_embeds(self, guild_id: int):
        market_data = self.MARKET_CHANNELS.get(guild_id)
        if not market_data:
            return

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM market_orders WHERE shares_count <= 0")
            conn.commit()

            cursor.execute(
                "SELECT party_id, name, treasury, total_shares FROM parties WHERE creation_pending = 0"
            )
            parties = {p["party_id"]: p for p in cursor.fetchall()}

            cursor.execute("SELECT * FROM market_orders ORDER BY order_id ASC")
            orders = cursor.fetchall()

        buy_orders = [o for o in orders if o["order_type"] == "BUY"]
        sell_orders = [o for o in orders if o["order_type"] == "SELL"]

        buy_channel = self.get_channel(market_data.get("buy_channel_id"))
        if buy_channel:
            embed = discord.Embed(
                title="📈 **LIVE BUY MARKET ORDERS**",
                description="Active buy requests posted by users seeking shares:\n*(Click button below to fulfill or counter)*",
                color=discord.Color.green(),
            )
            if not buy_orders:
                embed.add_field(
                    name="No Buy Orders",
                    value="*No active buy offers.*",
                    inline=False,
                )
            else:
                for o in buy_orders:
                    party = parties.get(o["party_id"])
                    p_name = party["name"] if party else o["party_id"]
                    current_price = (
                        (party["treasury"] / party["total_shares"])
                        if party and party["total_shares"] > 0
                        else 0
                    )
                    embed.add_field(
                        name=f"🏢 {p_name} (`{o['party_id']}`)",
                        value=f"Order `{o['order_id']:03d}` | Buyer: <@{o['user_id']}>\n└ **{o['shares_count']:.2f}** share(s) | Offered: **${o['price_per_share']:.2f}** *(Est: `${current_price:.2f}`)*",
                        inline=False,
                    )
            try:
                view = MarketInteractView("BUY", self, self.refresh_market_embeds)
                msg_id = market_data.get("buy_message_id")
                if msg_id:
                    try:
                        msg = await buy_channel.fetch_message(msg_id)
                        await msg.edit(embed=embed, view=view)
                    except discord.NotFound:
                        sent = await buy_channel.send(embed=embed, view=view)
                        market_data["buy_message_id"] = sent.id
                        with get_db() as conn:
                            conn.cursor().execute(
                                "UPDATE guild_config SET buy_msg_id = ? WHERE guild_id = ?",
                                (sent.id, guild_id),
                            )
                            conn.commit()
                else:
                    sent = await buy_channel.send(embed=embed, view=view)
                    market_data["buy_message_id"] = sent.id
                    with get_db() as conn:
                        conn.cursor().execute(
                            "UPDATE guild_config SET buy_msg_id = ? WHERE guild_id = ?",
                            (sent.id, guild_id),
                        )
                        conn.commit()
            except Exception as e:
                print(f"Error refreshing buy market: {e}")

        sell_channel = self.get_channel(market_data.get("sell_channel_id"))
        if sell_channel:
            embed = discord.Embed(
                title="📉 **LIVE SELL MARKET ORDERS**",
                description="Active sell offers posted by shareholders:\n*(Click button below to fulfill or counter)*",
                color=discord.Color.red(),
            )
            if not sell_orders:
                embed.add_field(
                    name="No Sell Orders",
                    value="*No active sell offers.*",
                    inline=False,
                )
            else:
                for o in sell_orders:
                    party = parties.get(o["party_id"])
                    p_name = party["name"] if party else o["party_id"]
                    current_price = (
                        (party["treasury"] / party["total_shares"])
                        if party and party["total_shares"] > 0
                        else 0
                    )
                    seller_str = f"<@{o['user_id']}>" if o['user_id'] != 0 else "🏬 Company Treasury Offer"
                    embed.add_field(
                        name=f"🏢 {p_name} (`{o['party_id']}`)",
                        value=f"Order `{o['order_id']:03d}` | Seller: {seller_str}\n└ **{o['shares_count']:.2f}** share(s) | Asking: **${o['price_per_share']:.2f}** *(Est: `${current_price:.2f}`)*",
                        inline=False,
                    )
            try:
                view = MarketInteractView(
                    "SELL", self, self.refresh_market_embeds
                )
                msg_id = market_data.get("sell_message_id")
                if msg_id:
                    try:
                        msg = await sell_channel.fetch_message(msg_id)
                        await msg.edit(embed=embed, view=view)
                    except discord.NotFound:
                        sent = await sell_channel.send(embed=embed, view=view)
                        market_data["sell_message_id"] = sent.id
                        with get_db() as conn:
                            conn.cursor().execute(
                                "UPDATE guild_config SET sell_msg_id = ? WHERE guild_id = ?",
                                (sent.id, guild_id),
                            )
                            conn.commit()
                else:
                    sent = await sell_channel.send(embed=embed, view=view)
                    market_data["sell_message_id"] = sent.id
                    with get_db() as conn:
                        conn.cursor().execute(
                            "UPDATE guild_config SET sell_msg_id = ? WHERE guild_id = ?",
                            (sent.id, guild_id),
                        )
                        conn.commit()
            except Exception as e:
                print(f"Error refreshing sell market: {e}")

    async def setup_hook(self):
        init_db()
        self.load_market_channels()

        self.add_view(
            MarketInteractView("BUY", self, self.refresh_market_embeds)
        )
        self.add_view(
            MarketInteractView("SELL", self, self.refresh_market_embeds)
        )

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM parties WHERE creation_pending = 0")
            parties = cursor.fetchall()
            if parties:
                self.add_view(StockBoardView(parties))

        # Initialize safety systems
        await setup_safety(self)
        
        # Set audit log channel if you have one (uncomment and set)
        # SAFETY_CONFIG["security_log_channel"] = YOUR_AUDIT_CHANNEL_ID

        await self.load_extension("cogs.admin")
        await self.load_extension("cogs.company")
        await self.load_extension("cogs.market")
        await self.load_extension("cogs.economy")
        await self.load_extension("cogs.events")

        guild = discord.Object(id=self.GUILD_ID)
        self.tree.copy_global_to(guild=guild)

        try:
            synced = await self.tree.sync(guild=guild)
            print(
                f"✅ Instantly synced {len(synced)} commands to guild {self.GUILD_ID}."
            )
        except Exception as e:
            print(f"❌ Failed to sync guild commands: {e}")

        # Start the API server for stock data on port 8081
        asyncio.create_task(self.start_api_server())

    async def start_api_server(self):
        """Start aiohttp server for stock data API"""
        app = web.Application()
        app.router.add_get('/api/stock', self.handle_stock_data)
        app.router.add_get('/api/stock/history', self.handle_stock_history)
        app.router.add_route('OPTIONS', '/{path:.*}', self.handle_options)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8081)
        await site.start()
        print("📊 Stock API server running on port 8081 (/api/stock, /api/stock/history)")

    async def handle_options(self, request):
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS"
        }
        return web.Response(status=200, headers=headers)

    async def handle_stock_data(self, request):
        """API endpoint to get all stock market data"""
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET, OPTIONS"
        }
        
        try:
            conn = get_db()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT party_id, name, treasury, total_shares, total_messages, 
                       tax_enabled, tax_percentage, is_company, structure_type
                FROM parties 
                WHERE creation_pending = 0
                ORDER BY name
            """)
            parties = cursor.fetchall()
            
            result = {
                "parties": [],
                "total_value": 0,
                "timestamp": datetime.now().isoformat()
            }
            
            for p in parties:
                price = p["treasury"] / p["total_shares"] if p["total_shares"] > 0 else 0
                market_cap = p["treasury"]
                
                cursor.execute("""
                    SELECT price, timestamp FROM stock_history 
                    WHERE party_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT 30
                """, (p["party_id"],))
                history = cursor.fetchall()
                
                history_data = []
                for h in reversed(history):
                    history_data.append({
                        "price": h["price"],
                        "timestamp": h["timestamp"]
                    })
                
                cursor.execute("""
                    SELECT user_id, shares_owned FROM shares 
                    WHERE party_id = ? AND shares_owned > 0
                    ORDER BY shares_owned DESC
                    LIMIT 5
                """, (p["party_id"],))
                shareholders = cursor.fetchall()
                
                shareholder_data = []
                for s in shareholders:
                    shareholder_data.append({
                        "user_id": s["user_id"],
                        "shares": s["shares_owned"]
                    })
                
                price_24h_ago = price
                if len(history_data) >= 2:
                    price_24h_ago = history_data[0]["price"] if history_data else price
                
                change_24h = ((price - price_24h_ago) / price_24h_ago * 100) if price_24h_ago > 0 else 0
                
                result["parties"].append({
                    "id": p["party_id"],
                    "name": p["name"],
                    "treasury": p["treasury"],
                    "total_shares": p["total_shares"],
                    "price": price,
                    "market_cap": market_cap,
                    "total_messages": p["total_messages"],
                    "is_company": bool(p["is_company"]),
                    "structure_type": p["structure_type"] or "party",
                    "tax_enabled": bool(p["tax_enabled"]),
                    "tax_percentage": p["tax_percentage"],
                    "change_24h": change_24h,
                    "history": history_data,
                    "shareholders": shareholder_data,
                    "shareholder_count": len(shareholder_data)
                })
                
                result["total_value"] += market_cap
            
            conn.close()
            return web.json_response(result, headers=headers)
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=headers)

    async def handle_stock_history(self, request):
        """API endpoint to get historical stock data for a specific party"""
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET, OPTIONS"
        }
        
        party_id = request.query.get('party_id')
        days = int(request.query.get('days', 7))
        
        if not party_id:
            return web.json_response({"error": "party_id required"}, status=400, headers=headers)
        
        try:
            from datetime import datetime, timedelta
            conn = get_db()
            cursor = conn.cursor()
            
            cutoff = datetime.now() - timedelta(days=days)
            
            cursor.execute("""
                SELECT price, timestamp FROM stock_history 
                WHERE party_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """, (party_id, cutoff.strftime("%Y-%m-%d %H:%M:%S")))
            history = cursor.fetchall()
            
            cursor.execute("""
                SELECT name FROM parties WHERE party_id = ?
            """, (party_id,))
            party = cursor.fetchone()
            
            conn.close()
            
            return web.json_response({
                "party_id": party_id,
                "name": party["name"] if party else party_id,
                "history": [{"price": h["price"], "timestamp": h["timestamp"]} for h in history]
            }, headers=headers)
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500, headers=headers)


bot = EconomyBot()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} - System Online")


if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")
    bot.run(TOKEN)