"""
AI 관련성 분석기 - Claude API로 동물복지 관련성 자동 태깅
각 발의안에 대해 1~5점 관련성 점수 + 태그 + 한줄 요약 생성
"""
import json
import logging
import time
import urllib.request
import urllib.error

from config import ANTHROPIC_API_KEY, AI_FILTER_ENABLED, AI_RELEVANCE_THRESHOLD
from database import get_conn

logger = logging.getLogger(__name__)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"  # 빠르고 저렴한 모델 사용


# ──────────────────────────────────────────────
# DB 스키마 업데이트 (ai_score 컬럼 추가)
# ──────────────────────────────────────────────

def migrate_db():
    """ai_score, ai_tags, ai_summary 컬럼 추가 (없는 경우)"""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bills)").fetchall()]
        if "ai_score" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN ai_score INTEGER DEFAULT NULL")
        if "ai_tags" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN ai_tags TEXT DEFAULT NULL")
        if "ai_summary" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN ai_summary TEXT DEFAULT NULL")
    logger.info("DB 마이그레이션 완료 (ai_score, ai_tags, ai_summary)")


# ──────────────────────────────────────────────
# Claude API 호출
# ──────────────────────────────────────────────

def analyze_bill(bill_name: str, committee: str = "", proposer: str = "") -> dict:
    """
    발의안 제목을 분석해서 동물복지 관련성 점수 반환
    반환: {"score": 1~5, "tags": [...], "summary": "..."}
    """
    prompt = f"""다음 국회 발의안이 동물복지/동물보호/동물권리와 얼마나 관련이 있는지 분석해주세요.

발의안명: {bill_name}
소관위원회: {committee or "미지정"}
대표발의자: {proposer or "미상"}

다음 기준으로 관련성 점수를 1~5점으로 매겨주세요:
5점: 동물보호/복지/권리가 핵심 주제인 발의안
4점: 동물복지 조항이 주요 내용에 포함된 발의안  
3점: 동물 관련 내용이 일부 포함된 발의안
2점: 간접적으로 동물에 영향을 줄 수 있는 발의안
1점: 동물과 거의 관련 없는 발의안

반드시 아래 JSON 형식으로만 답변하세요. 다른 텍스트는 절대 포함하지 마세요:
{{"score": 점수, "tags": ["태그1", "태그2"], "summary": "한줄요약(20자 이내)"}}

태그 예시: 동물보호, 동물복지, 반려동물, 농장동물, 야생동물, 실험동물, 수의, 축산, 해양생물"""

    if not ANTHROPIC_API_KEY:
        return {"score": None, "tags": [], "summary": ""}

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["content"][0]["text"].strip()
            result = json.loads(text)
            return {
                "score":   int(result.get("score", 1)),
                "tags":    result.get("tags", []),
                "summary": result.get("summary", "")
            }
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        logger.warning("AI 분석 실패 (%s): %s", bill_name[:20], e)
        return {"score": None, "tags": [], "summary": ""}


# ──────────────────────────────────────────────
# 일괄 분석
# ──────────────────────────────────────────────

def run_ai_filter(force: bool = False):
    """
    AI 점수가 없는 발의안을 일괄 분석
    force=True 이면 기존 점수도 재분석
    """
    if not AI_FILTER_ENABLED:
        logger.info("AI 필터링 비활성화 상태")
        return

    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY 미설정 — AI 필터링 건너뜀")
        return

    migrate_db()

    with get_conn() as conn:
        if force:
            bills = conn.execute(
                "SELECT bill_id, bill_name, committee, proposer FROM bills"
            ).fetchall()
        else:
            bills = conn.execute(
                "SELECT bill_id, bill_name, committee, proposer FROM bills WHERE ai_score IS NULL"
            ).fetchall()

    if not bills:
        logger.info("AI 분석할 발의안 없음")
        return

    logger.info("AI 분석 시작: %d건", len(bills))
    success = 0

    for bill in bills:
        result = analyze_bill(
            bill["bill_name"],
            bill["committee"] or "",
            bill["proposer"] or ""
        )
        if result["score"] is not None:
            with get_conn() as conn:
                conn.execute("""
                    UPDATE bills
                    SET ai_score=?, ai_tags=?, ai_summary=?
                    WHERE bill_id=?
                """, (
                    result["score"],
                    json.dumps(result["tags"], ensure_ascii=False),
                    result["summary"],
                    bill["bill_id"]
                ))
            success += 1
            logger.debug("  [%d점] %s", result["score"], bill["bill_name"][:30])

        time.sleep(0.2)  # API 속도 제한

    logger.info("AI 분석 완료: %d/%d건 성공", success, len(bills))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    migrate_db()
    run_ai_filter()
