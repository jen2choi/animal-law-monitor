"""
국회 의안정보 수집기 - ALLBILLV2 (의안정보 통합 API v2)
"""
import json
import logging
import time
from datetime import datetime

import requests

from config import API_KEY, API_BASE_URL, MAX_PAGES, SEARCH_KEYWORDS
from database import get_conn

logger = logging.getLogger(__name__)

ENDPOINT = f"{API_BASE_URL}/ALLBILLV2"
ERACO = "제22대"


def fetch_bills_page(keyword: str, page: int, page_size: int = 100) -> dict:
    params = {
        "KEY":    API_KEY,
        "Type":   "json",
        "pIndex": page,
        "pSize":  page_size,
        "ERACO":  ERACO,
        "BILL_NM": keyword,
    }
    resp = requests.get(ENDPOINT, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_response(data: dict) -> tuple[list[dict], int]:
    try:
        root = data.get("ALLBILLV2", [])
        if not root or len(root) < 2:
            return [], 0
        head = root[0].get("head", [{}])[0]
        total_count = int(head.get("list_total_count", 0))
        rows = root[1].get("row", [])
        return rows, total_count
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("응답 파싱 오류: %s", e)
        return [], 0


def fetch_all_bills(keyword: str) -> list[dict]:
    all_bills = []
    page = 1
    while page <= MAX_PAGES:
        try:
            data = fetch_bills_page(keyword, page)
            rows, total = parse_response(data)
        except requests.RequestException as e:
            logger.error("API 요청 실패 (keyword=%s, page=%d): %s", keyword, page, e)
            break
        if not rows:
            break
        all_bills.extend(rows)
        logger.info("[%s] page %d / total %d건 → 현재 %d건 수집",
                    keyword, page, total, len(all_bills))
        if len(all_bills) >= total:
            break
        page += 1
        time.sleep(0.3)
    return all_bills


def upsert_bill(conn, row: dict, now: str) -> tuple[bool, bool]:
    bill_id       = row.get("BILL_ID", "")
    bill_no       = row.get("BILL_NO", "")
    bill_name     = row.get("BILL_NM", "")
    bill_kind     = row.get("BILL_KND", "")
    proposer      = row.get("PPSR_NM", "")
    proposer_kind = row.get("PPSR_KND", "")
    propose_dt    = row.get("PPSL_DT", "")
    propose_sess  = row.get("PPSL_SESS", "")
    committee     = row.get("JRCMIT_NM", "")
    cmmt_proc_result = row.get("JRCMIT_PROC_RSLT") or ""
    cmmt_proc_dt     = row.get("JRCMIT_PROC_DT") or ""
    proc_result   = row.get("RGS_CONF_RSLT") or ""
    proc_dt       = row.get("RGS_CONF_DT") or ""
    detail_link   = f"https://likms.assembly.go.kr/bill/billDetail.do?billId={bill_id}" if bill_id else ""
    raw_json      = json.dumps(row, ensure_ascii=False)

    pk = bill_id or bill_no
    if not pk:
        return False, False

    existing = conn.execute(
        "SELECT * FROM bills WHERE bill_id = ?", (pk,)
    ).fetchone()

    if existing is None:
        conn.execute("""
            INSERT INTO bills
              (bill_id, bill_no, bill_name, bill_kind, proposer, proposer_kind,
               propose_dt, propose_sess, committee, committee_proc_result,
               committee_proc_dt, proc_result, proc_dt,
               detail_link, raw_json, first_seen, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (pk, bill_no, bill_name, bill_kind, proposer, proposer_kind,
              propose_dt, propose_sess, committee, cmmt_proc_result,
              cmmt_proc_dt, proc_result, proc_dt,
              detail_link, raw_json, now, now))
        return True, False

    # 변경사항 감지
    field_map = {
        "bill_name":              bill_name,
        "committee":              committee,
        "committee_proc_result":  cmmt_proc_result,
        "proc_result":            proc_result,
    }
    changes = []
    for field, new_val in field_map.items():
        try:
            old_val = existing[field] or ""
        except IndexError:
            old_val = ""
        if new_val and new_val != old_val:
            changes.append((pk, now, field, old_val, new_val))

    if changes:
        for change in changes:
            conn.execute("""
                INSERT INTO bill_history
                  (bill_id, changed_at, field_name, old_value, new_value)
                VALUES (?,?,?,?,?)
            """, change)
        conn.execute("""
            UPDATE bills
            SET bill_name=?, bill_kind=?, proposer=?, proposer_kind=?,
                committee=?, committee_proc_result=?, committee_proc_dt=?,
                proc_result=?, proc_dt=?, detail_link=?, raw_json=?, last_updated=?
            WHERE bill_id=?
        """, (bill_name, bill_kind, proposer, proposer_kind,
              committee, cmmt_proc_result, cmmt_proc_dt,
              proc_result, proc_dt, detail_link, raw_json, now, pk))
        return False, True

    return False, False


def log_collect(conn, keyword, now, fetched, new, updated, error=None):
    conn.execute("""
        INSERT INTO collect_log
          (collected_at, keyword, total_fetched, new_bills, updated_bills, error_msg)
        VALUES (?,?,?,?,?,?)
    """, (now, keyword, fetched, new, updated, error))


def run_collection():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("═══ 수집 시작: %s ═══", now)
    total_new = total_updated = 0

    for keyword in SEARCH_KEYWORDS:
        error_msg = None
        new_cnt = updated_cnt = 0
        bills = []
        try:
            bills = fetch_all_bills(keyword)
            with get_conn() as conn:
                for row in bills:
                    is_new, is_updated = upsert_bill(conn, row, now)
                    new_cnt     += is_new
                    updated_cnt += is_updated
                log_collect(conn, keyword, now, len(bills), new_cnt, updated_cnt)
        except Exception as e:
            error_msg = str(e)
            logger.exception("키워드 '%s' 수집 중 오류", keyword)
            with get_conn() as conn:
                log_collect(conn, keyword, now, len(bills), new_cnt, updated_cnt, error_msg)

        logger.info("  [%s] 수집 %d건 / 신규 %d건 / 변경 %d건",
                    keyword, len(bills), new_cnt, updated_cnt)
        total_new     += new_cnt
        total_updated += updated_cnt

    logger.info("═══ 수집 완료: 신규 %d건 / 변경 %d건 ═══", total_new, total_updated)
    return total_new, total_updated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    from database import init_db
    init_db()
    run_collection()
