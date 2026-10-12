"""
규칙 기반 관련성 분류기 - Claude API 없이 동작
법안명 키워드로 동물복지 관련성 점수(1~5) + 태그 자동 생성

점수 기준 (AI_RELEVANCE_THRESHOLD = 3 기준):
  5점 ✅ 포함: 동물보호/복지 핵심 법안
  4점 ✅ 포함: 동물복지 조항 포함 법안
  3점 ✅ 포함: 동물 관련 법안 (경계선)
  2점 ❌ 제외: 간접 관련 (동물복지 주목적 아님)
  1점 ❌ 제외: 무관

※ 마사회법, 가축전염병법 등 경계선 법안은 낮은 점수로 N 처리되지만
  담당자가 구글 시트 "포함여부" 열에서 Y로 직접 수정 가능
"""

print("ai_filter.py 로드됨", flush=True)

import json
import logging

import re

from config import AI_FILTER_ENABLED, AI_RELEVANCE_THRESHOLD, CONTENT_CHECK_LAWS
from database import get_conn

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 분류 규칙
# ──────────────────────────────────────────────

SCORE_5_LAWS = [
    "동물보호법", "동물복지법",
    "동물원 및 수족관", "동물원수족관",
    "야생생물 보호", "야생생물보호",
    "실험동물에 관한 법률",
    "개 식용 종식", "개식용종식",
    "개의 식용",
    "동물대체시험",
]

SCORE_5_KEYWORDS = [
    "동물학대", "동물 학대",
    "유기동물", "유기 동물",
    "동물등록", "반려동물 등록",
    "동물보호", "동물복지",
    "동물 복지", "동물 보호",
    "동물권",
]

SCORE_4_KEYWORDS = [
    "반려동물", "동물실험", "실험동물",
    "전시동물", "농장동물",
    "동물원", "수족관",
    "야생동물", "야생 동물",
    "동물진료", "동물 진료",
    "동물병원", "동물 병원",
    "동물용의약품",
]

SCORE_3_KEYWORDS = [
    "수의사법", "동물진료비",
    "동물복지 축산", "동물 복지 축산",
    "축산환경", "가축 사육", "사육환경",
]

SCORE_2_KEYWORDS = [
    "가축전염병", "가축 전염병",
    "축산물", "축산법",
    "수산생물", "해양생물",
    "어업", "낚시",
    "한국마사회", "말산업",
]

TAG_MAP = {
    "반려동물": "반려동물",
    "유기동물": "유기동물",
    "유기 동물": "유기동물",
    "동물보호": "동물보호",
    "동물복지": "동물복지",
    "동물학대": "동물보호",
    "동물실험": "실험동물",
    "실험동물": "실험동물",
    "야생생물": "야생동물",
    "야생동물": "야생동물",
    "동물원": "전시동물",
    "수족관": "전시동물",
    "농장동물": "농장동물",
    "가축": "농장동물",
    "축산": "농장동물",
    "수의사": "수의",
    "동물진료": "수의",
    "동물병원": "수의",
    "해양생물": "해양생물",
    "수산생물": "해양생물",
    "개 식용": "동물보호",
    "개식용": "동물보호",
}


def _get_tags(bill_name: str) -> list:
    tags = []
    for keyword, tag in TAG_MAP.items():
        if keyword in bill_name and tag not in tags:
            tags.append(tag)
    return tags


# 「반려」는 「반려되는(거절)」, 「가축」은 「가축의 방목」 같은 무관한 문맥에도 걸려 제외
ANIMAL_RE = re.compile(r"동물|반려견|반려묘|야생동|유기견|유기묘|로드킬")
ECOLOGY_RE = re.compile(r"생물\s?다양성|생태계|서식지|야생\s?생물")


def _classify_by_content(name: str, summary: str, proposer_type: str):
    """민법·자연환경보전법 등 법안명만으로 판단이 어려운 법률을 주요내용으로 분류.
    해당 법률이 아니면 None 반환."""
    if not any(law in name for law in CONTENT_CHECK_LAWS):
        return None
    text = summary or ""
    animal_hits = len(ANIMAL_RE.findall(text))
    if animal_hits >= 1:
        return {"score": 4, "tags": _get_tags(text) or ["동물보호"],
                "summary": "주요내용에 동물 관련 내용 포함", "proposer_type": proposer_type}
    if "민법" not in name and ECOLOGY_RE.search(text):
        # 생태·서식지 관련이지만 동물 직접 언급 없음 → 담당자 검토(?)
        return {"score": 2, "tags": ["야생동물"],
                "summary": "생태·서식지 관련 (검토 필요)", "proposer_type": proposer_type}
    return {"score": 1, "tags": [], "summary": "동물 관련 내용 없음",
            "proposer_type": proposer_type}


def classify_bill(bill_name: str, proposer_kind: str = "", summary: str = "") -> dict:
    """법안명(필요 시 주요내용)으로 관련성 점수, 태그 반환 (API 없이 규칙 기반)"""
    name = bill_name.strip()

    proposer_type = "의원"
    if "위원장" in (proposer_kind or ""):
        proposer_type = "위원장"
    elif "정부" in (proposer_kind or ""):
        proposer_type = "정부"

    by_content = _classify_by_content(name, summary, proposer_type)
    if by_content is not None:
        return by_content

    for law in SCORE_5_LAWS:
        if law in name:
            return {"score": 5, "tags": _get_tags(name),
                    "summary": "동물보호/복지 핵심 법안", "proposer_type": proposer_type}

    for kw in SCORE_5_KEYWORDS:
        if kw in name:
            return {"score": 5, "tags": _get_tags(name),
                    "summary": "동물보호/복지 관련 법안", "proposer_type": proposer_type}

    for kw in SCORE_4_KEYWORDS:
        if kw in name:
            return {"score": 4, "tags": _get_tags(name),
                    "summary": "동물복지 조항 포함 법안", "proposer_type": proposer_type}

    for kw in SCORE_3_KEYWORDS:
        if kw in name:
            return {"score": 3, "tags": _get_tags(name) or ["동물보호"],
                    "summary": "동물 관련 법안", "proposer_type": proposer_type}

    for kw in SCORE_2_KEYWORDS:
        if kw in name:
            return {"score": 2, "tags": [],
                    "summary": "간접 관련 법안", "proposer_type": proposer_type}

    if "동물" in name:
        return {"score": 3, "tags": _get_tags(name) or ["동물보호"],
                "summary": "동물 관련 법안", "proposer_type": proposer_type}

    return {"score": 1, "tags": [], "summary": "동물복지 비관련",
            "proposer_type": proposer_type}


# ──────────────────────────────────────────────
# DB 스키마 업데이트
# ──────────────────────────────────────────────

def migrate_db():
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(bills)").fetchall()]
        for col, default in [
            ("ai_score",        "NULL"),
            ("ai_tags",         "NULL"),
            ("ai_summary",      "NULL"),
            ("ai_proposer_type","NULL"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE bills ADD COLUMN {col} TEXT DEFAULT {default}")
    logger.info("DB 마이그레이션 완료")


# ──────────────────────────────────────────────
# 일괄 분류
# ──────────────────────────────────────────────

def run_ai_filter(force: bool = False):
    if not AI_FILTER_ENABLED:
        logger.info("필터링 비활성화 상태")
        return

    migrate_db()

    with get_conn() as conn:
        has_summary = "summary" in [r[1] for r in conn.execute("PRAGMA table_info(bills)").fetchall()]
        query = ("SELECT bill_id, bill_name, proposer_kind, "
                 + ("summary" if has_summary else "NULL AS summary") + " FROM bills")
        if not force:
            query += " WHERE ai_score IS NULL"
        bills = conn.execute(query).fetchall()

    if not bills:
        logger.info("분류할 발의안 없음")
        return

    logger.info("규칙 기반 분류 시작: %d건", len(bills))
    score_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    for bill in bills:
        summary = bill["summary"] if bill["summary"] not in (None, "-") else ""
        result = classify_bill(bill["bill_name"], bill["proposer_kind"] or "", summary)
        with get_conn() as conn:
            conn.execute("""
                UPDATE bills
                SET ai_score=?, ai_tags=?, ai_summary=?, ai_proposer_type=?
                WHERE bill_id=?
            """, (
                result["score"],
                json.dumps(result["tags"], ensure_ascii=False),
                result["summary"],
                result["proposer_type"],
                bill["bill_id"]
            ))
        score_counts[result["score"]] = score_counts.get(result["score"], 0) + 1

    included = sum(v for k, v in score_counts.items() if k >= AI_RELEVANCE_THRESHOLD)
    excluded = sum(v for k, v in score_counts.items() if k < AI_RELEVANCE_THRESHOLD)
    logger.info(
        "분류 완료 — 5점:%d / 4점:%d / 3점:%d / 2점:%d / 1점:%d | 포함:%d / 제외:%d",
        score_counts[5], score_counts[4], score_counts[3],
        score_counts[2], score_counts[1], included, excluded
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    migrate_db()
    run_ai_filter(force=True)
