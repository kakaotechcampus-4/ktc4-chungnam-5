"""MEAL 문맥 판정: 실측 오탐과 실제 위반의 대조쌍. 모델/외부 파일 의존 없음."""
import pytest

from app.schemas import ShortFeedbackRequest
from app.short_feedback import cumulative_progress, judge_nutrients
from evals.checks import met_daily_not_pushed, no_contradict_rule, unscored_axis_silent


@pytest.fixture
def request_with_met_targets():
    request = {
        "scope": "MEAL", "userId": "context-test", "mealId": "context-test",
        "stage": "MAINTENANCE", "mealType": "LUNCH", "weightKg": 60,
        "qqs": {"quantity": None, "quality": 80, "satiety": 75},
        "items": [{"displayName": "검증용 식사", "amount": 1, "unit": "인분",
                   "nutrition": {"proteinG": 30, "fiberG": 10, "sodiumMg": 400}}],
        "dailyNutritionTotals": {"proteinG": 90, "fiberG": 28, "sodiumMg": 900},
    }
    model = ShortFeedbackRequest.model_validate(request)
    assert all(j.state == "OK" for j in judge_nutrients(model))
    assert all(p.current >= p.target for p in cumulative_progress(model) if p.name != "sodium")
    return request


def response(field, text):
    result = {"body": "식사 기록을 확인했어요.", "reasoning": "기록을 참고한다.", "suggestions": []}
    if field == "advice":
        result["suggestions"] = [{"foodName": "반찬", "advice": text}]
    else:
        result[field] = text
    return result


@pytest.mark.parametrize("field", ["body", "advice"])
@pytest.mark.parametrize("text,expected", [
    # 실측 두 문장: 완료된 식사의 연결형은 다음 행동을 지시하지 않는다.
    ("다만 아침 수육에서 단백질을 챙긴 데 이어 점심에는 콩과 채소가 더해져, 기록상 단백질은 이미 하루 목표에 도달했어요.", True),
    ("아침 수육에 이어 점심에도 콩으로 단백질을 더해, 기록에서 확인된 누적 단백질은 이미 하루 목표에 도달했어요.", True),
    # 다른 음식/영양소/시점에도 적용되며 다음 행동의 면제 조건은 아니다.
    ("저녁에는 귀리로 식이섬유를 더해, 기록상 목표에 도달했어요.", True),
    ("이번 식사에 렌틸콩으로 단백질을 보태, 구성이 달라졌어요.", True),
    ("아침에 단백질을 보충했어요.", True),
    ("아침에는 소고기로 단백질을 챙기고 점심에는 잡곡과 콩·나물로 식이섬유를 보탰으니, 다음 끼니에는 양념을 적게 쓰면 좋아요.", True),
    ("아침 수육으로 단백질을 챙기고 점심에는 콩과 나물로 식이섬유를 더했으니, 채소 반찬을 이어가면 좋아요.", True),
    ("아침에는 단백질을 챙기고 점심에는 식이섬유를 더했지만 다음 끼니에는 단백질을 보충하세요.", False),
    ("단백질을 더 보충하기보다 채소를 곁들이면 좋아요.", True),
    ("단백질을 더 챙기지 않아도 돼요.", True),
    ("다음 끼니에는 단백질을 더 보충하세요.", False),
    ("식이섬유를 더 챙기면 좋아요.", False),
    ("내일 아침에도 단백질을 더해", False),
    ("아침처럼 단백질을 더해 보세요.", False),
    ("아침에 먹었으니 단백질을 더 보충하세요.", False),
    ("아침에 단백질을 보충했으니 다음 끼니에도 단백질을 더 챙기면 좋아요.", False),
    ("단백질을 이미 채웠으니 더 보충하세요.", False),
    ("단백질은 이미 충분하지만 다음 끼니에 단백질을 더 챙기세요.", False),
    ("단백질을 이미 충분히 먹었어도 더 보충하세요.", False),
    ("단백질을 보충하지 않아도 돼요. 다음 끼니에는 단백질을 더 챙기세요.", False),
    ("단백질을 더 채우는 대신 단백질 반찬을 보충하세요.", False),
])
def test_met_daily_context(field, text, expected, request_with_met_targets):
    assert met_daily_not_pushed(response(field, text), request_with_met_targets)[0] is expected


@pytest.mark.parametrize("field", ["body", "reasoning", "advice"])
@pytest.mark.parametrize("text,expected", [
    ("식후 포만감이 있는 만큼 다음 끼니는 밥 양을 늘리기보다 단백질 반찬을 중심에 두면 좋아요.", True),
    ("식후 포만감도 있었으니 다음 끼니에는 밥 양을 늘리기보다 생선을 곁들이면 좋아요.", True),
    ("다음 식사에는 식사량을 크게 늘리기보다 반찬 구성을 바꾸면 좋아요.", True),
    ("내일은 양을 줄이기보다 채소를 곁들이면 좋아요.", True),
    ("다음 끼니에는 양을 조금씩 살펴보면 좋아요.", True),
    ("이번 식사의 양이 많았어요.", False),
    ("이번 점심은 양이 부족했어요.", False),
    ("이번 식사량이 과했어요.", False),
    ("이번 식사는 많이 드셨어요.", False),
    ("이번 끼니는 양이 적당했어요.", False),
    ("이번 끼니 양이 많은 편이에요.", False),
    ("양이 부족해요.", False),
    ("양이 충분해요.", False),
    ("이번 양이 많았으니 다음 끼니에는 양을 줄이기보다 구성을 바꾸면 좋아요.", False),
    ("다음 끼니는 밥 양을 늘리기보다 채소를 곁들이세요. 이번 양은 적었어요.", False),
    ("다음 끼니를 생각하면 이번 점심은 양이 많았어요.", False),
    ("다음 끼니를 위해 이번 점심은 양을 늘리셨어요.", False),
    ("다음 끼니에는 양을 늘리기보다 채소를 고르되 이번 점심 양은 많았어요.", False),
])
def test_unscored_quantity_context(field, text, expected, request_with_met_targets):
    assert unscored_axis_silent(response(field, text), request_with_met_targets)[0] is expected


@pytest.mark.parametrize("field", ["body", "reasoning", "advice"])
@pytest.mark.parametrize("text,expected", [
    ("나트륨 한도가 남았다는 이유로 짠 반찬을 권하지 않는다.", True),
    ("나트륨 한도가 남았지만 나트륨이 높은 음식을 추천하지 않는다.", True),
    ("현재 식사는 나트륨이 많지 않았어요.", True),
    ("현재 식사는 식이섬유가 부족하지 않았어요.", True),
    ("현재 식사의 나트륨이 많았어요.", False),
    ("이번 점심은 나트륨이 한도를 넘었어요.", False),
    ("이번 국물이 짰어요.", False),
    ("현재 식사의 나트륨이 높은 편이에요.", False),
    ("현재 식사의 나트륨이 높아서 다음 끼니는 싱겁게 먹으면 좋아요.", False),
    ("현재 식사의 나트륨이 많아서 짠 반찬을 권하지 않는다.", False),
    ("현재 식사의 단백질이 적어서 다음 끼니에 보충하면 좋아요.", False),
    ("현재 식사는 식이섬유가 충분하지 않았어요.", False),
    ("현재 식사는 나트륨이 많았으니 짠 반찬을 권하지 않는다.", False),
    ("현재 나트륨이 높은 편이고 반찬을 권하지 않는다.", False),
    ("나트륨 한도가 남았다는 이유로 짠 반찬을 권하지 않는다. 이번 식사 나트륨은 과다했어요.", False),
    ("이번 식사 나트륨은 과다했어요. 짠 반찬은 추천하지 않아요.", False),
])
def test_contradiction_negation_scope(field, text, expected, request_with_met_targets):
    assert no_contradict_rule(response(field, text), request_with_met_targets)[0] is expected
