"""GET /nutrition/candidates 의 HTTP 계약.

이름 매칭이 하나로 좁혀지지 않아 `matched: false` 로 나간 음식(`김치찌개` ·
`미역국` 처럼 흔한 것들이 여기 해당한다)에 사용자가 직접 영양정보를 붙이는
폴백 흐름의 첫 단계다. 검색 규칙 자체는 `test_nutrition_service.py` 가 고정하고,
여기서는 FE 가 의존하는 응답 모양·상태코드만 본다.
"""

from decimal import Decimal

from app.tests.factories import make_food_ref, make_user

_PATH = "/api/v1/nutrition/candidates"


def test_candidates_returns_the_shape_from_the_spec(client, db):
    """명세서(contracts/API.md GET /nutrition/candidates)의 응답 모양 그대로."""
    user = make_user(db)
    make_food_ref(
        db,
        food_ref_id="KFD_01023",
        name="달걀_삶은것",
        serving_size=Decimal("100.000"),
        calories=Decimal("155.000"),
        protein_g=Decimal("13.000"),
        fat_g=Decimal("11.000"),
        carbohydrate_g=Decimal("1.000"),
        fiber_g=Decimal("0.000"),
        sodium_mg=Decimal("124.000"),
    )
    db.commit()

    response = client.get(
        _PATH, params={"q": "달걀", "limit": 5}, headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None

    assert body["data"]["candidates"] == [
        {
            "foodRefId": "KFD_01023",
            "name": "달걀_삶은것",
            "servingSizeG": 100.0,
            "nutrition": {
                "kcal": 155.0,
                "proteinG": 13.0,
                "fatG": 11.0,
                "carbG": 1.0,
                "fiberG": 0.0,
                "sodiumMg": 124.0,
            },
        }
    ]


def test_candidate_nutrition_is_per_serving_size_not_scaled(client, db):
    """후보의 영양성분은 `servingSizeG` 기준량 값이다 — 먹은 양으로 환산하지 않는다.

    이 엔드포인트는 사용자가 얼마나 먹었는지 모른다. meal item 응답의 `nutrition`
    (섭취량 환산값)과 같은 모델을 쓰기 때문에, 한쪽 규칙이 다른 쪽으로 새면 FE 가
    100g 기준값을 먹은 양으로 표시한다.
    """
    user = make_user(db)
    make_food_ref(
        db,
        food_ref_id="KFD_MIYEOK",
        name="미역국",
        serving_size=Decimal("200.000"),
        calories=Decimal("80.000"),
    )
    db.commit()

    response = client.get(
        _PATH, params={"q": "미역국"}, headers={"X-User-Id": str(user.id)}
    )

    candidate = response.json()["data"]["candidates"][0]
    assert candidate["servingSizeG"] == 200.0
    assert candidate["nutrition"]["kcal"] == 80.0


def test_candidates_is_an_empty_list_when_nothing_matches(client, db):
    """후보 0건은 에러가 아니다 — 사용자는 직접 입력으로 넘어간다."""
    user = make_user(db)
    make_food_ref(db, name="미역국")
    db.commit()

    response = client.get(
        _PATH,
        params={"q": "존재하지않는음식"},
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["candidates"] == []


def test_candidates_limit_caps_the_number_of_results(client, db):
    """`limit` 이 실제로 개수를 자른다. 기본값 5 는 명세서 예시를 따른다."""
    user = make_user(db)
    for index in range(7):
        make_food_ref(db, food_ref_id=f"KFD_EGG_{index}", name=f"계란요리{index}")
    db.commit()

    limited = client.get(
        _PATH, params={"q": "계란", "limit": 2}, headers={"X-User-Id": str(user.id)}
    )
    defaulted = client.get(
        _PATH, params={"q": "계란"}, headers={"X-User-Id": str(user.id)}
    )

    assert len(limited.json()["data"]["candidates"]) == 2
    assert len(defaulted.json()["data"]["candidates"]) == 5


def test_candidates_requires_a_user_header(client, db):
    """다른 모든 공개 라우트와 같은 인증 이음새를 탄다 (README 절대 규칙 6)."""
    make_food_ref(db, name="미역국")
    db.commit()

    response = client.get(_PATH, params={"q": "미역국"})

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_candidates_rejects_a_missing_query(client, db):
    """`q` 없이 부르면 422 다 — 33만건을 통째로 훑는 요청이 되어선 안 된다."""
    user = make_user(db)

    response = client.get(_PATH, headers={"X-User-Id": str(user.id)})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_candidates_rejects_a_single_character_query(client, db):
    """한 글자 검색은 거부한다.

    `밥` 한 글자는 33만건 중 수만 건과 일치해 후보 목록으로 쓸모가 없는데, 비용은
    전체 순차 스캔으로 똑같이 든다. 퍼지 폴백의 두 글자 조각도 만들 수 없다.
    """
    user = make_user(db)

    response = client.get(
        _PATH, params={"q": "밥"}, headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_candidates_rejects_a_limit_beyond_the_cap(client, db):
    """`limit` 상한을 넘기면 422 다 — 한 번의 요청이 33만건 정렬을 통째로 끌고 오지 못하게."""
    user = make_user(db)

    response = client.get(
        _PATH, params={"q": "미역국", "limit": 21}, headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_candidates_rejects_a_query_that_is_only_one_character_after_normalizing(
    client, db
):
    """`" 밥 "` 은 세 글자지만 실제 검색어는 한 글자다.

    길이 검사를 원문에만 걸면 공백·밑줄로 길이를 채운 요청이 그대로 통과해, 앞
    테스트가 막으려던 전체 스캔이 뒷문으로 들어온다. 비교는 이름 매칭과 같은
    정규화(`crud.food.normalize_name`) 뒤에 해야 한다.
    """
    user = make_user(db)

    response = client.get(
        _PATH, params={"q": " 밥_"}, headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_candidates_never_emit_nan(client, db):
    """`NaN` 은 표준 JSON 에 없다 — 한 건만 새어 나가도 FE 의 파싱이 통째로 깨진다.

    Postgres NUMERIC 은 NaN 을 담을 수 있고 공공 DB 적재분에 섞일 수 있다.
    meal item 응답은 `test_meal_items_api.py` 가 같은 것을 막고 있는데, 후보 응답은
    환산을 거치지 않아 다른 경로(`services.nutrition._finite`)로 나간다.
    """
    user = make_user(db)
    make_food_ref(
        db,
        food_ref_id="KFD_NAN",
        name="깨진데이터",
        serving_size=Decimal("NaN"),
        calories=Decimal("NaN"),
    )
    db.commit()

    response = client.get(
        _PATH, params={"q": "깨진데이터"}, headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 200, response.text
    assert "NaN" not in response.text
    candidate = response.json()["data"]["candidates"][0]
    assert candidate["servingSizeG"] is None
    assert candidate["nutrition"]["kcal"] is None


def test_candidates_rejects_a_query_of_only_one_character_tokens(client, db):
    """`"밥 국"` 은 토큰 길이의 합이 2 지만 실제로 검색되는 조각은 전부 한 글자다.

    합으로 재면 `"밥"` 은 막고 `"밥 국"` 은 통과하는데, 뒤쪽이 더 비싸다 — 한 글자
    조각 둘로 33만건을 두 번 훑는다. 앞 테스트가 막으려던 비용이 뒷문으로 들어온다.
    """
    user = make_user(db)

    response = client.get(
        _PATH, params={"q": "밥 국"}, headers={"X-User-Id": str(user.id)}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_candidates_rejects_a_query_with_absurdly_many_words(client, db):
    """단어 수 상한 — 조각 하나마다 33만건 평가가 한 번씩 더 붙기 때문이다.

    실측: 25개 토큰 3.1초, 48자(조각 41개) 4.2초. `limit` 은 이 비용을 못 줄인다 —
    정렬 전에 모든 행을 평가해야 한다. 토큰을 조용히 버리는 대신 거부하는 건, 버리면
    사용자가 친 단어가 검색에 안 들어갔는데도 결과가 그럴듯해 보이기 때문이다.
    """
    user = make_user(db)

    response = client.get(
        _PATH,
        # 원문 38자 — `max_length=50` 에 닿지 않는다. 닿으면 단어 수가 아니라
        # 길이 때문에 422 가 나서 이 테스트가 엉뚱한 것을 지키게 된다.
        params={"q": "가나 다라 마바 사아 자차 카타 파하 거너 더러 머버 서어 저처 커터"},
        headers={"X-User-Id": str(user.id)},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
