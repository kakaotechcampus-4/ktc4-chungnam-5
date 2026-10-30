"""merge duplicate food_refs

Revision ID: 8d3f6a1c5e27
Revises: b7e2c4a9d1f3
Create Date: 2026-10-25 12:00:00.000000

같은 `(category, 정규화한 이름)` 의 행을 하나로 합치고, 영양성분은 **100g 당으로 맞춘
평균**으로 둔다. 공공 DB 에는 이름이 같은 음식이 여러 건씩 있어서(336,351건 → 276,092
그룹) 이름 매칭이 대부분 "여러 건이라 포기" 로 끝났다.

- 대표 행은 그룹에서 `id` 가 가장 작은 행이다. 그 행의 `id` · `name` 이 남는다.
- `category`(GENERAL / PROCESSED)가 다르면 다른 음식이다 — 일반음식 `배추김치` 와
  가공식품 `배추김치` 는 각각 한 건씩 남는다.
- 이름은 `crud.food.normalize_name` 과 같은 규칙(공백·밑줄 제거)으로 묶는다. 그래야
  `달걀_삶은것` 과 `달걀 삶은것` 이 같은 음식이 된다.

지워질 행을 가리키던 참조는 먼저 대표 id 로 옮긴다 — `meal_items.food_ref_id`(FK,
`RESTRICT` 라 안 옮기면 DELETE 가 실패한다), `chat_food_cards.food_ref_id`,
`meal_feedbacks.suggestions[].candidateFoodRefId`(둘은 FK 가 없어 안 옮기면 조용히
끊긴다).

새로 적재할 때도 같은 규칙으로 합친다(`scripts/load_food_refs.py`). 앱 코드를 import
하지 않고 SQL 을 여기 따로 적는 건, 마이그레이션은 적힌 시점의 동작을 그대로 지켜야
하기 때문이다.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8d3f6a1c5e27'
down_revision: Union[str, Sequence[str], None] = 'b7e2c4a9d1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


AVERAGED_COLUMNS = [
    "calories",
    "carbohydrate_g",
    "protein_g",
    "fat_g",
    "fiber_g",
    "cholesterol_mg",
    "saturated_fat_g",
    "trans_fat_g",
    "sodium_mg",
]

# 기준량이 정상인 행만 평균에 넣는다. Postgres 에서는 `'NaN' > 0` 이 참이라 따로 거른다.
_VALID_SERVING = "f.serving_size > 0 AND f.serving_size <> 'NaN'"


def _per_100g_avg(column: str) -> str:
    """성분 하나의 100g 당 평균. 행마다 기준량이 다를 수 있어 원시값을 그대로 평균 내면
    안 된다. 값이 없는(NULL) 행은 `AVG` 가 알아서 건너뛴다 — 그 성분을 아는 행끼리만
    평균을 낸다."""
    return (
        f"ROUND(AVG(f.{column} * 100 / f.serving_size) "
        f"FILTER (WHERE {_VALID_SERVING} AND f.{column} <> 'NaN'), 3)"
    )


# 목록 순서가 곧 실행 순서다. 테스트(`test_food_refs_merge.py`)가 이 목록을
# 그대로 실행해 검증한다.
MERGE_STATEMENTS = [
    # 행 → 대표 id. 식은 `ix_food_refs_name_normalized` 와 같다.
    """
    CREATE TEMP TABLE food_ref_merge ON COMMIT DROP AS
    SELECT id,
           min(id) OVER (
               PARTITION BY category, replace(replace(name, ' ', ''), '_', '')
           ) AS rep_id
      FROM food_refs
    """,
    "ALTER TABLE food_ref_merge ADD PRIMARY KEY (id)",
    """
    UPDATE meal_items AS m
       SET food_ref_id = g.rep_id
      FROM food_ref_merge AS g
     WHERE m.food_ref_id = g.id
       AND g.id <> g.rep_id
    """,
    """
    UPDATE chat_food_cards AS c
       SET food_ref_id = g.rep_id
      FROM food_ref_merge AS g
     WHERE c.food_ref_id = g.id
       AND g.id <> g.rep_id
    """,
    # 배열 순서(WITH ORDINALITY)를 지키며 해당 원소의 id 만 바꾼다.
    """
    UPDATE meal_feedbacks AS mf
       SET suggestions = (
           SELECT jsonb_agg(
                      CASE WHEN g.rep_id IS NOT NULL
                           THEN jsonb_set(e.item, '{candidateFoodRefId}', to_jsonb(g.rep_id))
                           ELSE e.item
                      END
                      ORDER BY e.ord
                  )
             FROM jsonb_array_elements(mf.suggestions) WITH ORDINALITY AS e(item, ord)
             LEFT JOIN food_ref_merge AS g
               ON g.id = e.item->>'candidateFoodRefId' AND g.id <> g.rep_id
       )
     WHERE jsonb_typeof(mf.suggestions) = 'array'
       AND EXISTS (
           SELECT 1
             FROM jsonb_array_elements(mf.suggestions) AS e(item)
             JOIN food_ref_merge AS g
               ON g.id = e.item->>'candidateFoodRefId' AND g.id <> g.rep_id
       )
    """,
    # 대표 행에 평균을 채운다. 한 건짜리 그룹도 같은 식을 거쳐 100g 기준으로 맞춰진다.
    # 기준량을 아는 행이 하나도 없는 그룹은 환산 근거가 없어 성분까지 NULL 이 된다.
    f"""
    UPDATE food_refs AS r
       SET origin_type = a.origin_type,
           serving_size = a.serving_size,
           {", ".join(f"{c} = a.{c}" for c in AVERAGED_COLUMNS)}
      FROM (
           SELECT g.rep_id,
                  -- 식품기원명이 그룹 안에서 갈리면 어느 하나로 대표할 수 없다
                  CASE WHEN count(DISTINCT f.origin_type) = 1
                       THEN min(f.origin_type) END AS origin_type,
                  CASE WHEN bool_or({_VALID_SERVING}) THEN 100 END AS serving_size,
                  {", ".join(f"{_per_100g_avg(c)} AS {c}" for c in AVERAGED_COLUMNS)}
             FROM food_refs AS f
             JOIN food_ref_merge AS g ON g.id = f.id
            GROUP BY g.rep_id
      ) AS a
     WHERE r.id = a.rep_id
    """,
    """
    DELETE FROM food_refs AS f
     USING food_ref_merge AS g
     WHERE f.id = g.id
       AND g.id <> g.rep_id
    """,
    "DROP TABLE food_ref_merge",
]


def upgrade() -> None:
    """Upgrade schema."""
    for statement in MERGE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    # 지운 행과 원래 성분값은 복원할 근거가 DB 에 남아 있지 않다. 원본이 필요하면
    # 엑셀에서 다시 적재해야 한다(`scripts/load_food_refs.py` 이전 버전). 스키마는
    # 바뀌지 않았으므로 되감아도 앞선 리비전과 충돌하지 않는다.
    pass
