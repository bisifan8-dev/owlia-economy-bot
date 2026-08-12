#!/usr/bin/env python3
"""
Stock Market API Server for Owlia Website
Serves real-time stock data from the bot's database to the website.
"""

import os
import json
import sqlite3
import datetime
from aiohttp import web
from database import get_db

# ─── Configuration ──────────────────────────────────────────────
API_PORT = 8081  # Different from the bot's API port (8080)

# ─── Database Helpers ──────────────────────────────────────────
def get_all_entities():
    """Get all parties and companies with current prices."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                p.party_id,
                p.name,
                p.treasury,
                p.total_shares,
                p.is_company,
                p.is_setup,
                p.structure_type,
                (SELECT price FROM stock_history 
                 WHERE party_id = p.party_id 
                 ORDER BY timestamp DESC LIMIT 1) as current_price,
                (SELECT price FROM stock_history 
                 WHERE party_id = p.party_id 
                 ORDER BY timestamp DESC LIMIT 1 OFFSET 1) as previous_price,
                (SELECT price FROM stock_history 
                 WHERE party_id = p.party_id 
                 AND timestamp >= datetime('now', '-24 hours')
                 ORDER BY timestamp ASC LIMIT 1) as day_start_price
            FROM parties p
            WHERE p.creation_pending = 0
            ORDER BY p.name ASC
        """)
        entities = cursor.fetchall()

        result = []
        for e in entities:
            current_price = e['current_price'] or 0.0
            previous_price = e['previous_price'] or current_price
            day_start_price = e['day_start_price'] or current_price

            # Calculate changes
            change_24h = current_price - day_start_price if day_start_price > 0 else 0
            change_pct = (change_24h / day_start_price * 100) if day_start_price > 0 else 0

            # Calculate 24h volume (total shares traded in last 24h)
            cursor.execute("""
                SELECT COALESCE(SUM(shares_count), 0) as volume
                FROM market_orders
                WHERE party_id = ? AND created_at >= datetime('now', '-24 hours')
            """, (e['party_id'],))
            volume_row = cursor.fetchone()
            volume_24h = volume_row['volume'] if volume_row else 0

            # Get recent history for sparklines (last 30 data points)
            cursor.execute("""
                SELECT price, timestamp
                FROM stock_history
                WHERE party_id = ?
                ORDER BY timestamp DESC
                LIMIT 30
            """, (e['party_id'],))
            history_rows = cursor.fetchall()
            history = [{'price': h['price'], 'timestamp': h['timestamp']} for h in reversed(history_rows)]

            result.append({
                'party_id': e['party_id'],
                'name': e['name'],
                'treasury': e['treasury'],
                'total_shares': e['total_shares'],
                'is_company': bool(e['is_company']),
                'is_setup': bool(e['is_setup']),
                'structure_type': e['structure_type'],
                'price': current_price,
                'change': change_24h,
                'change_pct': change_pct,
                'volume_24h': volume_24h,
                'history': history,
            })

        return result

def get_entity_history(party_id, days=7):
    """Get historical prices for a specific entity."""
    with get_db() as conn:
        cursor = conn.cursor()
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        cursor.execute("""
            SELECT price, timestamp
            FROM stock_history
            WHERE party_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
        """, (party_id, cutoff.strftime('%Y-%m-%d %H:%M:%S')))
        rows = cursor.fetchall()
        return [{'price': r['price'], 'timestamp': r['timestamp']} for r in rows]

def get_entity_orders(party_id):
    """Get active buy and sell orders for an entity."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT order_id, user_id, shares_count, price_per_share, created_at
            FROM market_orders
            WHERE party_id = ? AND order_type = 'BUY'
            ORDER BY price_per_share DESC
            LIMIT 20
        """, (party_id,))
        buy_orders = cursor.fetchall()

        cursor.execute("""
            SELECT order_id, user_id, shares_count, price_per_share, created_at
            FROM market_orders
            WHERE party_id = ? AND order_type = 'SELL'
            ORDER BY price_per_share ASC
            LIMIT 20
        """, (party_id,))
        sell_orders = cursor.fetchall()

        return {
            'buy_orders': [dict(o) for o in buy_orders],
            'sell_orders': [dict(o) for o in sell_orders],
        }

# ─── HTTP Handlers ─────────────────────────────────────────────
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS"
}

async def handle_options(request):
    return web.Response(status=200, headers=CORS_HEADERS)

async def handle_entities(request):
    """GET /api/stock/entities - Get all entities with current prices."""
    try:
        entities = get_all_entities()
        return web.json_response(entities, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500, headers=CORS_HEADERS)

async def handle_history(request):
    """GET /api/stock/history/{party_id}?days=7 - Get historical prices."""
    party_id = request.match_info.get('party_id')
    if not party_id:
        return web.json_response({'error': 'Missing party_id'}, status=400, headers=CORS_HEADERS)

    days = int(request.query.get('days', 7))
    try:
        history = get_entity_history(party_id, days)
        return web.json_response({'party_id': party_id, 'history': history}, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500, headers=CORS_HEADERS)

async def handle_orders(request):
    """GET /api/stock/orders/{party_id} - Get active orders for an entity."""
    party_id = request.match_info.get('party_id')
    if not party_id:
        return web.json_response({'error': 'Missing party_id'}, status=400, headers=CORS_HEADERS)

    try:
        orders = get_entity_orders(party_id)
        return web.json_response(orders, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response({'error': str(e)}, status=500, headers=CORS_HEADERS)

# ─── Server ─────────────────────────────────────────────────────
async def start_stock_api_server():
    """Start the stock API server."""
    app = web.Application()
    app.router.add_route('OPTIONS', '/{path:.*}', handle_options)
    app.router.add_get('/api/stock/entities', handle_entities)
    app.router.add_get('/api/stock/history/{party_id}', handle_history)
    app.router.add_get('/api/stock/orders/{party_id}', handle_orders)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', API_PORT)
    await site.start()
    print(f"📊 Stock API server running on port {API_PORT}")
    print(f"   /api/stock/entities")
    print(f"   /api/stock/history/{{party_id}}?days=7")
    print(f"   /api/stock/orders/{{party_id}}")

# ─── Standalone Mode ──────────────────────────────────────────
async def main():
    """Run the stock API server standalone."""
    print("🚀 Starting Owlia Stock API Server...")
    print(f"📁 Working directory: {os.getcwd()}")
    await start_stock_api_server()
    print("Press Ctrl+C to stop")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())