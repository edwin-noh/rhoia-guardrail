"""골든셋 채점 로직. 모델 호출과 분리해 단위 테스트할 수 있게 둔다.

문항 점수 (0~1)
  - mcq  : 응답에서 추출한 보기 기호가 정답이면 1, 아니면 0
  - open : 0.6 * judge_score + 0.4 * keyword_recall
           금지표현(forbidden)이 하나라도 있으면 0점 (잘못된 안내는 치명적이므로)
  judge_score 를 쓰지 않으면(judge 비활성) open 점수 = keyword_recall

집계 지표
  - golden_score   : 전체 문항 점수 평균 (primary)
  - mcq_accuracy   : 객관식 정답률
  - keyword_recall : 주관식 핵심용어 포함률 평균
  - forbidden_rate : 주관식 중 금지표현이 나온 문항 비율 (낮을수록 좋음)
  - judge_score    : 주관식 LLM 판정 점수 평균 (1~5점 → 0~1 정규화)
  - category_<분류키> : 분류별 golden_score (CATEGORY_KEYS 로 영문 키 매핑)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

W_JUDGE = 0.6
W_KEYWORD = 0.4

# MLflow 지표 키는 ASCII 만 허용되므로 분류명을 영문 키로 바꾼다 (없으면 cat_<n>)
CATEGORY_KEYS = {"약관": "terms", "청구": "claims", "상품": "product", "계약관리": "policy_admin", "세제": "tax"}


def normalize(text: str) -> str:
    """공백/대소문자 차이를 무시하고 비교하기 위한 정규화."""
    return re.sub(r"\s+", "", text or "").lower()


def extract_choice(response: str, valid: list[str]) -> str | None:
    """응답에서 보기 기호(A~D)를 뽑는다. '정답: B', 'B.', '(B)', 'B' 등을 허용."""
    text = (response or "").strip().upper()
    letters = "".join(valid)
    patterns = [
        rf"정답\s*[:：은는]?\s*\(?([{letters}])\b",
        rf"^\(?([{letters}])[\.\)\s:]",
        rf"^([{letters}])$",
        rf"\b([{letters}])\s*[\.\)]",
    ]
    for p in patterns:
        m = re.search(p, text, re.M)
        if m:
            return m.group(1)
    return None


def keyword_recall(response: str, keywords: list[str]) -> float | None:
    if not keywords:
        return None
    resp = normalize(response)
    return sum(normalize(k) in resp for k in keywords) / len(keywords)


def forbidden_hits(response: str, forbidden: list[str]) -> list[str]:
    resp = normalize(response)
    return [f for f in forbidden if normalize(f) in resp]


def parse_judge(text: str) -> int | None:
    """판정 모델 응답에서 1~5 점수를 뽑는다."""
    m = re.search(r"(?:점수|score)\s*[:：]?\s*([1-5])", text or "", re.I) or re.search(r"\b([1-5])\b", text or "")
    return int(m.group(1)) if m else None


JUDGE_PROMPT = """당신은 생명보험 상담 답변을 채점하는 심사관입니다.
모범답안과 비교해 모델 답변의 정확성과 완전성을 1~5점으로 채점하세요.

채점 기준:
5 = 모범답안의 핵심 내용을 모두 정확히 포함
4 = 대부분 정확하나 사소한 누락
3 = 일부 핵심 내용 누락 또는 부정확
2 = 상당 부분 틀리거나 핵심을 놓침
1 = 틀렸거나 고객에게 잘못된 안내를 함

중요 규칙:
- 기간·금액·비율·횟수 등 숫자가 모범답안과 다르면 다른 내용이 맞아도 2점 이하로 채점합니다.
- 모범답안에 없는 내용을 사실처럼 단정하면 감점합니다.

[질문]
{question}

[모범답안]
{reference}

[모델 답변]
{response}

다른 설명 없이 "점수: N" 형식으로만 답하세요."""


@dataclass
class ItemResult:
    id: str
    category: str
    type: str
    question: str
    response: str
    score: float
    correct: bool | None = None
    predicted: str | None = None
    keyword_recall: float | None = None
    forbidden: list[str] = field(default_factory=list)
    judge: int | None = None
    error: str | None = None


def score_item(item: dict, response: str, judge: int | None) -> ItemResult:
    base = dict(id=item["id"], category=item["category"], type=item["type"],
                question=item["question"], response=response)
    if item["type"] == "mcq":
        pred = extract_choice(response, list(item["choices"]))
        correct = pred == item["answer"]
        return ItemResult(**base, score=1.0 if correct else 0.0, correct=correct, predicted=pred)

    kw = keyword_recall(response, item.get("required_keywords", []))
    hits = forbidden_hits(response, item.get("forbidden", []))
    judge_norm = (judge - 1) / 4 if judge is not None else None
    if judge_norm is not None and kw is not None:
        score = W_JUDGE * judge_norm + W_KEYWORD * kw
    else:
        score = judge_norm if judge_norm is not None else (kw if kw is not None else 0.0)
    if hits:
        score = 0.0
    return ItemResult(**base, score=score, keyword_recall=kw, forbidden=hits, judge=judge)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(items: list[ItemResult]) -> dict[str, float]:
    opens = [r for r in items if r.type == "open"]
    mcqs = [r for r in items if r.type == "mcq"]
    metrics: dict[str, float | None] = {
        "golden_score": _mean([r.score for r in items]),
        "mcq_accuracy": _mean([1.0 if r.correct else 0.0 for r in mcqs]),
        "keyword_recall": _mean([r.keyword_recall for r in opens if r.keyword_recall is not None]),
        "forbidden_rate": _mean([1.0 if r.forbidden else 0.0 for r in opens]),
        "judge_score": _mean([(r.judge - 1) / 4 for r in opens if r.judge is not None]),
    }
    for i, cat in enumerate(sorted({r.category for r in items})):
        key = CATEGORY_KEYS.get(cat, f"cat_{i}")
        metrics[f"category_{key}"] = _mean([r.score for r in items if r.category == cat])
    return {k: round(v, 4) for k, v in metrics.items() if v is not None}
