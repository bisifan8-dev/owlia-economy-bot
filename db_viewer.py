#!/usr/bin/env python3
"""
Database Viewer Dashboard with Interactive Charts
Serves a complete HTML5 dashboard showing all database data with stock charts.
Run this separately from the main bot.
"""

import json
import sqlite3
import datetime
import asyncio
from aiohttp import web
from aiohttp import web
from typing import Dict, List, Any
import os

DB_NAME = "server_economy.db"
API_PORT = 5900


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_NAME, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def dict_from_row(row) -> Dict:
    """Convert sqlite3.Row to dict with proper types."""
    if row is None:
        return None
    result = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, bytes):
            try:
                value = value.decode('utf-8')
            except:
                value = str(value)
        result[key] = value
    return result


def fetch_all_as_dict(cursor, query: str, params: tuple = ()) -> List[Dict]:
    """Execute query and return all rows as dicts."""
    cursor.execute(query, params)
    rows = cursor.fetchall()
    return [dict_from_row(row) for row in rows]


def get_all_data() -> Dict:
    """Fetch all data from the database."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        data = {
            "metadata": {
                "exported_at": datetime.datetime.now().isoformat(),
                "database": DB_NAME,
            },
            "tables": {}
        }
        
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = cursor.fetchall()
        
        for table in tables:
            table_name = table["name"]
            if table_name.startswith("sqlite_"):
                continue
            
            try:
                rows = fetch_all_as_dict(cursor, f"SELECT * FROM {table_name}")
                data["tables"][table_name] = rows
            except Exception as e:
                data["tables"][table_name] = {"error": str(e)}
        
        return data


def get_stock_data() -> Dict:
    """Get stock market data for charts."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Get all entities with their current prices
        cursor.execute("""
            SELECT 
                p.party_id,
                p.name,
                p.treasury,
                p.total_shares,
                p.is_company,
                p.structure_type,
                p.total_messages,
                (SELECT price FROM stock_history 
                 WHERE party_id = p.party_id 
                 ORDER BY timestamp DESC LIMIT 1) as current_price
            FROM parties p
            WHERE p.creation_pending = 0
            ORDER BY p.name ASC
        """)
        entities = cursor.fetchall()
        
        result = []
        for e in entities:
            current_price = e['current_price'] or 0.0
            price = e['treasury'] / e['total_shares'] if e['total_shares'] > 0 else 0
            
            # Get price history for chart
            cursor.execute("""
                SELECT price, timestamp 
                FROM stock_history 
                WHERE party_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 50
            """, (e['party_id'],))
            history = cursor.fetchall()
            
            history_data = []
            for h in reversed(history):
                history_data.append({
                    "price": h['price'],
                    "timestamp": h['timestamp']
                })
            
            # Get shareholders
            cursor.execute("""
                SELECT user_id, shares_owned 
                FROM shares 
                WHERE party_id = ? AND shares_owned > 0
                ORDER BY shares_owned DESC
            """, (e['party_id'],))
            shareholders = cursor.fetchall()
            
            result.append({
                "id": e['party_id'],
                "name": e['name'],
                "treasury": e['treasury'],
                "total_shares": e['total_shares'],
                "price": price,
                "current_price": current_price,
                "is_company": bool(e['is_company']),
                "structure_type": e['structure_type'] or 'party',
                "total_messages": e['total_messages'],
                "history": history_data,
                "shareholders": [dict_from_row(s) for s in shareholders],
                "shareholder_count": len(shareholders)
            })
        
        return result


def get_user_data() -> List[Dict]:
    """Get user financial data."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                u.user_id,
                u.balance,
                u.message_count,
                u.debt,
                u.last_daily,
                u.last_weekly,
                u.last_monthly,
                u.last_yearly,
                COALESCE(SUM(s.shares_owned), 0) as total_shares_owned
            FROM users u
            LEFT JOIN shares s ON u.user_id = s.user_id
            GROUP BY u.user_id
            ORDER BY u.balance DESC
        """)
        return [dict_from_row(row) for row in cursor.fetchall()]


def get_loan_data() -> List[Dict]:
    """Get loan data."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                l.*,
                p.name as company_name
            FROM loan_requests l
            LEFT JOIN parties p ON l.company_id = p.party_id
            ORDER BY l.request_time DESC
        """)
        return [dict_from_row(row) for row in cursor.fetchall()]


def get_order_data() -> List[Dict]:
    """Get market order data."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                mo.*,
                p.name as party_name,
                mos.status as order_status
            FROM market_orders mo
            LEFT JOIN parties p ON mo.party_id = p.party_id
            LEFT JOIN market_order_status mos ON mo.order_id = mos.order_id
            ORDER BY mo.created_at DESC
        """)
        return [dict_from_row(row) for row in cursor.fetchall()]


def get_transaction_data() -> List[Dict]:
    """Get transaction data."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                tl.*,
                u.balance as user_balance
            FROM transaction_log tl
            LEFT JOIN users u ON tl.user_id = u.user_id
            ORDER BY tl.timestamp DESC
            LIMIT 200
        """)
        return [dict_from_row(row) for row in cursor.fetchall()]


# ─── HTML DASHBOARD ─────────────────────────────────────────────

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🗄️ Economy DB Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            padding: 20px;
        }
        .header {
            background: linear-gradient(135deg, #161b22, #0d1117);
            padding: 20px 30px;
            border-radius: 12px;
            border: 1px solid #30363d;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }
        .header h1 { font-size: 28px; }
        .header h1 span { color: #58a6ff; }
        .header .status { 
            background: #238636; 
            padding: 8px 16px; 
            border-radius: 20px; 
            font-size: 14px;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 15px 20px;
            transition: transform 0.2s;
        }
        .stat-card:hover { transform: translateY(-2px); }
        .stat-card .label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
        .stat-card .value { font-size: 24px; font-weight: bold; margin-top: 5px; }
        .stat-card .value.green { color: #3fb950; }
        .stat-card .value.blue { color: #58a6ff; }
        .stat-card .value.orange { color: #d29922; }
        .stat-card .value.red { color: #f85149; }
        .stat-card .value.purple { color: #bc8cff; }
        
        .tabs {
            display: flex;
            gap: 5px;
            flex-wrap: wrap;
            margin-bottom: 20px;
            border-bottom: 1px solid #30363d;
            padding-bottom: 10px;
        }
        .tab-btn {
            background: #161b22;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 10px 20px;
            border-radius: 8px 8px 0 0;
            cursor: pointer;
            transition: all 0.3s;
            font-size: 14px;
        }
        .tab-btn:hover { background: #1c2333; }
        .tab-btn.active {
            background: #238636;
            border-color: #238636;
            color: white;
        }
        
        .tab-content {
            display: none;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .tab-content.active { display: block; }
        
        .chart-container {
            background: #0d1117;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 15px;
        }
        .chart-container canvas { max-height: 300px; }
        
        .table-wrap {
            overflow-x: auto;
            margin-top: 10px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            background: #0d1117;
            color: #8b949e;
            text-align: left;
            padding: 10px 12px;
            border-bottom: 2px solid #30363d;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        td {
            padding: 8px 12px;
            border-bottom: 1px solid #21262d;
        }
        tr:hover { background: #1c2333; }
        .badge {
            display: inline-block;
            padding: 2px 10px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge.green { background: #238636; color: white; }
        .badge.red { background: #da3633; color: white; }
        .badge.yellow { background: #d29922; color: #0d1117; }
        .badge.blue { background: #1f6feb; color: white; }
        .badge.gray { background: #30363d; color: #c9d1d9; }
        .badge.purple { background: #8957e5; color: white; }
        
        .json-view {
            background: #0d1117;
            padding: 15px;
            border-radius: 8px;
            overflow-x: auto;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            max-height: 500px;
            overflow-y: auto;
        }
        
        .search-box {
            background: #0d1117;
            border: 1px solid #30363d;
            color: #c9d1d9;
            padding: 8px 15px;
            border-radius: 8px;
            width: 100%;
            max-width: 400px;
            font-size: 14px;
        }
        .search-box:focus { outline: none; border-color: #58a6ff; }
        
        .flex { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
        .mb-10 { margin-bottom: 10px; }
        
        @media (max-width: 768px) {
            .header { flex-direction: column; gap: 10px; text-align: center; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .tab-btn { font-size: 12px; padding: 8px 12px; }
        }
        
        .loading {
            text-align: center;
            padding: 40px;
            color: #8b949e;
        }
        .spinner {
            border: 3px solid #30363d;
            border-top: 3px solid #58a6ff;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
            margin: 0 auto 15px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        
        .total-row td { font-weight: bold; background: #0d1117; border-top: 2px solid #30363d; }
    </style>
</head>
<body>

<div class="header">
    <div>
        <h1>🗄️ <span>Economy DB</span> Dashboard</h1>
        <div style="font-size: 14px; color: #8b949e; margin-top: 5px;">
            All database tables visualized with interactive charts
        </div>
    </div>
    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
        <span class="status">🟢 Live</span>
        <span id="timestamp" style="color: #8b949e; font-size: 13px;"></span>
        <button onclick="refreshAll()" style="background: #1f6feb; border: none; color: white; padding: 8px 16px; border-radius: 8px; cursor: pointer;">🔄 Refresh</button>
    </div>
</div>

<div class="stats-grid" id="statsGrid">
    <div class="stat-card"><div class="label">Total Users</div><div class="value blue" id="statUsers">-</div></div>
    <div class="stat-card"><div class="label">Total Balance</div><div class="value green" id="statBalance">-</div></div>
    <div class="stat-card"><div class="label">Total Debt</div><div class="value red" id="statDebt">-</div></div>
    <div class="stat-card"><div class="label">Entities</div><div class="value orange" id="statEntities">-</div></div>
    <div class="stat-card"><div class="label">Active Orders</div><div class="value purple" id="statOrders">-</div></div>
    <div class="stat-card"><div class="label">Loans</div><div class="value blue" id="statLoans">-</div></div>
</div>

<div class="tabs">
    <button class="tab-btn active" onclick="showTab('overview')">📊 Overview</button>
    <button class="tab-btn" onclick="showTab('stocks')">📈 Stocks</button>
    <button class="tab-btn" onclick="showTab('users')">👥 Users</button>
    <button class="tab-btn" onclick="showTab('orders')">📋 Orders</button>
    <button class="tab-btn" onclick="showTab('loans')">💳 Loans</button>
    <button class="tab-btn" onclick="showTab('transactions')">💰 Transactions</button>
    <button class="tab-btn" onclick="showTab('all_tables')">🗃️ All Tables</button>
</div>

<!-- OVERVIEW TAB -->
<div id="tab-overview" class="tab-content active">
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
        <div class="chart-container">
            <h3 style="margin-bottom: 10px;">📊 Top Entities by Treasury</h3>
            <canvas id="topTreasuryChart"></canvas>
        </div>
        <div class="chart-container">
            <h3 style="margin-bottom: 10px;">👥 Top Users by Balance</h3>
            <canvas id="topBalanceChart"></canvas>
        </div>
    </div>
    <div class="chart-container">
        <h3 style="margin-bottom: 10px;">📈 Stock Price Comparison</h3>
        <canvas id="priceComparisonChart"></canvas>
    </div>
</div>

<!-- STOCKS TAB -->
<div id="tab-stocks" class="tab-content">
    <div class="flex mb-10">
        <select id="stockSelect" onchange="updateStockChart()" style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;padding:8px 15px;border-radius:8px;">
            <option value="">Select a stock...</option>
        </select>
        <select id="stockDays" onchange="updateStockChart()" style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;padding:8px 15px;border-radius:8px;">
            <option value="7">7 Days</option>
            <option value="14">14 Days</option>
            <option value="30" selected>30 Days</option>
            <option value="60">60 Days</option>
            <option value="90">90 Days</option>
        </select>
    </div>
    <div class="chart-container">
        <canvas id="stockChart"></canvas>
    </div>
    <div id="stockDetails" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:10px;"></div>
    <div class="table-wrap" id="stockHoldings"></div>
</div>

<!-- USERS TAB -->
<div id="tab-users" class="tab-content">
    <div class="flex mb-10">
        <input class="search-box" id="userSearch" placeholder="🔍 Search users..." oninput="filterTable('usersTable', this.value)">
    </div>
    <div class="table-wrap"><table id="usersTable"><thead><tr>
        <th>User ID</th><th>Balance</th><th>Messages</th><th>Debt</th><th>Shares</th><th>Last Daily</th><th>Last Weekly</th>
    </tr></thead><tbody id="usersBody"></tbody></table></div>
</div>

<!-- ORDERS TAB -->
<div id="tab-orders" class="tab-content">
    <div class="table-wrap"><table><thead><tr>
        <th>Order ID</th><th>User</th><th>Party</th><th>Type</th><th>Shares</th><th>Price</th><th>Status</th><th>Created</th>
    </tr></thead><tbody id="ordersBody"></tbody></table></div>
</div>

<!-- LOANS TAB -->
<div id="tab-loans" class="tab-content">
    <div class="table-wrap"><table><thead><tr>
        <th>ID</th><th>Company</th><th>User</th><th>Amount</th><th>Interest</th><th>Status</th><th>Due</th><th>Created</th>
    </tr></thead><tbody id="loansBody"></tbody></table></div>
</div>

<!-- TRANSACTIONS TAB -->
<div id="tab-transactions" class="tab-content">
    <div class="table-wrap"><table><thead><tr>
        <th>ID</th><th>User</th><th>Type</th><th>Amount</th><th>Party</th><th>Description</th><th>Time</th>
    </tr></thead><tbody id="transactionsBody"></tbody></table></div>
</div>

<!-- ALL TABLES TAB -->
<div id="tab-all_tables" class="tab-content">
    <div class="flex mb-10">
        <select id="tableSelect" onchange="showTableData()" style="background:#0d1117;color:#c9d1d9;border:1px solid #30363d;padding:8px 15px;border-radius:8px;">
            <option value="">Select a table...</option>
        </select>
    </div>
    <div id="tableDataDisplay" class="json-view">Select a table to view its data</div>
</div>

<script>
    let allData = {};
    let stockData = [];
    let charts = {};

    async function fetchData() {
        try {
            const [dataRes, stockRes] = await Promise.all([
                fetch('/api/all'),
                fetch('/api/stocks')
            ]);
            allData = await dataRes.json();
            stockData = await stockRes.json();
            return true;
        } catch(e) {
            console.error('Error fetching data:', e);
            return false;
        }
    }

    function formatCurrency(v) { return '$' + Number(v).toFixed(2); }
    function formatNumber(v) { return Number(v).toLocaleString(); }
    function formatDate(d) { 
        if (!d) return '-';
        try { return new Date(d).toLocaleString(); } catch(e) { return d; }
    }
    function shortDate(d) {
        if (!d) return '-';
        try { return new Date(d).toLocaleDateString(); } catch(e) { return d; }
    }

    function getBadge(status) {
        const map = {
            'OPEN': 'green', 'PENDING': 'yellow', 'APPROVED': 'blue', 
            'REJECTED': 'red', 'REPAID': 'green', 'DEFAULTED': 'red',
            'FILLED': 'green', 'PARTIAL': 'yellow', 'CANCELLED': 'gray',
            'SENT': 'green', 'FAILED': 'red', 'COMPLETED': 'green',
            'STRUCK': 'red', 'RUNNING': 'blue', 'PENDING_STRIKE': 'yellow'
        };
        return `<span class="badge ${map[status] || 'gray'}">${status || 'Unknown'}</span>`;
    }

    function updateStats() {
        const users = allData.tables?.users || [];
        const parties = allData.tables?.parties || [];
        const orders = allData.tables?.market_orders || [];
        const loans = allData.tables?.loan_requests || [];
        
        let totalBal = 0, totalDebt = 0;
        users.forEach(u => { totalBal += (u.balance || 0); totalDebt += (u.debt || 0); });
        
        document.getElementById('statUsers').textContent = users.length;
        document.getElementById('statBalance').textContent = formatCurrency(totalBal);
        document.getElementById('statDebt').textContent = formatCurrency(totalDebt);
        document.getElementById('statEntities').textContent = parties.length;
        document.getElementById('statOrders').textContent = orders.length;
        document.getElementById('statLoans').textContent = loans.length;
        document.getElementById('timestamp').textContent = '🕐 ' + new Date().toLocaleTimeString();
    }

    function renderUsers() {
        const users = allData.tables?.users || [];
        const shares = allData.tables?.shares || [];
        const userShares = {};
        shares.forEach(s => {
            if (!userShares[s.user_id]) userShares[s.user_id] = 0;
            userShares[s.user_id] += (s.shares_owned || 0);
        });
        
        const sorted = [...users].sort((a,b) => (b.balance||0) - (a.balance||0));
        const tbody = document.getElementById('usersBody');
        tbody.innerHTML = sorted.map(u => `
            <tr>
                <td><code>${u.user_id}</code></td>
                <td><strong>${formatCurrency(u.balance)}</strong></td>
                <td>${u.message_count || 0}</td>
                <td>${u.debt > 0 ? formatCurrency(u.debt) : '-'}</td>
                <td>${(userShares[u.user_id] || 0).toFixed(2)}</td>
                <td>${shortDate(u.last_daily)}</td>
                <td>${shortDate(u.last_weekly)}</td>
            </tr>
        `).join('');
    }

    function renderOrders() {
        const orders = allData.tables?.market_orders || [];
        const statuses = allData.tables?.market_order_status || {};
        const statusMap = {};
        statuses.forEach(s => { statusMap[s.order_id] = s.status; });
        
        const sorted = [...orders].sort((a,b) => b.order_id - a.order_id);
        const tbody = document.getElementById('ordersBody');
        tbody.innerHTML = sorted.map(o => `
            <tr>
                <td><code>#${o.order_id}</code></td>
                <td><code>${o.user_id}</code></td>
                <td>${o.party_name || o.party_id}</td>
                <td><span class="badge ${o.order_type === 'BUY' ? 'green' : 'red'}">${o.order_type}</span></td>
                <td>${Number(o.shares_count).toFixed(2)}</td>
                <td>${formatCurrency(o.price_per_share)}</td>
                <td>${getBadge(statusMap[o.order_id] || 'OPEN')}</td>
                <td>${formatDate(o.created_at)}</td>
            </tr>
        `).join('');
    }

    function renderLoans() {
        const loans = allData.tables?.loan_requests || [];
        const sorted = [...loans].sort((a,b) => b.request_id - a.request_id);
        const tbody = document.getElementById('loansBody');
        tbody.innerHTML = sorted.map(l => `
            <tr>
                <td><code>#${l.request_id}</code></td>
                <td>${l.company_name || l.company_id}</td>
                <td><code>${l.user_id}</code></td>
                <td>${formatCurrency(l.amount)}</td>
                <td>${(l.interest_rate || 0)}%</td>
                <td>${getBadge(l.status)}</td>
                <td>${formatDate(l.due_time)}</td>
                <td>${formatDate(l.request_time)}</td>
            </tr>
        `).join('');
    }

    function renderTransactions() {
        const txs = allData.tables?.transaction_log || [];
        const sorted = [...txs].sort((a,b) => b.log_id - a.log_id).slice(0, 200);
        const tbody = document.getElementById('transactionsBody');
        tbody.innerHTML = sorted.map(t => `
            <tr>
                <td><code>#${t.log_id}</code></td>
                <td><code>${t.user_id}</code></td>
                <td><span class="badge ${t.amount > 0 ? 'green' : t.amount < 0 ? 'red' : 'gray'}">${t.transaction_type}</span></td>
                <td>${t.amount !== 0 ? formatCurrency(t.amount) : '-'}</td>
                <td>${t.party_id || '-'}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${t.description || ''}">${t.description || '-'}</td>
                <td>${formatDate(t.timestamp)}</td>
            </tr>
        `).join('');
    }

    function populateTableSelect() {
        const sel = document.getElementById('tableSelect');
        const tables = Object.keys(allData.tables || {}).filter(t => !t.startsWith('_'));
        sel.innerHTML = '<option value="">Select a table...</option>' + 
            tables.map(t => `<option value="${t}">${t}</option>`).join('');
    }

    function showTableData() {
        const name = document.getElementById('tableSelect').value;
        const container = document.getElementById('tableDataDisplay');
        if (!name) { container.textContent = 'Select a table to view its data'; return; }
        const data = allData.tables?.[name] || [];
        container.textContent = JSON.stringify(data, null, 2);
    }

    // ─── CHARTS ──────────────────────────────────────────────────

    function initOverviewCharts() {
        const parties = allData.tables?.parties || [];
        const sorted = [...parties].sort((a,b) => (b.treasury||0) - (a.treasury||0)).slice(0, 10);
        
        // Top Treasury Chart
        const ctx1 = document.getElementById('topTreasuryChart').getContext('2d');
        charts.treasury = new Chart(ctx1, {
            type: 'bar',
            data: {
                labels: sorted.map(p => p.name),
                datasets: [{
                    label: 'Treasury ($)',
                    data: sorted.map(p => p.treasury || 0),
                    backgroundColor: ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff', '#1f6feb', '#238636', '#da3633', '#8957e5', '#30363d'],
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { callback: v => '$' + v } } }
            }
        });

        // Top Balance Chart
        const users = allData.tables?.users || [];
        const topUsers = [...users].sort((a,b) => (b.balance||0) - (a.balance||0)).slice(0, 10);
        const ctx2 = document.getElementById('topBalanceChart').getContext('2d');
        charts.balance = new Chart(ctx2, {
            type: 'bar',
            data: {
                labels: topUsers.map(u => u.user_id),
                datasets: [{
                    label: 'Balance ($)',
                    data: topUsers.map(u => u.balance || 0),
                    backgroundColor: ['#3fb950', '#58a6ff', '#bc8cff', '#d29922', '#f85149', '#1f6feb', '#238636', '#8957e5', '#da3633', '#30363d'],
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { callback: v => '$' + v } } }
            }
        });

        // Price Comparison Chart
        const entities = stockData || [];
        const topEntities = entities.slice(0, 8);
        const ctx3 = document.getElementById('priceComparisonChart').getContext('2d');
        const colors = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#bc8cff', '#1f6feb', '#238636', '#da3633'];
        charts.comparison = new Chart(ctx3, {
            type: 'line',
            data: {
                labels: topEntities.map(e => e.name),
                datasets: [{
                    label: 'Current Price ($)',
                    data: topEntities.map(e => e.current_price || e.price || 0),
                    backgroundColor: colors,
                    borderColor: colors,
                    borderWidth: 2,
                    pointRadius: 6
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, ticks: { callback: v => '$' + v } } }
            }
        });
    }

    function populateStockSelect() {
        const sel = document.getElementById('stockSelect');
        const entities = stockData || [];
        sel.innerHTML = '<option value="">Select a stock...</option>' + 
            entities.map(e => `<option value="${e.id}">${e.name} (${e.id}) - $${(e.current_price || e.price).toFixed(2)}</option>`).join('');
    }

    function updateStockChart() {
        const id = document.getElementById('stockSelect').value;
        const days = parseInt(document.getElementById('stockDays').value);
        const entity = stockData.find(e => e.id === id);
        if (!entity) return;

        const history = entity.history || [];
        const filtered = history.slice(-days);
        
        if (charts.stock) { charts.stock.destroy(); }
        
        const ctx = document.getElementById('stockChart').getContext('2d');
        const labels = filtered.map(h => new Date(h.timestamp).toLocaleString());
        const prices = filtered.map(h => h.price);
        
        const color = entity.is_company ? '#58a6ff' : '#3fb950';
        charts.stock = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: `${entity.name} Price`,
                    data: prices,
                    borderColor: color,
                    backgroundColor: color + '33',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 2
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { labels: { color: '#c9d1d9' } }
                },
                scales: {
                    x: { ticks: { color: '#8b949e', maxTicksLimit: 20 } },
                    y: { beginAtZero: true, ticks: { color: '#8b949e', callback: v => '$' + v } }
                }
            }
        });

        // Update details
        const details = document.getElementById('stockDetails');
        const latest = prices[prices.length-1] || 0;
        const first = prices[0] || 0;
        const change = latest - first;
        const changePct = first > 0 ? (change / first * 100) : 0;
        const shareCount = entity.shareholders?.length || 0;
        
        details.innerHTML = `
            <div class="stat-card"><div class="label">Current Price</div><div class="value green">${formatCurrency(latest)}</div></div>
            <div class="stat-card"><div class="label">Change (${days}d)</div><div class="value ${change >= 0 ? 'green' : 'red'}">${change >= 0 ? '+' : ''}${formatCurrency(change)} (${changePct.toFixed(2)}%)</div></div>
            <div class="stat-card"><div class="label">Treasury</div><div class="value blue">${formatCurrency(entity.treasury)}</div></div>
            <div class="stat-card"><div class="label">Total Shares</div><div class="value orange">${formatNumber(entity.total_shares)}</div></div>
            <div class="stat-card"><div class="label">Shareholders</div><div class="value purple">${shareCount}</div></div>
            <div class="stat-card"><div class="label">Type</div><div class="value">${entity.is_company ? '🏬 Company' : '🏢 Party'}</div></div>
        `;

        // Show shareholders
        const holdingsDiv = document.getElementById('stockHoldings');
        if (entity.shareholders && entity.shareholders.length > 0) {
            const sorted = [...entity.shareholders].sort((a,b) => b.shares_owned - a.shares_owned);
            holdingsDiv.innerHTML = `
                <h4 style="margin:10px 0;">📊 Shareholders (${sorted.length})</h4>
                <table>
                    <thead><tr><th>User ID</th><th>Shares</th><th>Percentage</th></tr></thead>
                    <tbody>
                        ${sorted.map(s => `
                            <tr>
                                <td><code>${s.user_id}</code></td>
                                <td>${Number(s.shares_owned).toFixed(2)}</td>
                                <td>${(entity.total_shares > 0 ? (s.shares_owned / entity.total_shares * 100) : 0).toFixed(2)}%</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } else {
            holdingsDiv.innerHTML = '<p style="color:#8b949e;">No shareholders found.</p>';
        }
    }

    // ─── TAB SWITCHING ──────────────────────────────────────────

    function showTab(name) {
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        document.querySelector(`.tab-btn[onclick*="${name}"]`).classList.add('active');
        
        // Refresh chart if switching to stocks
        if (name === 'stocks' && !charts.stock) {
            const sel = document.getElementById('stockSelect');
            if (sel.value) updateStockChart();
        }
    }

    function filterTable(tableId, query) {
        const table = document.getElementById(tableId);
        const rows = table.querySelectorAll('tbody tr');
        const q = query.toLowerCase();
        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(q) ? '' : 'none';
        });
    }

    async function refreshAll() {
        document.getElementById('statsGrid').innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';
        await fetchData();
        renderAll();
    }

    function renderAll() {
        updateStats();
        renderUsers();
        renderOrders();
        renderLoans();
        renderTransactions();
        populateTableSelect();
        populateStockSelect();
        initOverviewCharts();
        
        // Auto-select first stock if available
        const sel = document.getElementById('stockSelect');
        if (sel.options.length > 1) {
            sel.selectedIndex = 1;
            updateStockChart();
        }
    }

    // ─── INIT ────────────────────────────────────────────────────

    (async function init() {
        await fetchData();
        renderAll();
        // Update timestamp every 30s
        setInterval(() => {
            document.getElementById('timestamp').textContent = '🕐 ' + new Date().toLocaleTimeString();
        }, 30000);
    })();
</script>
</body>
</html>
"""

# ─── HTTP HANDLERS ──────────────────────────────────────────────

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Accept",
    "Access-Control-Allow-Methods": "GET, OPTIONS"
}


async def handle_options(request):
    return web.Response(status=200, headers=CORS_HEADERS)


async def handle_dashboard(request):
    """Serve the main HTML dashboard."""
    return web.Response(
        text=HTML_TEMPLATE,
        content_type='text/html',
        headers=CORS_HEADERS
    )


async def handle_all_data(request):
    """API endpoint for all data."""
    try:
        data = get_all_data()
        return web.json_response(data, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


async def handle_stocks(request):
    """API endpoint for stock data."""
    try:
        data = get_stock_data()
        return web.json_response(data, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


async def handle_users(request):
    """API endpoint for user data."""
    try:
        data = get_user_data()
        return web.json_response(data, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


async def handle_loans(request):
    """API endpoint for loan data."""
    try:
        data = get_loan_data()
        return web.json_response(data, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


async def handle_orders(request):
    """API endpoint for order data."""
    try:
        data = get_order_data()
        return web.json_response(data, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


async def handle_transactions(request):
    """API endpoint for transaction data."""
    try:
        data = get_transaction_data()
        return web.json_response(data, headers=CORS_HEADERS)
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


async def handle_table(request):
    """API endpoint for specific table data."""
    table_name = request.match_info.get('table_name')
    if not table_name:
        return web.json_response(
            {"error": "Missing table_name"},
            status=400,
            headers=CORS_HEADERS
        )
    
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,)
            )
            if not cursor.fetchone():
                return web.json_response(
                    {"error": f"Table '{table_name}' not found"},
                    status=404,
                    headers=CORS_HEADERS
                )
            data = fetch_all_as_dict(cursor, f"SELECT * FROM {table_name}")
        return web.json_response(
            {"table": table_name, "data": data},
            headers=CORS_HEADERS
        )
    except Exception as e:
        return web.json_response(
            {"error": str(e)},
            status=500,
            headers=CORS_HEADERS
        )


# ─── SERVER ─────────────────────────────────────────────────────

async def start_dashboard_server():
    """Start the dashboard server."""
    app = web.Application()
    
    app.router.add_route('OPTIONS', '/{path:.*}', handle_options)
    app.router.add_get('/', handle_dashboard)
    app.router.add_get('/api/all', handle_all_data)
    app.router.add_get('/api/stocks', handle_stocks)
    app.router.add_get('/api/users', handle_users)
    app.router.add_get('/api/loans', handle_loans)
    app.router.add_get('/api/orders', handle_orders)
    app.router.add_get('/api/transactions', handle_transactions)
    app.router.add_get('/api/table/{table_name}', handle_table)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', API_PORT)
    await site.start()
    
    print(f"\n{'='*60}")
    print(f"🗄️  ECONOMY DB DASHBOARD")
    print(f"{'='*60}")
    print(f"📍 URL: http://localhost:{API_PORT}")
    print(f"📁 Database: {DB_NAME}")
    print(f"📊 Features:")
    print(f"   • Interactive stock charts with price history")
    print(f"   • User balance and statistics")
    print(f"   • Market orders with status tracking")
    print(f"   • Loan management view")
    print(f"   • Transaction history")
    print(f"   • Full table browser")
    print(f"{'='*60}\n")


async def main():
    """Run the dashboard server."""
    if not os.path.exists(DB_NAME):
        print(f"⚠️  Database '{DB_NAME}' not found in current directory!")
        print(f"   Make sure the bot has been run at least once.")
        print(f"   Current directory: {os.getcwd()}")
        print()
    
    print("🚀 Starting Economy Database Dashboard...")
    print(f"📁 Database: {DB_NAME}")
    print(f"🔌 Port: {API_PORT}")
    print("Press Ctrl+C to stop")
    print()
    
    try:
        await start_dashboard_server()
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())