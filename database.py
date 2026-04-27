"""
DB 초기화 및 모델 정의 - ALLBILLV2 필드 반영
"""
import sqlite3
import logging
from pathlib import Path
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS bills (
            bill_id         TEXT PRIMARY KEY,
            bill_no         TEXT,
            bill_name       TEXT NOT NULL,
            bill_kind       TEXT,
            proposer        TEXT,
            proposer_kind   TEXT,
            propose_dt      TEXT,
            propose_sess    TEXT,
            committee       TEXT,
            committee_proc_result  TEXT,
            committee_proc_dt      TEXT,
            proc_result     TEXT,
            proc_dt         TEXT,
            detail_link     TEXT,
            raw_json        TEXT,
            first_seen      TEXT NOT NULL,
            last_updated    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bill_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id         TEXT NOT NULL,
            changed_at      TEXT NOT NULL,
            field_name      TEXT NOT NULL,
            old_value       TEXT,
            new_value       TEXT,
            FOREIGN KEY (bill_id) REFERENCES bills(bill_id)
        );

        CREATE TABLE IF NOT EXISTS collect_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at    TEXT NOT NULL,
            keyword         TEXT NOT NULL,
            total_fetched   INTEGER DEFAULT 0,
            new_bills       INTEGER DEFAULT 0,
            updated_bills   INTEGER DEFAULT 0,
            error_msg       TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_bills_propose_dt      ON bills(propose_dt);
        CREATE INDEX IF NOT EXISTS idx_bills_proc_result     ON bills(proc_result);
        CREATE INDEX IF NOT EXISTS idx_bills_bill_kind       ON bills(bill_kind);
        CREATE INDEX IF NOT EXISTS idx_history_bill_id       ON bill_history(bill_id);
        """)
    logger.info("DB 초기화 완료: %s", DB_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("DB 초기화 완료!")
