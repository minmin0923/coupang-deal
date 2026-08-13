"""SQLite 저장소: 가격 히스토리 + 판정 로그(튜닝의 유일한 근거)"""
import sqlite3, statistics
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "prices.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY, name TEXT, url TEXT, image TEXT,
    category_id INTEGER, category_name TEXT, is_rocket INTEGER,
    first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS price_history (
    product_id TEXT, price INTEGER, captured_at TEXT,
    PRIMARY KEY (product_id, captured_at)
);
CREATE TABLE IF NOT EXISTS judgments (
    product_id TEXT, captured_at TEXT, name TEXT, category_name TEXT,
    price INTEGER, p_ref INTEGER, ratio REAL, saving INTEGER,
    score INTEGER, grade TEXT, digit_error INTEGER,
    keyword TEXT, reject_reason TEXT,
    PRIMARY KEY (product_id, captured_at)
);
CREATE TABLE IF NOT EXISTS alerts_sent (
    product_id TEXT, price INTEGER, sent_at TEXT,
    PRIMARY KEY (product_id, price)
);
CREATE INDEX IF NOT EXISTS idx_hist_pid ON price_history(product_id);
CREATE INDEX IF NOT EXISTS idx_jud_time ON judgments(captured_at);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def upsert(con, items, at):
    for it in items:
        if not it["product_id"] or it["price"] <= 0:
            continue
        con.execute("""INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(product_id) DO UPDATE SET
              name=excluded.name, url=excluded.url, image=excluded.image,
              category_id=COALESCE(excluded.category_id, products.category_id),
              last_seen=excluded.last_seen""",
            (it["product_id"], it["name"], it["url"], it["image"],
             it["category_id"], it["category_name"], int(it["is_rocket"]), at, at))
        con.execute("INSERT OR IGNORE INTO price_history VALUES (?,?,?)",
                    (it["product_id"], it["price"], at))
    con.commit()


def log_judgment(con, row):
    con.execute("""INSERT OR REPLACE INTO judgments VALUES
        (:product_id,:captured_at,:name,:category_name,:price,:p_ref,:ratio,
         :saving,:score,:grade,:digit_error,:keyword,:reject_reason)""", row)


def recently_sent(con, pid, price, at):
    r = con.execute("""SELECT 1 FROM alerts_sent WHERE product_id=?
                       AND sent_at >= datetime(?, '-24 hours')""", (pid, at)).fetchone()
    if r:
        return True
    r = con.execute("""SELECT 1 FROM alerts_sent WHERE product_id=? AND price=?
                       AND sent_at >= datetime(?, '-7 days')""", (pid, price, at)).fetchone()
    return bool(r)


def mark_sent(con, pid, price, at):
    con.execute("INSERT OR REPLACE INTO alerts_sent VALUES (?,?,?)", (pid, price, at))
    con.commit()


def s_count_today(con, at):
    return con.execute("""SELECT COUNT(*) FROM judgments
        WHERE grade='S' AND date(captured_at)=date(?)""", (at,)).fetchone()[0]


def baseline_with_count(con, pid, days=30, min_points=6):
    """(중앙값, 표본수). 표본이 모자라면 (None, 표본수)."""
    rows = con.execute("""SELECT price FROM price_history WHERE product_id=?
        AND captured_at >= datetime('now', ?)""", (pid, f"-{days} days")).fetchall()
    prices = [r[0] for r in rows]
    if len(prices) < min_points:
        return None, len(prices)
    return statistics.median(prices), len(prices)


def baseline(con, pid, days=30, min_points=6):
    return baseline_with_count(con, pid, days, min_points)[0]
