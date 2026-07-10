"""
Google Sheets 업로더 - ALLBILLV2 필드 반영
[수정 내용]
- 이번주변동 시트: 의안번호, 대표발의자, 링크 컬럼 추가
- 신규발의안 시트: 당일/주간 신규 법안
- 포함여부 Y/N/? 3단계 유지
"""
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_CREDENTIALS, SPREADSHEET_ID

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_client():
    creds_json = json.loads(GOOGLE_CREDENTIALS)
    creds = Credentials.from_service_account_info(creds_json, scopes=SCOPES)
    return gspread.authorize(creds)


def get_or_create_sheet(sh, name, rows=500, cols=20):
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(name, rows=rows, cols=cols)


def write_sheet(ws, headers, rows):
    ws.clear()
    all_data = [headers] + rows
    if all_data:
        ws.update(all_data, value_input_option="RAW")
    ws.format("1:1", {
        "backgroundColor": {"red": 0.18, "green": 0.42, "blue": 0.31},
        "textFormat": {"bold": True,
                       "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "horizontalAlignment": "CENTER"
    })


def get_include_flag(score):
    if score is None:
        return "?"
    if score >= 4:
        return "Y"
    if score >= 2:
        return "?"
    return "N"


def upload_to_sheets(data: dict, start: str, end: str):
    if not GOOGLE_CREDENTIALS or not SPREADSHEET_ID:
        logger.warning("Google Sheets 설정이 없어 업로드를 건너뜁니다.")
        return

    try:
        client = get_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # ── 대시보드 ──
        ws = get_or_create_sheet(sh, "대시보드", rows=50, cols=6)
        ws.clear()
        ws.update([
            ["동물보호법 발의안 주간 모니터링 리포트"],
            [f"기준 기간: {start[:10]} ~ {end[:10]}"],
            [f"생성일: {now_str}"],
            [],
            ["항목", "건수"],
            ["전체 발의안",   data["total"]],
            ["계류 중",       data["pending_cnt"]],
            ["처리 완료",     data["completed_cnt"]],
            ["가결",          data["passed"]],
            ["부결/폐기",     data["rejected"]],
            ["이번 주 신규",  len(data["new_bills"])],
            ["이번 주 변동",  len(data["changed"])],
        ])

        # ── 신규발의안 ──
        ws = get_or_create_sheet(sh, "신규발의안", rows=500, cols=10)
        headers = ["수집일", "의안번호", "의안종류", "의안명", "제안자구분",
                   "대표발의자", "발의일", "소관위원회", "링크"]
        rows = [[...] for b in data["new_bills"]
        if get_include_flag(b.get("ai_score")) == "Y"]
        write_sheet(ws, headers, rows)

        # ── 계류 중인 법안 ──
        ws = get_or_create_sheet(sh, "계류중")
        headers = ["의안번호", "의안종류", "의안명", "제안자구분", "대표발의자",
                   "발의일", "소관위원회", "위원회처리결과", "링크"]
        rows = [[b["bill_no"], b["bill_kind"] or "-", b["bill_name"],
                 b["proposer_kind"] or "-", b["proposer"] or "-",
                 b["propose_dt"] or "-", b["committee"] or "-",
                 b["committee_proc_result"] or "-",
                 b["detail_link"] or "-"]
                for b in data["pending"]]
        write_sheet(ws, headers, rows)

        # ── 처리 완료 ──
        ws = get_or_create_sheet(sh, "처리완료")
        headers = ["의안번호", "의안종류", "의안명", "대표발의자", "발의일",
                   "소관위원회", "위원회처리결과", "본회의결과", "처리일", "링크"]
        rows = [[b["bill_no"], b["bill_kind"] or "-", b["bill_name"],
                 b["proposer"] or "-", b["propose_dt"] or "-",
                 b["committee"] or "-", b["committee_proc_result"] or "-",
                 b["proc_result"] or "-", b["proc_dt"] or "-",
                 b["detail_link"] or "-"]
                for b in data["completed"]]
        write_sheet(ws, headers, rows)

        # ── 이번 주 변동 (상태변경만) ──
        ws = get_or_create_sheet(sh, "이번주변동")
        headers = ["의안번호", "의안명", "대표발의자", "변경항목", "이전값", "현재값", "일시", "링크"]
        field_labels = {
            "proc_result":           "본회의 심의결과",
            "committee_proc_result": "위원회 처리결과",
            "committee":             "소관위원회",
            "bill_name":             "의안명"
        }
        rows = []
        for c in data["changed"]:
            rows.append([
                c.get("bill_no") or "-",
                c.get("bill_name") or "-",
                c.get("proposer") or "-",
                field_labels.get(c.get("field_name", ""), c.get("field_name", "-")),
                c.get("old_value") or "-",
                c.get("new_value") or "-",
                str(c.get("changed_at") or "")[:16],
                c.get("detail_link") or "-"
            ])
        write_sheet(ws, headers, rows)

        # ── 전체 발의안 ──
        ws = get_or_create_sheet(sh, "전체발의안", rows=1000, cols=16)

        existing_manual = {}
        try:
            existing_data = ws.get_all_values()
            if len(existing_data) > 1:
                for row in existing_data[1:]:
                    if len(row) >= 4 and row[3]:
                        val = row[0].strip() if row[0] else ""
                        if val in ("Y", "N"):
                            existing_manual[row[3]] = val
        except Exception:
            pass

        headers = ["포함여부", "관련성점수", "AI태그", "의안번호", "의안종류", "의안명",
                   "제안자구분", "대표발의자", "발의일", "회기",
                   "소관위원회", "위원회처리결과", "본회의결과", "처리일",
                   "최초수집일", "링크"]
        rows = []
        for b in data["all_bills"]:
            score = b.get("ai_score")
            if isinstance(score, str) and score == "-":
                score = None
            elif score is not None:
                try:
                    score = int(score)
                except (ValueError, TypeError):
                    score = None

            bill_no = b["bill_no"]
            include = existing_manual.get(bill_no) or get_include_flag(score)

            rows.append([
                include,
                score if score is not None else "-",
                b.get("ai_tags") or "-",
                bill_no,
                b["bill_kind"] or "-",
                b["bill_name"],
                b["proposer_kind"] or "-",
                b["proposer"] or "-",
                b["propose_dt"] or "-",
                b["propose_sess"] or "-",
                b["committee"] or "-",
                b["committee_proc_result"] or "-",
                b["proc_result"] or "-",
                b["proc_dt"] or "-",
                b["first_seen"][:10] if b.get("first_seen") else "-",
                b["detail_link"] or "-"
            ])

        write_sheet(ws, headers, rows)
        ws.format("A2:A1000", {
            "horizontalAlignment": "CENTER",
            "textFormat": {"bold": True}
        })

        # ── 통계 ──
        ws = get_or_create_sheet(sh, "통계", rows=100, cols=4)
        ws.clear()
        stat_data = [
            ["본회의 심의결과 분포"], ["처리결과", "건수"],
            *[[r["status"], r["cnt"]] for r in data["proc_dist"]],
            [],
            ["위원회 처리결과 분포"], ["처리결과", "건수"],
            *[[r["status"], r["cnt"]] for r in data["cmmt_proc_dist"]],
            [],
            ["의안종류별 현황"], ["의안종류", "건수"],
            *[[r["kind"], r["cnt"]] for r in data["bill_kind_dist"]],
            [],
            ["제안자구분별 현황"], ["제안자구분", "건수"],
            *[[r["kind"], r["cnt"]] for r in data["proposer_kind_dist"]],
            [],
            ["소관위원회별 현황"], ["위원회", "건수"],
            *[[r["committee"], r["cnt"]] for r in data["committee_dist"]],
            [],
            ["연도별 발의 추이"], ["연도", "건수"],
            *[[r["year"], r["cnt"]] for r in data["yearly"]],
        ]
        ws.update(stat_data)

        logger.info("Google Sheets 업로드 완료!")

    except Exception as e:
        logger.exception("Google Sheets 업로드 오류: %s", e)
