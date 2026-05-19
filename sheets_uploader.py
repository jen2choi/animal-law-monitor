"""
Google Sheets 업로더 - ALLBILLV2 필드 반영
[수정 내용]
- 전체발의안 시트: proc_result 빈 값을 "-"으로 수정 (기존 "계류중" 버그 수정)
- 전체발의안 시트: "포함여부" 열 추가
  · Y = 대시보드에 표시
  · N = 대시보드에서 제외
  · 기본값: AI 점수 3점 이상이면 Y, 미만이면 N
  · 담당자가 시트에서 직접 Y/N 수정 가능 (수동 override)
  · 이미 값이 있는 셀은 덮어쓰지 않음 (수동 수정 보존)
"""
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_CREDENTIALS, SPREADSHEET_ID, AI_RELEVANCE_THRESHOLD

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

        # ── 이번 주 변동 ──
        ws = get_or_create_sheet(sh, "이번주변동")
        headers = ["구분", "의안번호", "의안명", "변경항목", "이전값", "현재값", "일시"]
        rows = []
        field_labels = {
            "proc_result":            "본회의 심의결과",
            "committee_proc_result":  "위원회 처리결과",
            "committee":              "소관위원회",
            "bill_name":              "의안명"
        }
        for b in data["new_bills"]:
            rows.append(["신규발의", b["bill_no"], b["bill_name"],
                         "신규 발의", "-", b["bill_kind"] or "-", b["propose_dt"] or "-"])
        for c in data["changed"]:
            rows.append(["상태변경", c["bill_id"], c["bill_name"],
                         field_labels.get(c["field_name"], c["field_name"]),
                         c["old_value"] or "-", c["new_value"] or "-",
                         c["changed_at"][:16]])
        write_sheet(ws, headers, rows)

        # ── 전체 발의안 ──
        # 컬럼 순서 (코드.gs의 row[] 인덱스와 정확히 매핑):
        # [0] 포함여부(Y/N)  [1] 관련성점수  [2] AI태그  [3] 의안번호  [4] 의안종류
        # [5] 의안명  [6] 제안자구분  [7] 대표발의자  [8] 발의일  [9] 회기
        # [10] 소관위원회  [11] 위원회처리결과  [12] 본회의결과
        # [13] 처리일  [14] 최초수집일  [15] 링크

        ws = get_or_create_sheet(sh, "전체발의안", rows=1000, cols=16)

        # 기존 포함여부 값 읽어오기 (수동 수정 보존)
        existing_include = {}
        try:
            existing_data = ws.get_all_values()
            if len(existing_data) > 1:
                for row in existing_data[1:]:
                    if len(row) >= 4 and row[3]:  # bill_no는 [3]
                        bill_no = row[3]
                        include_val = row[0] if row[0] in ("Y", "N") else None
                        if include_val:
                            existing_include[bill_no] = include_val
        except Exception:
            pass

        headers = ["포함여부", "관련성점수", "AI태그", "의안번호", "의안종류", "의안명",
                   "제안자구분", "대표발의자", "발의일", "회기",
                   "소관위원회", "위원회처리결과", "본회의결과", "처리일",
                   "최초수집일", "링크"]

        rows = []
        for b in data["all_bills"]:
            score = b.get("ai_score")
            bill_no = b["bill_no"]

            # 포함여부 결정: 기존 수동 수정값 우선, 없으면 점수 기준 자동
            if bill_no in existing_include:
                include = existing_include[bill_no]   # 수동 수정 보존
            elif score is not None:
                include = "Y" if score >= AI_RELEVANCE_THRESHOLD else "N"
            else:
                include = "Y"   # 점수 없으면 일단 포함 (나중에 분류)

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

        # 포함여부 열(A열) 강조 포맷
        ws.format("A2:A1000", {
            "horizontalAlignment": "CENTER",
            "textFormat": {"bold": True}
        })
        # Y는 초록, N은 빨강으로 조건부 색상 (배경색으로 구분)
        # gspread는 조건부서식 미지원이므로 텍스트로만 표시

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
