"""`meal_feedbacks.suggestions` JSONB 해석 규칙.

끼니 피드백 화면(`services/feedback.py`)과 챗봇 첫 메시지(`services/chat.py`)가 **같은
규칙**으로 걸러야 같은 끼니가 두 화면에서 다른 카드를 보이지 않는다. DB 의존 0.
"""

from app.crud.feedback import StoredSuggestion, parse_suggestion, parse_suggestions


def test_valid_item_is_kept():
    item = {"foodName": "두부 반 모", "advice": "단백질을 채워요", "candidateFoodRefId": "KFD_1"}
    assert parse_suggestion(item) == StoredSuggestion("두부 반 모", "단백질을 채워요", "KFD_1")


def test_missing_ref_is_none():
    assert parse_suggestion({"foodName": "두부", "advice": "채워요"}).ref_id is None


def test_non_string_ref_is_dropped_to_none():
    """int 는 `varchar = integer` 로 DB 가 거부하고, list 는 set() 에서 터진다."""
    assert parse_suggestion({"foodName": "두부", "advice": "채워요", "candidateFoodRefId": 3}).ref_id is None


def test_null_or_empty_text_is_rejected():
    assert parse_suggestion({"foodName": None, "advice": "채워요"}) is None
    assert parse_suggestion({"foodName": "두부", "advice": ""}) is None


def test_non_dict_is_rejected():
    assert parse_suggestion("두부") is None


def test_parse_suggestions_drops_only_broken_items():
    stored = ["깨진 원소", {"foodName": "두부", "advice": "채워요"}, {"foodName": 1, "advice": "x"}]
    assert parse_suggestions(stored) == [StoredSuggestion("두부", "채워요", None)]


def test_parse_suggestions_non_list_is_empty():
    assert parse_suggestions(None) == []
    assert parse_suggestions({"foodName": "두부"}) == []
