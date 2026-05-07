"""
AI 관련성 분석기 - Claude API로 동물복지 관련성 자동 태깅
각 발의안에 대해 1~5점 관련성 점수 + 태그 + 한줄 요약 생성

[개선 내용]
- 1단계: 법안명 규칙 기반 사전 분류 (API 비용 절약 + 정확도 향상)
  · 핵심 법안 → 무조건 5점
  · 무관 법안 → 무조건 1점
  · 경계선 법안만 Claude API로 판단
- 2단계: Claude 프롬프트 개선 (구체적 판단 기준 + 발의자구분 활용)
- 3단계: ai_proposer_type 컬럼 추가 (의원안/위원장안/정부안 구분)
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
MODEL = "claude-haiku-4-5-20251001"


# ──────────────────────────────────────────────
# 1단계: 규칙 기반 사전 분류
# ──────────────────────────────────────────────

# 무조건 포함 (5점) — 동물복지/보호가 핵심 목적인 법률
ALWAYS_INCLUDE_LAWS = [
    "동물보호법",
    "동물복지법",
    "동물원 및 수족관",
    "동물원수족관",
    "야생생물 보호",
    "야생생물보호",
    "실험동물에 관한 법률",
    "개 식용 종식",
    "개식용종식",
    "동물대체시험",
    "동물학대",
    "반려동물 관리",
    "유기동물",
]

# 무조건 제외 (1점) — 동물과 실질적으로 무관한 법률
ALWAYS_EXCLUDE_LAWS = [
    "한국마사회법",         # 경마 산업/도박 규제 위주
    "말산업육성법",         # 말 산업 육성 (복지 조항 거의 없음)
    "가축전염병예방법",     # 방역/검역 위주 (동물복지 아님)
    "축산물 위생관리법",    # 식품위생 위주
    "축산물위생관리법",
    "식품위생법",
    "농약관리법",
    "비료관리법",
    "종자산업법",
    "인삼산업법",
]

# Claude API로 판단할 경계선 법률 (법안 내용에 따라 관련/무관 갈림)
BORDERLINE_LAWS = [
    "수의사법",             # 동물진료/복지 조항 있을 수도, 면허/개설 위주일 수도
    "축산법",               # 동물복지 축산 조항 있을 수도, 생산성 위주일 수도
    "수산생물질병",         # 수산생물 보호 조항 여부에 따라
    "해양생물",             # 해양생물 보호 조항 여부에 따라
    "수산업법",
    "낚시 관리",
    "동물용 의약품",
    "동물용의약품",
    "사료관리법",
]


def prefilter_by_law_name(bill_name: str) -> int | None:
    """
    법안명으로 1차 분류.
    반환값:
      5   → 무조건 포함
      1   → 무조건 제외
      None → Claude API로 판단 필요
    """
    # 무조건 포함 체크
    for keyword in ALWAYS_INCLUDE_LAWS:
        if keyword in bill_name:
            return 5

    # 무조건 제외 체크
    for keyword in ALWAYS_EXCLUDE_LAWS:
        if keyword in bill_name:
            return 1

    # 경계선 법률 체크 → None 반환 (API 판단 필요)
    for keyword in BORDERLINE_LAWS:
        if keyword in bill_name:
            return None

    # 위 목록에 없는 법안 → 동물 키워드 포함 여부 체크
    animal_keywords = [
        "동물", "반려", "유기", "야생", "수의", "축산", "가축",
        "어류", "어업", "수산", "해양생물", "조류", "파충류"
    ]
    for kw in animal_keywords:
        if kw in bill_name:
            return None  # 동물 키워드 있으면 API로 판단

    # 동물 관련 키워드 전혀 없음 → 제외
    return 1


def get_auto_tags_from_law(bill_name: str) -> list[str]:
    """규칙 기반으로 기본 태그 반환 (API 호출 없이)"""
    tags = []
    tag_map = {
        "반려동물": "반려동물", "유기동물": "유기동물", "동물보호": "동물보호",
        "동물복지": "동물복지", "동물학대": "동물보호", "동물실험": "실험동물",
        "야생생물": "야생동물", "동물원": "전시동물", "수족관": "전시동물",
        "농장동물": "농장동물", "가축": "농장동물", "축산": "농장동물",
        "수의사": "수의", "동물용 의약품": "수의", "동물용의약품": "수의",
        "해양생물": "해양생물", "수산생물": "해양생물",
    }
    for keyword, tag in tag_map.items():
        if keyword in bill_name and tag not in tags:
            tags.append(tag)
    return tags or ["동물보호"]


# ──────────────────────────────────────────────
# DB 스키마 업데이트
# ──────────────────────────────────────────────

def migrate_db():
    """ai_score, ai_tags, ai_summary, ai_proposer_type 컬럼 추가"""
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bills)").fetchall()]
        if "ai_score" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN ai_score INTEGER DEFAULT NULL")
        if "ai_tags" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN ai_tags TEXT DEFAULT NULL")
        if "ai_summary" not in cols:
            conn.execute("ALTER TABLE bills ADD COLUMN ai_summary TEXT DEFAULT NULL")
        if "ai_proposer_type" not in cols:
            # 의원안 / 위원장안 / 정부안 구분 컬럼 추가
            conn.execute("ALTER TABLE bills ADD COLUMN ai_proposer_type TEXT DEFAULT NULL")
    logger.info("DB 마이그레이션 완료")


# ──────────────────────────────────────────────
# 2단계: Claude API 호출 (경계선 법안만)
# ──────────────────────────────────────────────

def analyze_bill_with_ai(
    bill_name: str,
    committee: str = "",
    proposer: str = "",
    proposer_type: str = ""   # "의원" / "위원장" / "정부"
) -> dict:
    """
    경계선 법안에 대해 Claude API로 관련성 판단.
    반환: {"score": 1~5, "tags": [...], "summary": "..."}
    """
    proposer_context = ""
    if proposer_type == "위원장":
        proposer_context = "※ 이 법안은 위원장이 제출한 위원회 대안입니다.\n"
    elif proposer_type == "정부":
        proposer_context = "※ 이 법안은 정부 제출안입니다.\n"

    prompt = f"""다음 국회 발의안이 동물복지/동물보호/동물권리와 얼마나 관련이 있는지 판단해주세요.

발의안명: {bill_name}
소관위원회: {committee or "미지정"}
대표발의자: {proposer or "미상"}
{proposer_context}
[판단 기준]
5점: 동물의 생명·복지·권리 보호가 핵심 목적인 법안
     예) 동물학대 금지, 유기동물 보호, 동물실험 규제, 반려동물 등록제
4점: 동물복지 조항이 주요 내용에 명시적으로 포함된 법안
     예) 동물복지 축산농장 인증, 수의사 동물진료 기록 공개
3점: 동물 처우나 환경에 실질적 영향을 주는 조항이 포함된 법안
     예) 축산 환경 개선, 해양생물 서식지 보호
2점: 간접적으로 동물에 영향을 줄 수 있으나 동물복지가 주목적이 아닌 법안
     예) 검역·방역 중심, 축산물 생산성 중심
1점: 동물과 관련이 있어도 동물복지와는 무관한 법안
     예) 경마 도박 규제, 수의사 면허 행정절차, 동물용 의약품 허가 절차

반드시 아래 JSON 형식으로만 답변하세요. 다른 텍스트 없이 JSON만:
{{"score": 점수, "tags": ["태그1", "태그2"], "summary": "한줄요약(20자 이내)"}}

태그는 해당되는 것만: 동물보호, 동물복지, 반려동물, 농장동물, 야생동물, 실험동물, 전시동물, 수의, 해양생물, 유기동물"""

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
        logger.warning("AI 분석 실패 (%s): %s", bill_name[:30], e)
        return {"score": None, "tags": [], "summary": ""}


def classify_proposer_type(ppsr_knd: str) -> str:
    """
    발의자 구분 정규화
    DB의 ppsr_knd 값: "의원" / "위원장" / "정부" 등
    """
    if not ppsr_knd:
        return "의원"
    ppsr_knd = str(ppsr_knd).strip()
    if "위원장" in ppsr_knd:
        return "위원장"
    if "정부" in ppsr_knd:
        return "정부"
    return "의원"


# ──────────────────────────────────────────────
# 통합 분석 함수
# ──────────────────────────────────────────────

def analyze_bill(
    bill_name: str,
    committee: str = "",
    proposer: str = "",
    ppsr_knd: str = ""
) -> dict:
    """
    발의안 분석 메인 함수.
    1단계 규칙 기반 → 경계선이면 2단계 AI 판단
    """
    proposer_type = classify_proposer_type(ppsr_knd)

    # 1단계: 규칙 기반 사전 분류
    prefilter_score = prefilter_by_law_name(bill_name)

    if prefilter_score == 5:
        # 핵심 법안 — AI 불필요
        tags = get_auto_tags_from_law(bill_name)
        return {
            "score": 5,
            "tags": tags,
            "summary": "동물보호/복지 핵심 법안",
            "proposer_type": proposer_type
        }

    if prefilter_score == 1:
        # 무관 법안 — AI 불필요
        return {
            "score": 1,
            "tags": [],
            "summary": "동물복지 비관련",
            "proposer_type": proposer_type
        }

    # 경계선 법안 — Claude API로 판단
    logger.debug("AI 판단 필요: %s", bill_name[:40])
    result = analyze_bill_with_ai(bill_name, committee, proposer, proposer_type)
    result["proposer_type"] = proposer_type
    return result


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

    migrate_db()

    with get_conn() as conn:
        if force:
            bills = conn.execute(
                "SELECT bill_id, bill_name, committee, proposer, ppsr_knd FROM bills"
            ).fetchall()
        else:
            bills = conn.execute(
                """SELECT bill_id, bill_name, committee, proposer, ppsr_knd
                   FROM bills WHERE ai_score IS NULL"""
            ).fetchall()

    if not bills:
        logger.info("AI 분석할 발의안 없음")
        return

    logger.info("분석 시작: %d건 (규칙기반 + AI 혼합)", len(bills))

    rule_based = 0
    ai_based   = 0
    skipped    = 0

    for bill in bills:
        precheck = prefilter_by_law_name(bill["bill_name"])
        use_ai = (precheck is None)

        result = analyze_bill(
            bill["bill_name"],
            bill["committee"] or "",
            bill["proposer"] or "",
            bill["ppsr_knd"] or ""
        )

        if result["score"] is not None:
            with get_conn() as conn:
                conn.execute("""
                    UPDATE bills
                    SET ai_score=?, ai_tags=?, ai_summary=?, ai_proposer_type=?
                    WHERE bill_id=?
                """, (
                    result["score"],
                    json.dumps(result["tags"], ensure_ascii=False),
                    result["summary"],
                    result.get("proposer_type", "의원"),
                    bill["bill_id"]
                ))
            if use_ai:
                ai_based += 1
                time.sleep(0.2)   # API 속도 제한 (AI 사용한 경우만)
            else:
                rule_based += 1
        else:
            skipped += 1

    logger.info(
        "분석 완료: 규칙기반 %d건 / AI판단 %d건 / 실패 %d건",
        rule_based, ai_based, skipped
    )


# ──────────────────────────────────────────────
# 현재 필터링 결과 요약 출력 (디버그용)
# ──────────────────────────────────────────────

def print_filter_summary():
    """임계값 기준으로 포함/제외 현황 출력"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ai_score, COUNT(*) as cnt FROM bills GROUP BY ai_score ORDER BY ai_score DESC"
        ).fetchall()

    print(f"\n=== 필터링 현황 (임계값: {AI_RELEVANCE_THRESHOLD}점 이상 포함) ===")
    total = sum(r["cnt"] for r in rows)
    included = sum(r["cnt"] for r in rows if r["ai_score"] and r["ai_score"] >= AI_RELEVANCE_THRESHOLD)
    excluded = sum(r["cnt"] for r in rows if r["ai_score"] and r["ai_score"] < AI_RELEVANCE_THRESHOLD)
    unscored = sum(r["cnt"] for r in rows if not r["ai_score"])

    for row in rows:
        score_label = f"{row['ai_score']}점" if row["ai_score"] else "미분석"
        include_mark = "✅" if row["ai_score"] and row["ai_score"] >= AI_RELEVANCE_THRESHOLD else "❌"
        print(f"  {include_mark} {score_label}: {row['cnt']}건")

    print(f"\n  전체: {total}건 | 포함: {included}건 | 제외: {excluded}건 | 미분석: {unscored}건")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    migrate_db()
    run_ai_filter()
    print_filter_summary()
