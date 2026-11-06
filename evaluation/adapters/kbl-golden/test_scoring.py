"""scoring.py 단위 테스트:  uv run --with pytest pytest -q test_scoring.py"""
import scoring


def test_extract_choice():
    v = ["A", "B", "C", "D"]
    assert scoring.extract_choice("정답: B", v) == "B"
    assert scoring.extract_choice("C. 30일입니다", v) == "C"
    assert scoring.extract_choice("(D)", v) == "D"
    assert scoring.extract_choice("A", v) == "A"
    assert scoring.extract_choice("잘 모르겠습니다", v) is None


def test_open_item_scoring():
    item = {"id": "1", "category": "상품", "type": "open", "question": "q",
            "reference_answer": "r", "required_keywords": ["투자실적", "원금손실"], "forbidden": ["원금 보장"]}
    good = scoring.score_item(item, "투자 실적에 따라 원금손실이 날 수 있습니다", judge=5)
    assert good.keyword_recall == 1.0 and good.score == 1.0
    half = scoring.score_item(item, "투자실적에 따라 달라집니다", judge=3)
    assert half.keyword_recall == 0.5 and abs(half.score - (0.6 * 0.5 + 0.4 * 0.5)) < 1e-9
    bad = scoring.score_item(item, "원금 보장되며 투자실적과 원금손실 걱정 없습니다", judge=5)
    assert bad.forbidden == ["원금 보장"] and bad.score == 0.0
    no_judge = scoring.score_item(item, "투자실적", judge=None)
    assert no_judge.score == 0.5


def test_parse_judge_and_aggregate():
    assert scoring.parse_judge("점수: 4") == 4
    assert scoring.parse_judge("Score 2") == 2
    assert scoring.parse_judge("없음") is None
    mcq = {"id": "2", "category": "청구", "type": "mcq", "question": "q", "choices": {"A": "a", "B": "b"}, "answer": "B"}
    rs = [scoring.score_item(mcq, "정답: B", None), scoring.score_item(mcq, "정답: A", None)]
    m = scoring.aggregate(rs)
    assert m["mcq_accuracy"] == 0.5 and m["golden_score"] == 0.5 and m["category_claims"] == 0.5
