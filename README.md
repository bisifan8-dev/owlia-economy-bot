# 🏦 Owlia Economy Bot

<div align="center">

![Discord](https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**A feature-rich Discord economy simulation with real-time stock trading, company management, and political parties.**

[Features](#-features) • [Quick Start](#-quick-start) • [Commands](#-commands) • [API](#-api) • [Contributing](#-contributing)

</div>

---

## 📖 Overview

Owlia Economy Bot transforms your Discord server into a living economy where users can:
- Trade stocks in real-time markets
- Form and manage companies with shareholder democracy
- Run for CEO and board positions
- Request and approve loans
- Form political parties with taxes and treasuries
- Participate in bidding wars for advertising channels

Built with **Python** and **discord.py**, it features a SQLite database for persistence and a REST API for external data access.

---

## ✨ Features

### 🏛️ **Core Economy**
| Feature | Description |
|---------|-------------|
| **Stock Market** | Real-time trading with buy/sell orders, counter-offers, and price charts |
| **Message Payouts** | Earn $1.00 for every 50 messages in designated channels |
| **Paid Messages** | Users pay $1.00 to broadcast messages in premium channels |
| **Treasury System** | Entities have treasuries that grow through taxes and investments |

### 🏢 **Company System**
| Feature | Description |
|---------|-------------|
| **Company Creation** | Three structures: Sole Proprietorship, Partnership, Corporation |
| **Share Issuance** | Investment-based share distribution with price calculation |
| **CEO Elections** | Share-weighted voting for leadership positions |
| **Board Seats** | Democratic board elections with nomination and voting periods |
| **Payroll System** | Set recurring salaries for employees |

### 💳 **Loan System**
| Feature | Description |
|---------|-------------|
| **Loan Requests** | Users can request loans from companies |
| **Interest Rates** | Configurable interest on loans |
| **Auto-Repayment** | Automatic repayment if user has sufficient funds |
| **Default Handling** | Users who default accrue debt and lose purchasing privileges |
| **Loan Management** | Company managers can approve or reject requests |

### 🎭 **Political Parties**
| Feature | Description |
|---------|-------------|
| **Party Creation** | Register parties with custom roles and managers |
| **Taxation** | Parties can tax member payouts |
| **Treasury Growth** | Tax revenue accumulates in party treasuries |
| **Member Stats** | Track message contributions per party |

### 🔒 **Safety & Security**
| Feature | Description |
|---------|-------------|
| **Rate Limiting** | Per-command rate limits to prevent abuse |
| **Transaction Safety** | ACID-compliant transactions with rollback |
| **Audit Logging** | Complete audit trail of admin and financial actions |
| **User Suspension** | Temporarily suspend users from financial commands |
| **Input Validation** | Sanitization and validation of all user inputs |
| **Trust Scores** | Track user reputation over time |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.8+** installed
- **Discord Bot Token** ([Get yours here](https://discord.com/developers/applications/1533286330879050019/))
- **Git** (optional, for cloning)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/bisifan8-dev/owlia-economy-bot.git
cd owlia-economy-bot

# 2. Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env file
echo "DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE" > .env

# 5. Run the bot
python main.py
```

### Docker Deployment (Optional)

> **Note:** Official Docker images are not yet available. You can build your own using the provided Dockerfile.

```dockerfile
# Dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

```bash
docker build -t owlia-bot .
docker run -d --name owlia-bot -v $(pwd)/data:/app/data owlia-bot
```

---

## 📋 Command Reference

### 📈 Market Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/buy` | Create a buy order for shares | `/buy party_id:techcorp shares:10 use_suggested:Y` |
| `/sell` | Create a sell order for shares | `/sell party_id:techcorp shares:5 price_per_share:2.50` |
| `/manage_stock` | View and manage your orders | `/manage_stock` |

### 🏢 Company Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/register_company` | Create a new company | `/register_company company_id:mycorp name:"My Corp" structure:corporation` |
| `/invest` | Invest capital in a company | `/invest company_id:mycorp amount:100` |
| `/company_info` | View company details | `/company_info company_id:mycorp` |
| `/vote_ceo` | Vote for CEO | `/vote_ceo company_id:mycorp candidate:@user` |
| `/ceo_results` | View election results | `/ceo_results company_id:mycorp` |

### 💰 Financial Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/pay` | Send money to another user | `/pay target:@user amount:5.00` |
| `/messages` | Check your message stats | `/messages` |
| `/treasury` | View entity treasury | `/treasury` |
| `/treasury_spend` | Spend from entity treasury | `/treasury_spend party_id:mycorp target:@user amount:10` |

### 💳 Loan Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/request_loan` | Request a loan | `/request_loan company_id:mycorp amount:100 duration_hours:24` |
| `/approve_loan` | Approve/reject a loan | `/approve_loan request_id:1 approve:Y` |
| `/manage_loan` | View your loans | `/manage_loan` |
| `/repay_loan` | Repay an approved loan | `/repay_loan request_id:1` |
| `/manage_debt` | View your debt | `/manage_debt` |
| `/pay_debt` | Pay toward your debt | `/pay_debt amount:50` |

### ⚙️ Admin Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/setup` | Complete market setup | `/setup designated_channels:#general,#chat paid_channel:#ads` |
| `/register_party` | Create a political party | `/register_party party_id:democrats name:Democrats role:@Democrat` |
| `/manage_party` | Manage party properties | `/manage_party party_id:democrats action:edit_name new_name:"New Name"` |
| `/add_balance` | Add money to a user | `/add_balance target:@user amount:100` |
| `/safety_status` | View safety system status | `/safety_status` |
| `/suspend_user` | Suspend a user | `/suspend_user user:@user reason:"Spam" duration_hours:24` |

---

## 🔌 API Endpoints

The bot exposes a REST API on port **8081** for external data access.

### 📊 Get All Entities
```
GET /api/stock/entities
```

**Response:**
```json
{
  "parties": [
    {
      "party_id": "techcorp",
      "name": "TechCorp Inc.",
      "price": 2.50,
      "change": 0.25,
      "change_pct": 11.11,
      "volume_24h": 150.5,
      "history": [
        {"price": 2.25, "timestamp": "2024-01-15 10:00:00"},
        {"price": 2.50, "timestamp": "2024-01-15 11:00:00"}
      ]
    }
  ],
  "total_value": 12500.00,
  "timestamp": "2024-01-15T12:00:00"
}
```

### 📈 Get Entity History
```
GET /api/stock/history/{party_id}?days=7
```

### 📋 Get Active Orders
```
GET /api/stock/orders/{party_id}
```

---

## 📁 Project Structure

```
owlia-economy-bot/
├── cogs/
│   ├── admin.py          # Administrative commands
│   ├── company.py        # Company management & loans
│   ├── economy.py        # Core economy commands
│   ├── events.py         # Event handlers & background tasks
│   └── market.py         # Stock market commands
├── database.py           # Database operations & schema
├── main.py              # Bot entry point & API server
├── safety.py            # Security, rate limiting, audit logging
├── views.py             # Discord UI components (buttons, selects, modals)
├── stock_api.py         # Standalone API server
├── requirements.txt     # Python dependencies
├── .env                 # Environment variables (ignored)
├── .gitignore          # Git ignore file
├── README.md           # This file
├── LICENSE             # MIT License
└── CONTRIBUTING.md     # Contribution guidelines
```

---

## 🗄️ Database Schema

### Core Tables
| Table | Purpose |
|-------|---------|
| `users` | User balances, message counts, debt |
| `parties` | Entities (companies & parties) |
| `shares` | Share ownership tracking |
| `market_orders` | Active buy/sell orders |
| `stock_history` | Price history for charts |

### Company Tables
| Table | Purpose |
|-------|---------|
| `company_investments` | Investment tracking |
| `company_paychecks` | Employee salary configuration |
| `company_votes` | Shareholder votes |
| `board_members` | Elected board members |
| `board_elections` | Election management |
| `board_votes` | Vote records |

### Loan Tables
| Table | Purpose |
|-------|---------|
| `loan_requests` | Loan applications |
| `loan_payments` | Repayment history |

### Safety Tables
| Table | Purpose |
|-------|---------|
| `audit_log` | Administrative action logs |
| `transaction_log` | Financial transaction logs |
| `rate_limit_log` | Rate limit events |
| `user_safety_flags` | Suspensions and trust scores |

---

## 🛡️ Security Features

### Rate Limiting
```python
# Configuration in safety.py
RATE_LIMITS = {
    "default": {"max_requests": 10, "time_window": 60},
    "financial": {"max_requests": 5, "time_window": 60},
    "admin": {"max_requests": 20, "time_window": 60},
}
```

### Transaction Safety
- ACID-compliant database transactions
- Automatic rollback on failure
- Transaction timeouts
- Step-by-step commit tracking

### Input Validation
- Sanitization of all user inputs
- Length limits on all fields
- Type enforcement
- Pattern validation

### Audit Logging
- All admin actions logged
- Financial transactions tracked
- Suspension events recorded
- Discord channel notifications

---

## 🧪 Development

### Testing
```bash
# Install test dependencies
pip install pytest pytest-asyncio black flake8

# Run tests
pytest tests/

# Lint code
flake8 cogs/
black --check .
```

### Pre-commit Hooks
```bash
# Install pre-commit
pip install pre-commit
pre-commit install

# Run manually
pre-commit run --all-files
```

---

## 📊 Performance

- **Database**: SQLite with connection pooling
- **Background Tasks**: Async loops for market updates and paychecks
- **Rate Limiting**: In-memory with periodic cleanup
- **API**: Async aiohttp server with CORS support

### Benchmarks (Approximate)
| Operation | Latency |
|-----------|---------|
| Buy Order | 150ms |
| Market Update | 200ms |
| API Request | 50ms |
| Database Query | 25ms |

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Guidelines
- Follow PEP 8 style guide
- Write docstrings for all functions
- Add type hints
- Update documentation
- Include tests for new features

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- [discord.py](https://github.com/Rapptz/discord.py) - Discord API wrapper
- [matplotlib](https://matplotlib.org/) - Stock chart generation
- [aiohttp](https://docs.aiohttp.org/) - Async HTTP server

---

<div align="center">

**Made with ❤️ for the Discord community**

[Report Bug](https://github.com/bisifan8-dev/owlia-economy-bot/issues) · [Request Feature](https://github.com/bisifan8-dev/owlia-economy-bot/issues)

</div>

test
