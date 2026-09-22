"""영양정보 도메인 로직 — 음식명으로 공공 DB 를 찾고 섭취량만큼 환산한다.

DB 세션은 직접 다루지 않고 `crud/` 를 통해서만 접근한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.crud import food as food_crud
from app.models.food import FoodRef
from app.schemas.nutrition import FoodCandidate, NutritionInfo


@dataclass(frozen=True)
class NutritionMatch:
    """공공 DB 에 붙은 결과. `nutrition` 은 환산이 가능했을 때만 채워진다."""

    food_ref_id: str
    nutrition: NutritionInfo | None


_QUANTUM = Decimal("0.01")

MIN_QUERY_LENGTH = 1
"""후보 검색어의 최소 길이(정규화 후).

**한글은 한 글자가 단어다** — `밥` · `국` · `떡` · `죽` · `면` 은 완결된 음식 이름이라
영어의 한 글자와 사정이 다르다. 실제로 `q=떡` 의 상위 5건은 꿀떡 · 떡국 · 쑥떡 ·
장떡 · 호떡 이다. 한 글자가 수만 건과 일치하는 건 맞지만(`밥` 16,566건),
`_candidate_order` 의 GENERAL · 짧은 이름 우선 정렬이 그중 쓸 만한 것만 위로 올린다.

거부해도 **아무것도 절약되지 않는다.** 어차피 전건 순차 스캔이라 검색어 길이는 비용과
무관하다 — 같은 세션 연속 측정에서 `밥` 88ms, `김치` 78ms, `김치찌개` 76ms 로 차이가
없었다. 막으면 사용자가 다른 이름을 다시 칠 뿐이고 그 요청도 같은 값을 쓴다.

값이 1 이라 이 검사는 실질적으로 **"정규화하면 아무것도 안 남는 입력"** 을 막는다
(`"_"` · `" "`). 상수로 남겨 둔 건 엔드포인트의 `min_length` 가 이걸 참조해
OpenAPI 에 같은 규칙을 비추기 때문이다."""

MAX_QUERY_TOKENS = 12
"""검색어에 담을 수 있는 단어 수 상한.

조각 하나가 33만건 평가 한 번이다 — 토큰 25개짜리 요청이 3.1초를 쓴다(실측).
`limit` 으로는 못 줄인다: 정렬 전에 모든 행을 평가해야 한다.

토큰을 조용히 버리지 않고 거부한다. 버리면 사용자가 친 단어가 검색에 들어가지
않았는데도 결과가 그럴듯해 보인다 — 1단계(부분일치)의 AND 는 정확도를 책임지는
자리라 임의로 느슨해지면 안 된다."""

MAX_FUZZY_FRAGMENTS = 8
"""퍼지 폴백이 쓰는 두 글자 조각 수 상한.

이쪽은 거부하지 않고 앞에서 자른다. 퍼지는 근사 검색이라 긴 이름의 앞부분만 써도
성격이 바뀌지 않고, 정확도를 책임지는 1단계는 토큰을 하나도 버리지 않기 때문이다.
조각 41개(48자 검색어)가 4.2초였다 — 8개면 그 1/5 이다."""


def _finite(value: Decimal | None) -> Decimal | None:
    """NaN·Infinity 를 결측과 똑같이 본다.

    Postgres NUMERIC 은 NaN 을 담을 수 있고, 공공 DB 적재분에 한 건만 섞여 있어도
    `NutritionInfo` 검증이 터져 500 이 된다(pydantic 은 비유한 Decimal 을 거부한다).
    """
    if value is None or not value.is_finite():
        return None
    return value


def _scale(value: Decimal | None, factor: Decimal) -> Decimal | None:
    """기준량 값에 배율을 곱한다. 공공 DB 에 값이 없으면 지어내지 않고 None."""
    finite = _finite(value)
    if finite is None:
        return None
    return (finite * factor).quantize(_QUANTUM)


def resolve_by_name(
    db: Session, *, name: str, amount_g: Decimal | None
) -> NutritionMatch | None:
    """음식명으로 공공 DB 를 찾는다. **하나로 좁혀지지 않으면 None.**

    못 찾은 것과 여러 건이라 고르지 못한 것을 구분하지 않는다 — 호출부가 할 일이
    `matched: false` 로 같기 때문이다. 그 신호를 받은 FE 는 `GET /nutrition/candidates`
    로 후보를 띄우고 사용자가 고른다. 왜 임의로 하나를 집지 않는지는
    `crud.food.find_unique_by_name` 참고.
    """
    food_ref = food_crud.find_unique_by_name(db, name)
    if food_ref is None:
        return None

    # 먹은 양을 모르거나(g 환산 불가) 기준량을 모르면 비례 계산의 근거가 없다.
    # 0 도 걸러진다 — 나누면 터진다.
    #
    # 기준량의 NaN 을 `_scale` 이 잡아 주지 못한다는 점에 주의: `_scale` 은 곱해질
    # 값만 보는데, 기준량이 NaN 이면 배율 자체가 NaN 이 되어 모든 성분이 NaN 으로
    # 물든다. `not Decimal("NaN")` 은 False 라(NaN 은 truthy) 여기서 걸러야 한다.
    serving_size = food_ref.serving_size
    if (
        amount_g is None
        or serving_size is None
        or not serving_size.is_finite()
        or serving_size <= 0
    ):
        return NutritionMatch(food_ref_id=food_ref.id, nutrition=None)

    factor = amount_g / serving_size
    nutrition = NutritionInfo(
        kcal=_scale(food_ref.calories, factor),
        protein_g=_scale(food_ref.protein_g, factor),
        fat_g=_scale(food_ref.fat_g, factor),
        carb_g=_scale(food_ref.carbohydrate_g, factor),
        fiber_g=_scale(food_ref.fiber_g, factor),
        sodium_mg=_scale(food_ref.sodium_mg, factor),
    )
    return NutritionMatch(food_ref_id=food_ref.id, nutrition=nutrition)


def search_candidates(db: Session, *, query: str, limit: int) -> list[FoodCandidate]:
    """사용자가 고를 후보를 찾는다. 못 찾으면 빈 목록.

    `resolve_by_name` 과 호출 맥락이 정반대다. 저쪽은 서버가 자동으로 음식을 거는
    자리라 애매하면 포기하고, 여기는 **사람이 눈으로 고르는** 자리라 애매해도
    보여준다 — 틀린 후보가 목록에 섞이는 비용은 사용자가 무시하면 끝이지만,
    자동으로 걸린 틀린 값은 Q/Q/S 채점까지 조용히 흘러간다.

    **정규화 뒤에 남는 조각이 하나는 있어야 한다**(`MIN_QUERY_LENGTH`). 아니면
    `ValueError` 다. 한 글자 검색은 막지 않는다 — 한글은 한 글자가 단어이고, 거부해도
    비용이 줄지 않는다(상수 쪽에 실측을 적어 두었다).

    검사를 **정규화 뒤에** 하는 게 핵심이다. 원문 길이로 재면 `"_"` 처럼 지워지고 나면
    아무것도 안 남는 입력이 통과한다. 빈 조각은 `LIKE '%%'` 가 되어 퍼지 점수에서
    모든 행에 1점을 주므로(`_tokenize` 참고) 33만건 전부가 후보가 된다.

    HTTP 로 옮기는 건 호출부의 몫이다(`api/v1/endpoints/nutrition.py`).
    """
    tokens = _tokenize(query)
    if max((len(token) for token in tokens), default=0) < MIN_QUERY_LENGTH:
        raise ValueError("검색어를 입력해 주세요.")
    if len(tokens) > MAX_QUERY_TOKENS:
        raise ValueError(f"검색어는 {MAX_QUERY_TOKENS} 단어까지 가능합니다.")

    exact = food_crud.search_by_tokens(db, tokens=tokens, limit=limit)
    found = list(exact)

    # 부분일치가 목록을 채우지 못했을 때만 퍼지로 나머지를 채운다. 순서가 곧
    # 관련도다 — 검색어를 통째로 담은 이름이 두 글자만 겹치는 이름보다 앞이다.
    #
    # 조각이 하나뿐이면(`계란` 같은 두 글자 검색어) 2단계의 조건이 1단계와 **완전히
    # 같아진다.** 그리고 여기까지 왔다는 건 LIMIT 이 자르지 않았다는 뜻이라 `exact`
    # 가 그 조건을 만족하는 전체 집합이고, `exclude_ids` 로 빼고 나면 남는 게 없다.
    # 결과가 확실히 0건인 33만건 스캔이라 아예 돌리지 않는다 — 실측으로 확인했다:
    # `q=가물` `limit=20` 이 가드 없이 213ms·후보 10건, 가드를 걸면 130ms·후보 10건.
    bigrams = _bigrams(tokens)
    if len(found) < limit and len(bigrams) > 1:
        found += food_crud.search_by_bigrams(
            db,
            bigrams=bigrams,
            min_score=_min_overlap(bigrams),
            limit=limit - len(found),
            exclude_ids=[food_ref.id for food_ref in exact],
        )

    return [_to_candidate(food_ref) for food_ref in found]


def _bigrams(tokens: list[str]) -> list[str]:
    """토큰마다 두 글자씩 겹쳐 잘라 낸다. `김치찌게` → `김치`·`치찌`·`찌게`.

    토큰 경계를 넘지 않는다 — `삶은 계란` 에서 `은계` 같은 조각을 만들면 사용자가
    치지도 않은 글자 조합으로 점수가 매겨진다.

    두 글자 이하 토큰은 통째로 한 조각이 된다 — `range(max(len-1, 1))` 이 최소 한
    번은 돌기 때문이다. 슬라이스가 길이를 넘어도 파이썬은 그냥 짧게 돌려주므로
    분기가 필요 없다. 이 보정이 없으면 짧은 토큰이 검색에서 통째로 사라진다.

    순서를 지키며 중복을 지운다(`dict.fromkeys`) — 같은 조각이 두 번 들어가면
    한 번 맞은 이름이 2점을 받아 관련도가 뒤집힌다.

    마지막으로 `MAX_FUZZY_FRAGMENTS` 개까지만 남긴다(비용 상한).
    """
    pieces = [
        token[index : index + 2]
        for token in tokens
        for index in range(max(len(token) - 1, 1))
    ]
    return list(dict.fromkeys(pieces))[:MAX_FUZZY_FRAGMENTS]


def _tokenize(query: str) -> list[str]:
    """검색어를 공백으로 쪼개고 토큰마다 정규화한다.

    `crud.food.normalize_name` 과 같은 변환이라 저장된 이름과 같은 자리에서 비교된다.

    정규화 결과가 빈 문자열인 토큰은 버린다. 빈 토큰만 남는 입력(`"_ _"`)은 어차피
    `search_candidates` 의 길이 검사가 막지만, `"김치 _"` 처럼 **멀쩡한 토큰이 섞여**
    길이 검사를 통과시켜 주면 빈 토큰이 그대로 쿼리까지 간다.

    1단계는 AND 라 `LIKE '%%'` 가 군더더기로 끝나지만(`'%김치%'` 단독과 결과 동일),
    퍼지의 점수는 CASE 합이라 빈 조각이 **모든 행에 1점을 공짜로 준다** — 조각이
    둘이면 `_min_overlap` 이 1이라 33만건 전부가 후보가 된다.
    """
    return [
        normalized
        for token in query.split()
        if (normalized := food_crud.normalize_name(token))
    ]


def _to_candidate(food_ref: FoodRef) -> FoodCandidate:
    """`food_refs` 행을 후보 응답으로 옮긴다. **환산하지 않는다.**

    `nutrition` 은 `serving_size` 기준량의 값 그대로다 — 먹은 양을 모르는 자리이기
    때문이다(`schemas.nutrition.FoodCandidate` 참고).
    """
    return FoodCandidate(
        food_ref_id=food_ref.id,
        name=food_ref.name,
        serving_size_g=_finite(food_ref.serving_size),
        nutrition=NutritionInfo(
            kcal=_finite(food_ref.calories),
            protein_g=_finite(food_ref.protein_g),
            fat_g=_finite(food_ref.fat_g),
            carb_g=_finite(food_ref.carbohydrate_g),
            fiber_g=_finite(food_ref.fiber_g),
            sodium_mg=_finite(food_ref.sodium_mg),
        ),
    )


def _min_overlap(bigrams: list[str]) -> int:
    """후보로 치려면 검색어 조각의 **절반 이상**이 이름에 들어 있어야 한다.

    하한이 없으면 조각 하나가 우연히 겹친 이름이 올라온다 — 실데이터에서
    `존재하지않는음식`(조각 7개)이 `굳지않는송편`(`지않`·`않는` 2개만 겹침)을
    후보로 냈다. 공공 DB 에 없는 음식은 **빈 목록**으로 나가야 사용자가 직접 입력
    으로 넘어간다.

    절반은 오타 한 글자를 견디는 선이다: `김치찌게`(조각 3개) 중 `김치`·`치찌`
    두 개가 `김치찌개` 에 남아 2 ≥ 2 로 살아남는다.

    조각이 하나뿐이면(두 글자 검색어) 이 단계는 부분일치와 같은 조건이 되어
    새로 찾는 것이 없다 — 그래도 1 을 돌려 특수분기를 만들지 않는다.
    """
    return max(1, -(-len(bigrams) // 2))
