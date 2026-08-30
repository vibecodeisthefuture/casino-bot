from collections.abc import Callable
import logging
import random
import shutil
import sqlite3
from pathlib import Path
from typing import Tuple, List

from app.config import config

Entry = Tuple[int, int, int]
DATABASE_PATH = Path(config.storage.database_path)
LEGACY_DATABASE_PATH = Path(__file__).resolve().parents[3] / "economy.db"
SCHEMA_VERSION = 4

logger = logging.getLogger(__name__)


def _migration_1_create_economy(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS economy (
        user_id INTEGER NOT NULL PRIMARY KEY,
        money INTEGER NOT NULL DEFAULT 0,
        credits INTEGER NOT NULL DEFAULT 0
    )"""
    )


def _migration_2_add_indexes(cur: sqlite3.Cursor) -> None:
    cur.execute("CREATE INDEX IF NOT EXISTS idx_economy_money ON economy(money DESC)")


def _migration_3_create_daily_claims(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_claims (
        user_id INTEGER NOT NULL PRIMARY KEY,
        last_claim INTEGER NOT NULL DEFAULT 0
    )"""
    )


def _migration_4_create_market(cur: sqlite3.Cursor) -> None:
    cur.execute("ALTER TABLE economy ADD COLUMN bitcoin INTEGER NOT NULL DEFAULT 0")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS market_roles (
        role_id INTEGER NOT NULL PRIMARY KEY,
        price INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'money'
    )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS market_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        bitcoin_price INTEGER NOT NULL DEFAULT 0,
        bitcoin_updated INTEGER NOT NULL DEFAULT 0
    )"""
    )
    cur.execute(
        "INSERT OR IGNORE INTO market_state(id, bitcoin_price, bitcoin_updated) "
        "VALUES(1, 0, 0)"
    )


MIGRATIONS: dict[int, Callable[[sqlite3.Cursor], None]] = {
    1: _migration_1_create_economy,
    2: _migration_2_add_indexes,
    3: _migration_3_create_daily_claims,
    4: _migration_4_create_market,
}


class Economy:
    """A wrapper for the economy database"""

    def __init__(self):
        self.open()

    def open(self):
        """Initializes the database"""
        if (
            DATABASE_PATH != LEGACY_DATABASE_PATH
            and not DATABASE_PATH.exists()
            and LEGACY_DATABASE_PATH.exists()
        ):
            DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LEGACY_DATABASE_PATH, DATABASE_PATH)
            logger.info(
                "Copied legacy economy database from %s to %s",
                LEGACY_DATABASE_PATH,
                DATABASE_PATH,
            )
        self.conn = sqlite3.connect(str(DATABASE_PATH), timeout=30)
        self.cur = self.conn.cursor()
        self._run_migrations()
        self.conn.commit()

    def _run_migrations(self) -> None:
        self.cur.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        )"""
        )
        self.cur.execute(
            "INSERT OR IGNORE INTO schema_version(id, version) VALUES(1, 0)"
        )
        self.cur.execute("SELECT version FROM schema_version WHERE id=1")
        row = self.cur.fetchone()
        current_version = int(row[0]) if row else 0

        if current_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than supported {SCHEMA_VERSION}."
            )

        for target_version in range(current_version + 1, SCHEMA_VERSION + 1):
            migration = MIGRATIONS.get(target_version)
            if migration is None:
                raise RuntimeError(
                    f"Missing migration for schema version {target_version}."
                )
            migration(self.cur)
            self.cur.execute(
                "UPDATE schema_version SET version=? WHERE id=1",
                (target_version,),
            )
            logger.info("Applied economy database migration version=%s", target_version)

    def close(self):
        """Safely closes the database"""
        if getattr(self, "conn", None):
            self.conn.commit()
            if getattr(self, "cur", None):
                self.cur.close()
            self.conn.close()
            self.cur = None
            self.conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _ensure_entry(self, user_id: int) -> None:
        self.cur.execute(
            "INSERT OR IGNORE INTO economy(user_id, money, credits) VALUES(?, ?, ?)",
            (user_id, 0, 0),
        )

    def _fetch_entry(self, user_id: int) -> Entry:
        self.cur.execute(
            "SELECT user_id, money, credits FROM economy WHERE user_id=?",
            (user_id,),
        )
        result = self.cur.fetchone()
        if result is None:
            raise RuntimeError(f"failed to fetch economy entry for user_id={user_id}")
        return result

    def get_entry(self, user_id: int) -> Entry:
        self._ensure_entry(user_id)
        self.conn.commit()
        return self._fetch_entry(user_id)

    def new_entry(self, user_id: int) -> Entry:
        self._ensure_entry(user_id)
        self.conn.commit()
        return self._fetch_entry(user_id)

    def remove_entry(self, user_id: int) -> None:
        self.cur.execute("DELETE FROM economy WHERE user_id=?", (user_id,))
        self.conn.commit()

    def set_money(self, user_id: int, money: int) -> Entry:
        money = max(0, int(money))
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET money=? WHERE user_id=?",
            (money, user_id),
        )
        self.conn.commit()
        return self._fetch_entry(user_id)

    def set_credits(self, user_id: int, credits: int) -> Entry:
        credits = max(0, int(credits))
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET credits=? WHERE user_id=?",
            (credits, user_id),
        )
        self.conn.commit()
        return self._fetch_entry(user_id)

    def add_money(self, user_id: int, money_to_add: int) -> Entry:
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET money=MAX(0, money + ?) WHERE user_id=?",
            (int(money_to_add), user_id),
        )
        self.conn.commit()
        return self._fetch_entry(user_id)

    def add_credits(self, user_id: int, credits_to_add: int) -> Entry:
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET credits=MAX(0, credits + ?) WHERE user_id=?",
            (int(credits_to_add), user_id),
        )
        self.conn.commit()
        return self._fetch_entry(user_id)

    def get_last_daily(self, user_id: int) -> int:
        self.cur.execute(
            "SELECT last_claim FROM daily_claims WHERE user_id=?",
            (user_id,),
        )
        row = self.cur.fetchone()
        return int(row[0]) if row else 0

    def set_last_daily(self, user_id: int, timestamp: int) -> None:
        self.cur.execute(
            "INSERT INTO daily_claims(user_id, last_claim) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_claim=excluded.last_claim",
            (user_id, int(timestamp)),
        )
        self.conn.commit()

    def transfer_money(
        self, sender_id: int, recipient_id: int, amount: int
    ) -> tuple[Entry, Entry]:
        """Atomically move ``amount`` money from sender to recipient.

        Raises ValueError for a non-positive amount or a self-transfer, and
        RuntimeError if the sender has insufficient funds. Both balance updates
        commit together or not at all.
        """
        amount = int(amount)
        if amount <= 0:
            raise ValueError("transfer amount must be positive")
        if sender_id == recipient_id:
            raise ValueError("cannot transfer to yourself")
        self._ensure_entry(sender_id)
        self._ensure_entry(recipient_id)
        self.conn.commit()
        try:
            self.cur.execute(
                "UPDATE economy SET money = money - ? WHERE user_id=? AND money >= ?",
                (amount, sender_id, amount),
            )
            if self.cur.rowcount != 1:
                self.conn.rollback()
                raise RuntimeError("insufficient funds")
            self.cur.execute(
                "UPDATE economy SET money = money + ? WHERE user_id=?",
                (amount, recipient_id),
            )
            self.conn.commit()
        except RuntimeError:
            raise
        except Exception:
            self.conn.rollback()
            raise
        return self._fetch_entry(sender_id), self._fetch_entry(recipient_id)

    def get_bitcoin(self, user_id: int) -> int:
        self._ensure_entry(user_id)
        self.cur.execute("SELECT bitcoin FROM economy WHERE user_id=?", (user_id,))
        row = self.cur.fetchone()
        return int(row[0]) if row else 0

    def add_bitcoin(self, user_id: int, amount: int) -> None:
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET bitcoin = MAX(0, bitcoin + ?) WHERE user_id=?",
            (int(amount), user_id),
        )
        self.conn.commit()

    def debit_money(self, user_id: int, amount: int) -> bool:
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET money = money - ? WHERE user_id=? AND money >= ?",
            (int(amount), user_id, int(amount)),
        )
        ok = self.cur.rowcount == 1
        self.conn.commit()
        return ok

    def debit_bitcoin(self, user_id: int, amount: int) -> bool:
        self._ensure_entry(user_id)
        self.cur.execute(
            "UPDATE economy SET bitcoin = bitcoin - ? WHERE user_id=? AND bitcoin >= ?",
            (int(amount), user_id, int(amount)),
        )
        ok = self.cur.rowcount == 1
        self.conn.commit()
        return ok

    def buy_bitcoin(self, user_id: int, count: int, unit_price: int) -> int:
        """Atomically spend ``count * unit_price`` money for ``count`` bitcoin.

        Returns the total cost. Raises ValueError (bad count) or RuntimeError
        (market unavailable / insufficient funds).
        """
        count = int(count)
        unit_price = int(unit_price)
        if count <= 0:
            raise ValueError("count must be positive")
        if unit_price <= 0:
            raise RuntimeError("market unavailable")
        cost = count * unit_price
        self._ensure_entry(user_id)
        self.conn.commit()
        try:
            self.cur.execute(
                "UPDATE economy SET money = money - ? WHERE user_id=? AND money >= ?",
                (cost, user_id, cost),
            )
            if self.cur.rowcount != 1:
                self.conn.rollback()
                raise RuntimeError("insufficient funds")
            self.cur.execute(
                "UPDATE economy SET bitcoin = bitcoin + ? WHERE user_id=?",
                (count, user_id),
            )
            self.conn.commit()
        except RuntimeError:
            raise
        except Exception:
            self.conn.rollback()
            raise
        return cost

    def sell_bitcoin(self, user_id: int, count: int, unit_price: int) -> int:
        """Atomically sell ``count`` bitcoin for ``count * unit_price`` money.

        Returns the total payout. Raises ValueError (bad count) or RuntimeError
        (market unavailable / insufficient bitcoin).
        """
        count = int(count)
        unit_price = int(unit_price)
        if count <= 0:
            raise ValueError("count must be positive")
        if unit_price <= 0:
            raise RuntimeError("market unavailable")
        payout = count * unit_price
        self._ensure_entry(user_id)
        self.conn.commit()
        try:
            self.cur.execute(
                "UPDATE economy SET bitcoin = bitcoin - ? WHERE user_id=? AND bitcoin >= ?",
                (count, user_id, count),
            )
            if self.cur.rowcount != 1:
                self.conn.rollback()
                raise RuntimeError("insufficient bitcoin")
            self.cur.execute(
                "UPDATE economy SET money = money + ? WHERE user_id=?",
                (payout, user_id),
            )
            self.conn.commit()
        except RuntimeError:
            raise
        except Exception:
            self.conn.rollback()
            raise
        return payout

    def set_market_role(self, role_id: int, price: int, currency: str = "money") -> None:
        self.cur.execute(
            "INSERT INTO market_roles(role_id, price, currency) VALUES(?, ?, ?) "
            "ON CONFLICT(role_id) DO UPDATE SET price=excluded.price, "
            "currency=excluded.currency",
            (int(role_id), int(price), currency),
        )
        self.conn.commit()

    def remove_market_role(self, role_id: int) -> bool:
        self.cur.execute("DELETE FROM market_roles WHERE role_id=?", (int(role_id),))
        removed = self.cur.rowcount > 0
        self.conn.commit()
        return removed

    def get_market_role(self, role_id: int):
        self.cur.execute(
            "SELECT role_id, price, currency FROM market_roles WHERE role_id=?",
            (int(role_id),),
        )
        return self.cur.fetchone()

    def list_market_roles(self):
        self.cur.execute(
            "SELECT role_id, price, currency FROM market_roles ORDER BY price ASC"
        )
        return self.cur.fetchall()

    def get_bitcoin_price(self) -> int:
        self.cur.execute("SELECT bitcoin_price FROM market_state WHERE id=1")
        row = self.cur.fetchone()
        return int(row[0]) if row else 0

    def get_bitcoin_updated(self) -> int:
        self.cur.execute("SELECT bitcoin_updated FROM market_state WHERE id=1")
        row = self.cur.fetchone()
        return int(row[0]) if row else 0

    def set_bitcoin_price(self, price: int, updated: int) -> None:
        self.cur.execute(
            "UPDATE market_state SET bitcoin_price=?, bitcoin_updated=? WHERE id=1",
            (int(price), int(updated)),
        )
        self.conn.commit()

    def random_entry(self) -> Entry:
        self.cur.execute("SELECT * FROM economy")
        entries = self.cur.fetchall()
        if not entries:
            raise RuntimeError("economy has no entries")
        return random.choice(entries)

    def top_entries(self, n: int = 0) -> List[Entry]:
        self.cur.execute("SELECT * FROM economy ORDER BY money DESC")
        return (self.cur.fetchmany(n) if n else self.cur.fetchall())
