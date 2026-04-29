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
 
# 검색 키워드 - 동물복지/보호/권리 관련 광범위 수집
SEARCH_KEYWORDS = [
    # 직접 동물 관련
    "동물보호",
    "동물복지",
    "동물권",
    "반려동물",
    "유기동물",
    "동물학대",
    "동물실험",
    "동물원",
    "동물약품",
    "농장동물",
    # 관련 법률
    "가축전염병",
    "수의사",
    "말산업",
    "한국마사회",
    "야생생물",
    "축산물",
    "축산법",
    "수산생물",
    "해양생물",
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
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
 
# ──────────────────────────────────────────────
# AI 필터링 설정 (Claude API)
# ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_FILTER_ENABLED = True   # AI 관련성 분석 on/off
AI_RELEVANCE_THRESHOLD = 3  # 1~5점 중 이 점수 이상만 주요 발의안으로 분류
