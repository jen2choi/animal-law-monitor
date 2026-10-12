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

# 의안정보시스템 개편 후 상세 페이지 주소. 예전 주소(billDetail.do)는 중간 리다이렉트를
# 거치는데, 환경에 따라 홈 화면으로 떨어지는 경우가 있어 최종 주소를 바로 쓴다.
DETAIL_URL = "https://likms.assembly.go.kr/bill/bi/billDetailPage.do?billId={bill_id}&currMenuNo=2600044"


def bill_link(bill_id: str) -> str:
    return DETAIL_URL.format(bill_id=bill_id) if bill_id else ""


def migrate_bills_columns(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(bills)").fetchall()]
    if "proc_stage" not in cols:
        conn.execute("ALTER TABLE bills ADD COLUMN proc_stage TEXT DEFAULT NULL")


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
    # 본회의 의결일은 RGS_RSLN_DT (RGS_CONF_DT는 응답에 없는 필드)
    proc_dt       = row.get("RGS_RSLN_DT") or row.get("RGS_CONF_DT") or ""
    proc_stage    = row.get("PROC_STAGE_CD") or ""   # 의안 진행단계 (예: 소관위접수, 본회의의결)
    detail_link   = bill_link(bill_id)
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
               committee_proc_dt, proc_result, proc_dt, proc_stage,
               detail_link, raw_json, first_seen, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (pk, bill_no, bill_name, bill_kind, proposer, proposer_kind,
              propose_dt, propose_sess, committee, cmmt_proc_result,
              cmmt_proc_dt, proc_result, proc_dt, proc_stage,
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

    # 진행단계: 처음 채워질 때(빈 값 → 값)는 변동으로 기록하지 않음 (기존 의안 일괄 백필 대비)
    try:
        old_stage = existing["proc_stage"] or ""
    except IndexError:
        old_stage = ""
    if proc_stage and old_stage and proc_stage != old_stage:
        changes.append((pk, now, "proc_stage", old_stage, proc_stage))

    # 이력에 남기지 않는 보조 필드(날짜·진행단계·링크)는 매번 최신값으로 갱신
    conn.execute("""
        UPDATE bills SET committee_proc_dt=?, proc_dt=?, proc_stage=?, detail_link=?
        WHERE bill_id=?
    """, (cmmt_proc_dt, proc_dt, proc_stage, detail_link, pk))

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
                migrate_bills_columns(conn)
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
