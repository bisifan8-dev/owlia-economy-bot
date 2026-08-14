# database.py - Full file with fixed execute_trade

import sqlite3
import datetime

DB_NAME = "server_economy.db"


def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                designated_channels TEXT,
                board_channel_id INTEGER,
                buy_channel_id INTEGER,
                sell_channel_id INTEGER,
                board_msg_id INTEGER,
                buy_msg_id INTEGER,
                sell_msg_id INTEGER,
                paid_channel_id INTEGER,
                bids_channel_id INTEGER,
                loans_channel_id INTEGER,
                loans_msg_id INTEGER,
                audit_channel_id INTEGER
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                premium_credits INTEGER DEFAULT 0,
                debt REAL DEFAULT 0
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS parties (
                party_id TEXT PRIMARY KEY,
                name TEXT,
                treasury REAL DEFAULT 50.0,
                total_shares REAL DEFAULT 20.0,
                total_messages INTEGER DEFAULT 0,
                tax_enabled INTEGER DEFAULT 0,
                tax_percentage REAL DEFAULT 0.0,
                role_id TEXT,
                manager_role_id TEXT,
                is_setup INTEGER DEFAULT 1,
                is_company INTEGER DEFAULT 0,
                structure_type TEXT DEFAULT 'party',
                vote_interval_days INTEGER DEFAULT 7,
                category_id INTEGER DEFAULT 0,
                chat_channel_id INTEGER DEFAULT 0,
                vote_channel_id INTEGER DEFAULT 0,
                creation_pending INTEGER DEFAULT 0,
                initial_invested REAL DEFAULT 0.0,
                max_positions INTEGER DEFAULT 1
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_investments (
                company_id TEXT,
                user_id INTEGER,
                amount REAL,
                PRIMARY KEY (company_id, user_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_paychecks (
                company_id TEXT,
                user_id INTEGER,
                salary REAL DEFAULT 0.0,
                PRIMARY KEY (company_id, user_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_votes (
                vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT,
                vote_type TEXT,
                description TEXT,
                shares_to_create REAL DEFAULT 0,
                sell_price_per_share REAL DEFAULT 0,
                yes_votes REAL DEFAULT 0.0,
                no_votes REAL DEFAULT 0.0,
                status TEXT DEFAULT 'OPEN',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS company_ballots (
                vote_id INTEGER,
                user_id INTEGER,
                vote_choice TEXT,
                weight REAL,
                PRIMARY KEY (vote_id, user_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS board_elections (
                election_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT,
                seat_number INTEGER,
                status TEXT DEFAULT 'NOMINATION',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                nomination_end TIMESTAMP,
                voting_end TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS board_candidates (
                candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id INTEGER,
                user_id INTEGER,
                nomination_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(election_id) REFERENCES board_elections(election_id),
                UNIQUE(election_id, user_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS board_votes (
                vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id INTEGER,
                candidate_id INTEGER,
                user_id INTEGER,
                weight REAL,
                company_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(election_id) REFERENCES board_elections(election_id),
                FOREIGN KEY(candidate_id) REFERENCES board_candidates(candidate_id),
                UNIQUE(election_id, user_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS board_members (
                company_id TEXT,
                user_id INTEGER,
                seat_number INTEGER,
                elected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (company_id, seat_number)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                party_id TEXT,
                price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS loan_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT,
                user_id INTEGER,
                amount REAL,
                duration_hours INTEGER,
                interest_rate REAL DEFAULT 0.0,
                status TEXT DEFAULT 'PENDING',
                request_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_time TIMESTAMP,
                due_time TIMESTAMP,
                approved_by INTEGER,
                FOREIGN KEY(company_id) REFERENCES parties(party_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS loan_payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                user_id INTEGER,
                amount REAL,
                payment_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(request_id) REFERENCES loan_requests(request_id)
            )
        """
        )

        # Create transaction_log with all columns from the start
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                transaction_type TEXT,
                amount REAL,
                description TEXT,
                party_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # ─── SHOUT SYSTEM TABLES ──────────────────────────────────────────────
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shout_log (
                shout_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                guild_id INTEGER,
                message TEXT,
                cost REAL DEFAULT 500.0,
                total_targeted INTEGER DEFAULT 0,
                total_sent INTEGER DEFAULT 0,
                total_failed INTEGER DEFAULT 0,
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                cooldown_until TIMESTAMP,
                strike_window_end TIMESTAMP,
                strike_reason TEXT,
                struck_by INTEGER,
                include_bots INTEGER DEFAULT 0,
                entity_id TEXT,
                entity_name TEXT,
                is_company_shout INTEGER DEFAULT 0
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shout_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shout_id INTEGER,
                user_id INTEGER,
                status TEXT DEFAULT 'SENT',
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(shout_id) REFERENCES shout_log(shout_id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shout_blacklist (
                user_id INTEGER PRIMARY KEY,
                opted_out_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                refund_amount REAL DEFAULT 20.0
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shout_cooldowns (
                user_id INTEGER PRIMARY KEY,
                last_shout_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shout_entity_cooldowns (
                entity_id TEXT PRIMARY KEY,
                last_shout_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # ─── SCHEMA MIGRATION CHECKS ──────────────────────────────────────────

        # Schema Migration Checks - Parties Table
        cursor.execute("PRAGMA table_info(parties)")
        columns = [col["name"] for col in cursor.fetchall()]
        if "role_id" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN role_id TEXT")
        if "manager_role_id" not in columns:
            cursor.execute(
                "ALTER TABLE parties ADD COLUMN manager_role_id TEXT"
            )
        if "is_setup" not in columns:
            cursor.execute(
                "ALTER TABLE parties ADD COLUMN is_setup INTEGER DEFAULT 1"
            )
        if "is_company" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN is_company INTEGER DEFAULT 0")
        if "structure_type" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN structure_type TEXT DEFAULT 'party'")
        if "vote_interval_days" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN vote_interval_days INTEGER DEFAULT 7")
        if "category_id" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN category_id INTEGER DEFAULT 0")
        if "chat_channel_id" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN chat_channel_id INTEGER DEFAULT 0")
        if "vote_channel_id" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN vote_channel_id INTEGER DEFAULT 0")
        if "creation_pending" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN creation_pending INTEGER DEFAULT 0")
        if "initial_invested" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN initial_invested REAL DEFAULT 0.0")
        if "max_positions" not in columns:
            cursor.execute("ALTER TABLE parties ADD COLUMN max_positions INTEGER DEFAULT 1")

        # Schema Migration Checks - Guild Config Table
        cursor.execute("PRAGMA table_info(guild_config)")
        g_cols = [col["name"] for col in cursor.fetchall()]
        if "paid_channel_id" not in g_cols:
            cursor.execute(
                "ALTER TABLE guild_config ADD COLUMN paid_channel_id INTEGER"
            )
        if "bids_channel_id" not in g_cols:
            cursor.execute(
                "ALTER TABLE guild_config ADD COLUMN bids_channel_id INTEGER"
            )
        if "loans_channel_id" not in g_cols:
            cursor.execute(
                "ALTER TABLE guild_config ADD COLUMN loans_channel_id INTEGER"
            )
        if "loans_msg_id" not in g_cols:
            cursor.execute(
                "ALTER TABLE guild_config ADD COLUMN loans_msg_id INTEGER"
            )

        # Schema Migration Checks - Users Table
        cursor.execute("PRAGMA table_info(users)")
        u_cols = [col["name"] for col in cursor.fetchall()]
        if "debt" not in u_cols:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN debt REAL DEFAULT 0"
            )

        # Schema Migration Checks - Board Votes Table (add company_id if missing)
        cursor.execute("PRAGMA table_info(board_votes)")
        bv_cols = [col["name"] for col in cursor.fetchall()]
        if "company_id" not in bv_cols:
            cursor.execute(
                "ALTER TABLE board_votes ADD COLUMN company_id TEXT"
            )

        # Schema Migration Checks - Shout Tables (ensure all columns exist)
        cursor.execute("PRAGMA table_info(shout_log)")
        shout_cols = [col["name"] for col in cursor.fetchall()]
        if "completed_at" not in shout_cols:
            cursor.execute("ALTER TABLE shout_log ADD COLUMN completed_at TIMESTAMP")
        if "cooldown_until" not in shout_cols:
            cursor.execute("ALTER TABLE shout_log ADD COLUMN cooldown_until TIMESTAMP")
        if "entity_id" not in shout_cols:
            cursor.execute("ALTER TABLE shout_log ADD COLUMN entity_id TEXT")
        if "entity_name" not in shout_cols:
            cursor.execute("ALTER TABLE shout_log ADD COLUMN entity_name TEXT")
        if "is_company_shout" not in shout_cols:
            cursor.execute("ALTER TABLE shout_log ADD COLUMN is_company_shout INTEGER DEFAULT 0")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS party_member_stats (
                user_id INTEGER,
                party_id TEXT,
                messages_sent INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, party_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS shares (
                user_id INTEGER,
                party_id TEXT,
                shares_owned REAL DEFAULT 0,
                PRIMARY KEY (user_id, party_id)
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS market_orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                party_id TEXT,
                order_type TEXT,
                shares_count REAL,
                price_per_share REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS counter_offers (
                offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER,
                user_id INTEGER,
                price REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(order_id) REFERENCES market_orders(order_id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS barred_users (
                user_id INTEGER,
                party_id TEXT,
                PRIMARY KEY (user_id, party_id)
            )
        """
        )

        # Seed initial history points
        cursor.execute("SELECT party_id, treasury, total_shares FROM parties")
        existing_parties = cursor.fetchall()
        now = datetime.datetime.utcnow()
        for p in existing_parties:
            cursor.execute(
                "SELECT COUNT(*) as count FROM stock_history WHERE party_id = ?",
                (p["party_id"],),
            )
            cnt = cursor.fetchone()["count"]
            if cnt == 0:
                price = p["treasury"] / p["total_shares"] if p["total_shares"] > 0 else 0.0
                old_time = now - datetime.timedelta(days=120)
                cursor.execute(
                    "INSERT INTO stock_history (party_id, price, timestamp) VALUES (?, ?, ?)",
                    (p["party_id"], price, old_time.strftime("%Y-%m-%d %H:%M:%S")),
                )
                cursor.execute(
                    "INSERT INTO stock_history (party_id, price, timestamp) VALUES (?, ?, ?)",
                    (p["party_id"], price, now.strftime("%Y-%m-%d %H:%M:%S")),
                )

        conn.commit()


def get_user_managed_parties(member):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM parties")
        all_parties = cursor.fetchall()

    managed = []
    user_role_ids = {str(r.id) for r in member.roles}

    for p in all_parties:
        if member.guild_permissions.administrator or (
            p["manager_role_id"] and p["manager_role_id"] in user_role_ids
        ):
            managed.append(p)
    return managed


def get_party_share_distribution(party_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_shares FROM parties WHERE party_id = ?",
            (party_id,),
        )
        party = cursor.fetchone()
        if not party:
            return 0.0, 0.0, 0.0

        total_shares = party["total_shares"]

        cursor.execute(
            "SELECT SUM(shares_owned) AS h_shares FROM shares WHERE party_id = ?",
            (party_id,),
        )
        row = cursor.fetchone()
        human_shares = row["h_shares"] if row and row["h_shares"] else 0.0

        unissued_shares = max(0.0, total_shares - human_shares)
        return total_shares, human_shares, unissued_shares


def get_user_share_weight(user_id: int, company_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ?",
            (user_id, company_id)
        )
        row = cursor.fetchone()
        return row["shares_owned"] if row else 0.0


def get_company_board_members(company_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, seat_number FROM board_members WHERE company_id = ? ORDER BY seat_number",
            (company_id,)
        )
        return cursor.fetchall()


def get_company_ceo(company_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id FROM board_members WHERE company_id = ? AND seat_number = 0",
            (company_id,)
        )
        return cursor.fetchone()


def is_company_shareholder(user_id: int, company_id: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT shares_owned FROM shares WHERE user_id = ? AND party_id = ? AND shares_owned > 0",
            (user_id, company_id)
        )
        return cursor.fetchone() is not None


def get_user_loans(user_id: int, status: str = None):
    with get_db() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                """
                SELECT l.*, p.name as company_name
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.user_id = ? AND l.status = ?
                ORDER BY l.request_time DESC
                """,
                (user_id, status)
            )
        else:
            cursor.execute(
                """
                SELECT l.*, p.name as company_name
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.user_id = ?
                ORDER BY l.request_time DESC
                """,
                (user_id,)
            )
        return cursor.fetchall()


def get_company_loans(company_id: str, status: str = None):
    with get_db() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                """
                SELECT l.*, p.name as company_name
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.company_id = ? AND l.status = ?
                ORDER BY l.request_time DESC
                """,
                (company_id, status)
            )
        else:
            cursor.execute(
                """
                SELECT l.*, p.name as company_name
                FROM loan_requests l
                JOIN parties p ON l.company_id = p.party_id
                WHERE l.company_id = ?
                ORDER BY l.request_time DESC
                """,
                (company_id,)
            )
        return cursor.fetchall()


def get_user_debt(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT debt FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return row["debt"] if row else 0.0


def check_user_can_buy(user_id: int) -> bool:
    """Check if user has debt that prevents them from buying."""
    debt = get_user_debt(user_id)
    return debt <= 0.001  # Allow small floating point errors


def get_user_transactions(user_id: int, limit: int = 50, offset: int = 0):
    """Get user's transaction history with pagination."""
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
            (user_id, limit, offset)
        )
        return cursor.fetchall()


def get_transaction_count(user_id: int) -> int:
    """Get total number of transactions for a user."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as total FROM transaction_log WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        return row["total"] if row else 0


async def execute_trade(
    conn,
    cursor,
    buyer_id,
    seller_id,
    party_id,
    shares,
    trade_price,
    order_type,
    order_id,
):
    total_cost = trade_price * shares

    # 1. Fetch entity metrics
    cursor.execute(
        "SELECT treasury, total_shares, is_company FROM parties WHERE party_id = ?",
        (party_id,),
    )
    party = cursor.fetchone()

    standard_cost = (
        (party["treasury"] / party["total_shares"])
        if party and party["total_shares"] > 0
        else 0.0
    )

    # 2. Transfer Funds & Shares Between Buyer/Seller
    if order_type == "SELL":
        cursor.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (total_cost, buyer_id),
        )
        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (total_cost, seller_id),
        )

        cursor.execute(
            "UPDATE shares SET shares_owned = shares_owned - ? WHERE user_id = ? AND party_id = ?",
            (shares, seller_id, party_id),
        )
        cursor.execute(
            """
            INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
            ON CONFLICT(user_id, party_id) DO UPDATE SET shares_owned = shares_owned + ?
        """,
            (buyer_id, party_id, shares, shares),
        )
    else:  # "BUY" Order Execution
        cursor.execute(
            "SELECT price_per_share FROM market_orders WHERE order_id = ?",
            (order_id,),
        )
        orig_order = cursor.fetchone()
        orig_price = (
            orig_order["price_per_share"] if orig_order else trade_price
        )
        orig_cost = orig_price * shares

        cursor.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (total_cost, seller_id),
        )

        if orig_cost > total_cost:
            refund = orig_cost - total_cost
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (refund, buyer_id),
            )
        elif total_cost > orig_cost:
            extra = total_cost - orig_cost
            cursor.execute(
                "UPDATE users SET balance = balance - ? WHERE user_id = ?",
                (extra, buyer_id),
            )

        cursor.execute(
            "UPDATE shares SET shares_owned = shares_owned - ? WHERE user_id = ? AND party_id = ?",
            (shares, seller_id, party_id),
        )
        cursor.execute(
            """
            INSERT INTO shares (user_id, party_id, shares_owned) VALUES (?, ?, ?)
            ON CONFLICT(user_id, party_id) DO UPDATE SET shares_owned = shares_owned + ?
        """,
            (buyer_id, party_id, shares, shares),
        )

    # 3. Apply Treasury shift based on price difference from standard cost
    # *** FIXED: Apply to BOTH companies AND parties ***
    price_diff_per_share = trade_price - standard_cost
    total_treasury_shift = price_diff_per_share * shares
    
    if total_treasury_shift != 0:
        new_treasury = max(0.0, party["treasury"] + total_treasury_shift)
        cursor.execute(
            "UPDATE parties SET treasury = ? WHERE party_id = ?",
            (new_treasury, party_id),
        )
    
    # Track stock price in history
    cursor.execute("SELECT treasury, total_shares FROM parties WHERE party_id = ?", (party_id,))
    latest_p = cursor.fetchone()
    new_price = latest_p["treasury"] / latest_p["total_shares"] if latest_p["total_shares"] > 0 else 0.0
    cursor.execute(
        "INSERT INTO stock_history (party_id, price) VALUES (?, ?)",
        (party_id, new_price),
    )

    # 4. Clean up order & counter-offers
    cursor.execute(
        "DELETE FROM market_orders WHERE order_id = ?", (order_id,)
    )
    cursor.execute(
        "DELETE FROM counter_offers WHERE order_id = ?", (order_id,)
    )