"""
의안 제안이유 및 주요내용 수집기 - BPMBILLSUMMARY

- 열린국회정보 BPMBILLSUMMARY API는 의안번호(BILL_NO) 1건당 1회 호출하는 구조
- 응답 필드: BILL_NO, BILL_NAME, BILL_ID, SUMMARY, AGE
- 정상 응답이어도 SUMMARY가 비어 있는 의안이 있으므로, 빈 값은 SUMMARY_EMPTY로
  표시해 두고 일정 기간(RETRY_DAYS) 안에서만 다시 조회한다.
- 처음 실행할 때는 기존 의안 전체(약 200건)를 한 번에 채우고,
  이후에는 신규 의안만 조회한다.
"""
import logging
import time
from datetime import datetime, timedelta

import requests

from config import API_KEY, API_BASE_URL
from database import get_conn

logger = logging.getLogger(__name__)

ENDPOINT = f"{API_BASE_URL}/BPMBILLSUMMARY"
SUMMARY_EMPTY = "-"      # 조회했지만 본문이 없는 경우
RETRY_DAYS = 30          # 발의 후 이 기간 안의 빈 값은 매일 재조회
MAX_PER_RUN = 400        # 1회 실행당 최대 호출 수 (안전장치)
SLEEP_SEC = 0.3


def migrate_summary_column():
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bills)").fetchall()]
        if "summary" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN summary TEXT DEFAULT NULL")
            logger.info("bills.summary 컬럼 추가")


def _extract_rows(data: dict) -> list:
    """응답 루트 키 이름에 의존하지 않고 row 목록을 꺼낸다."""
    if not isinstance(data, dict):
        return []
    for value in data.values():
        if isinstance(value, list) and len(value) >= 2:
            try:
                return value[1].get("row", []) or []
            except AttributeError:
                continue
    return []


def fetch_summary(bill_no: str, bill_id: str = "") -> str | None:
    """
    반환값
      - 본문 문자열: 정상 수집
      - SUMMARY_EMPTY: 응답은 정상이나 본문 없음
      - None: 요청 실패 (다음 실행 때 재시도)
    """
    params = {"KEY": API_KEY, "Type": "json", "pIndex": 1, "pSize": 5,
              "BILL_NO": bill_no}
    try:
        resp = requests.get(ENDPOINT, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("주요내용 조회 실패 (의안번호 %s): %s", bill_no, e)
        return None

    rows = _extract_rows(data)
    for row in rows:
        # 의안번호만으로 조회하는 API이므로 BILL_ID가 있으면 한 번 더 대조
        if bill_id and row.get("BILL_ID") and row.get("BILL_ID") != bill_id:
            continue
        text = (row.get("SUMMARY") or "").strip()
        return _clean(text) if text else SUMMARY_EMPTY
    return SUMMARY_EMPTY


def _clean(text: str) -> str:
    # 줄바꿈은 유지하되 연속 공백·빈 줄만 정리
    lines = [" ".join(line.split()) for line in text.replace("\r", "").split("\n")]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()


def run_summary_fetch():
    migrate_summary_column()
    retry_from = (datetime.now() - timedelta(days=RETRY_DAYS)).strftime("%Y-%m-%d")

    with get_conn() as conn:
        targets = conn.execute("""
            SELECT bill_id, bill_no FROM bills
            WHERE bill_no IS NOT NULL AND bill_no != ''
              AND (summary IS NULL
                   OR (summary = ? AND propose_dt >= ?))
            ORDER BY propose_dt DESC
            LIMIT ?
        """, (SUMMARY_EMPTY, retry_from, MAX_PER_RUN)).fetchall()

    if not targets:
        logger.info("주요내용 수집 대상 없음")
        return 0

    logger.info("주요내용 수집 시작: %d건", len(targets))
    filled = 0
    for t in targets:
        result = fetch_summary(t["bill_no"], t["bill_id"])
        if result is not None:
            with get_conn() as conn:
                conn.execute("UPDATE bills SET summary=? WHERE bill_id=?",
                             (result, t["bill_id"]))
            if result != SUMMARY_EMPTY:
                filled += 1
        time.sleep(SLEEP_SEC)

    logger.info("주요내용 수집 완료: %d건 중 %d건 본문 확보", len(targets), filled)
    return filled


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    run_summary_fetch()
