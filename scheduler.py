"""
스케줄러 메인 실행 파일
- 매일 09:00  → 수집 → AI 분석
- 매주 월 09:30 → 리포트 생성 + Google Sheets 업로드
"""
import logging
import signal
import sys
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    COLLECT_HOUR, COLLECT_MINUTE,
    REPORT_WEEKDAY, REPORT_HOUR, REPORT_MINUTE,
)
from database import init_db
from collector import run_collection
from report_generator import generate_weekly_report, get_report_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("monitor.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")


def job_collect():
    logger.info("▶ 일일 수집 잡 시작")
    try:
        new, updated = run_collection()
        logger.info("✔ 수집 완료 — 신규 %d건 / 변경 %d건", new, updated)
    except Exception:
        logger.exception("✘ 수집 잡 오류")

    # AI 필터링 (신규 발의안 분류)
try:
    from ai_filter import run_ai_filter, migrate_db
    migrate_db()
    run_ai_filter(force=True)  # ← force=True 추가 (기존 데이터도 재분류)
    logger.info("✔ AI 분석 완료")
except Exception as e:
    logger.warning("AI 분석 건너뜀: %s", e, exc_info=True)  # ← 실제 오류 출력


def job_report():
    logger.info("▶ 주간 리포트 잡 시작")
    try:
        path = generate_weekly_report()
        logger.info("✔ 엑셀 리포트 생성 완료: %s", path)

        try:
            from sheets_uploader import upload_to_sheets
            data, start_str, end_str = get_report_data()
            upload_to_sheets(data, start_str, end_str)
            logger.info("✔ Google Sheets 업로드 완료")
        except Exception:
            logger.warning("Google Sheets 업로드 건너뜀 (설정 확인 필요)")

    except Exception:
        logger.exception("✘ 리포트 잡 오류")


def main():
    init_db()
    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        job_collect,
        CronTrigger(hour=COLLECT_HOUR, minute=COLLECT_MINUTE),
        id="daily_collect",
        name="일일 발의안 수집",
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_report,
        CronTrigger(day_of_week=REPORT_WEEKDAY,
                    hour=REPORT_HOUR, minute=REPORT_MINUTE),
        id="weekly_report",
        name="주간 리포트 생성",
        misfire_grace_time=3600,
    )

    def handle_signal(sig, frame):
        logger.info("종료 신호 수신 — 스케줄러 정지 중...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("═══ 모니터링 시스템 시작 ═══")
    scheduler.start()


if __name__ == "__main__":
    if "--now" in sys.argv:
        init_db()
        logger.info("즉시 수집 + 리포트 실행 모드")
        job_collect()
        job_report()
    else:
        main()
