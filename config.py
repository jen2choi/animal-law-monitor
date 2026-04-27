"""
동물보호법 발의안 모니터링 시스템 - 설정 파일
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ──────────────────────────────────────────────
# 국회 OpenAPI 설정
# ──────────────────────────────────────────────
API_KEY = os.getenv("ASSEMBLY_API_KEY", "2f9680f4b1554511954ba421194ee8bb")
API_BASE_URL = "https://open.assembly.go.kr/portal/openapi"

SEARCH_KEYWORDS = [
    "동물보호",
    "동물복지",
    "반려동물",
    "유기동물",
    "동물학대",
    "동물실험",
]

MAX_PAGES = 10

# ──────────────────────────────────────────────
# DB 설정
# ──────────────────────────────────────────────
DB_PATH = BASE_DIR / "data" / "bills.db"

# ──────────────────────────────────────────────
# 스케줄 설정
# ──────────────────────────────────────────────
COLLECT_HOUR = 9
COLLECT_MINUTE = 0

REPORT_WEEKDAY = "mon"
REPORT_HOUR = 9
REPORT_MINUTE = 30

# ──────────────────────────────────────────────
# 리포트 출력 설정
# ──────────────────────────────────────────────
REPORT_DIR = BASE_DIR / "reports"
REPORT_FORMAT = "markdown"

# ──────────────────────────────────────────────
# Google Sheets 설정
# ──────────────────────────────────────────────
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")  # JSON 문자열
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")          # 스프레드시트 ID
