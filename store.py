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
CREATE TABLE IF NOT EXISTS rank_history (
    product_id TEXT, rank INTEGER, captured_at TEXT,
    PRIMARY KEY (product_id, captured_at)
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY, v TEXT
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


def record_ranks(con, items, at):
    """판매 순위 이력. '인기 급상승' 판정의 근거."""
    for it in items:
        r = it.get("rank")
        if it.get("product_id") and r and r < 900:
            con.execute("INSERT OR IGNORE INTO rank_history VALUES (?,?,?)",
                        (it["product_id"], int(r), at))
    con.commit()


def rank_rise(con, pid, cur_rank, days=3):
    """(이전 최악 순위, 상승폭). 이력이 없으면 (None, 0).
       예: 3일 전 62위 -> 지금 9위  =>  (62, 53)"""
    row = con.execute("""SELECT MAX(rank) FROM rank_history WHERE product_id=?
        AND captured_at >= datetime('now', ?)""", (pid, f"-{days} days")).fetchone()
    if not row or row[0] is None:
        return None, 0
    prev = int(row[0])
    return prev, max(0, prev - int(cur_rank))


def get_meta(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def set_meta(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))
    con.commit()


def prune(con, days):
    """오래된 이력 삭제 — DB가 무한정 커지는 걸 막는다."""
    con.execute("DELETE FROM price_history WHERE captured_at < datetime('now', ?)",
                (f"-{days} days",))
    con.execute("DELETE FROM judgments WHERE captured_at < datetime('now', ?)",
                (f"-{days} days",))
    con.execute("DELETE FROM alerts_sent WHERE sent_at < datetime('now', '-7 days')")
    con.commit()


def hour_bucket(at: str) -> str:
    """'2026-08-13 14:37:02' -> '2026-08-13 14:00:00'
       20분마다 돌아도 시간당 1건만 남겨 DB 폭증을 막는다."""
    return at[:13] + ":00:00"


def ref_price(con, pid, days=14, min_points=2):
    """표시용 비교가. 최근 최고가를 쓴다 — 표본 2개만 있어도 나온다.
       (판정용 중앙값과 달리, 사람 눈에 보여줄 '원래 이 값이었다'는 숫자)"""
    rows = con.execute("""SELECT price FROM price_history WHERE product_id=?
        AND captured_at >= datetime('now', ?)""", (pid, f"-{days} days")).fetchall()
    prices = [r[0] for r in rows]
    if len(prices) < min_points:
        return None, 0
    return max(prices), len(prices)


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
