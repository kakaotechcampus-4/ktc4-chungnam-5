"""식약처 식품영양성분DB 엑셀을 food_refs 에 적재한다.

사용법:
    python -m scripts.load_food_refs GENERAL   "...\20260828_음식DB_19617건.xlsx"
    python -m scripts.load_food_refs PROCESSED "...\20260828_가공식품DB_316734건.xlsx"

엑셀은 컬럼이 160개를 넘고 파일이 200MB 를 넘기 때문에 openpyxl read_only 로 스트리밍해
staging 테이블에 COPY 한 뒤 upsert 한다. 컬럼은 위치가 아니라 **헤더명**으로 찾는다 —
두 파일의 컬럼 개수가 다르기 때문이다(160 vs 166).

**이름이 같은 음식은 한 행으로 합친다.** 이름(공백·밑줄 제거)으로 묶어 식품코드가 가장
작은 행의 id · 이름을 남기고, 성분은 100g 당으로 맞춘 평균을 넣는다. 한 파일은 한
category 라 일반음식과 가공식품은 서로 섞이지 않는다. 이미 적재된 DB 를 합친 마이그레이션
(`8d3f6a1c5e27`)과 같은 규칙이다 — 한쪽을 고치면 다른 쪽도 고친다.

다른 데이터셋으로 다시 적재하면 대표 id 가 바뀐 그룹은 옛 행이 남는다(upsert 라 지우지
않는다). 옛 행은 `meal_items` 가 FK 로 물고 있을 수 있어 여기서 지우지 않는다.
"""

from __future__ import annotations

import argparse
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl

from app.db.session import engine
from app.models.enums import FoodCategory

# food_refs 컬럼 → 엑셀 헤더명
COLUMN_MAP: dict[str, str] = {
    "id": "식품코드",
    "name": "식품명",
    "origin_type": "식품기원명",
    "serving_size": "영양성분함량기준량",
    "calories": "에너지(kcal)",
    "carbohydrate_g": "탄수화물(g)",
    "protein_g": "단백질(g)",
    "fat_g": "지방(g)",
    "fiber_g": "식이섬유(g)",
    "cholesterol_mg": "콜레스테롤(mg)",
    "saturated_fat_g": "포화지방산(g)",
    "trans_fat_g": "트랜스지방산(g)",
    "sodium_mg": "나트륨(mg)",
}
NUMERIC_COLUMNS = set(COLUMN_MAP) - {"id", "name", "origin_type", "serving_size"}
TARGET_COLUMNS = [*COLUMN_MAP, "category", "dataset_version"]

# 기준량이 정상인 행만 평균에 넣는다. Postgres 에서는 `'NaN' > 0` 이 참이라 따로 거른다.
VALID_SERVING = "s.serving_size > 0 AND s.serving_size <> 'NaN'"

# 이름 그룹 하나를 food_refs 한 행으로 만드는 식. 키 순서는 TARGET_COLUMNS 를 따른다.
MERGED_EXPRS: dict[str, str] = {
    "id": "min(s.id)",
    "name": "(array_agg(s.name ORDER BY s.id))[1]",
    # 식품기원명이 그룹 안에서 갈리면 어느 하나로 대표할 수 없다
    "origin_type": (
        "CASE WHEN count(DISTINCT s.origin_type) = 1 THEN min(s.origin_type) END"
    ),
    # 성분을 100g 당으로 맞췄으니 기준량도 100 이다. 아는 행이 없으면 환산 근거가 없다.
    "serving_size": f"CASE WHEN bool_or({VALID_SERVING}) THEN 100 END",
    # 행마다 기준량이 다를 수 있어 원시값을 그대로 평균 내면 안 된다. 값이 없는(NULL)
    # 행은 AVG 가 건너뛴다 — 그 성분을 아는 행끼리만 평균을 낸다.
    **{
        col: (
            f"ROUND(AVG(s.{col} * 100 / s.serving_size) "
            f"FILTER (WHERE {VALID_SERVING} AND s.{col} <> 'NaN'), 3)"
        )
        for col in NUMERIC_COLUMNS
    },
    "category": "s.category",
    "dataset_version": "min(s.dataset_version)",
}

# "100g", "100ml", "1회 제공량(30g)" 에서 앞쪽 수치를 뽑는다
SERVING_RE = re.compile(r"(\d+(?:\.\d+)?)")


class Stats:
    def __init__(self) -> None:
        self.read = 0
        self.skipped_no_id = 0
        self.bad_numeric = 0
        self.serving_units: dict[str, int] = {}


def to_decimal(value: object, stats: Stats) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "N/A"}:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        stats.bad_numeric += 1
        return None


def to_serving_size(value: object, stats: Stats) -> Decimal | None:
    """'100g' → 100. 단위는 따로 집계해 적재 후 보고한다."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    unit = SERVING_RE.sub("", text).strip().lower() or "(없음)"
    stats.serving_units[unit] = stats.serving_units.get(unit, 0) + 1
    m = SERVING_RE.search(text)
    return Decimal(m.group(1)) if m else None


def to_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def iter_rows(path: Path, category: FoodCategory, dataset_version: str, stats: Stats):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(rows)]

        missing = [h for h in COLUMN_MAP.values() if h not in header]
        if missing:
            raise SystemExit(f"엑셀에 없는 컬럼: {missing}")
        index = {col: header.index(h) for col, h in COLUMN_MAP.items()}

        for row in rows:
            stats.read += 1
            food_id = to_text(row[index["id"]])
            if not food_id:
                stats.skipped_no_id += 1
                continue
            record = {
                "id": food_id,
                "name": to_text(row[index["name"]]) or food_id,
                "origin_type": to_text(row[index["origin_type"]]),
                "serving_size": to_serving_size(row[index["serving_size"]], stats),
                "category": category.value,
                "dataset_version": dataset_version,
            }
            for col in NUMERIC_COLUMNS:
                record[col] = to_decimal(row[index[col]], stats)
            yield tuple(record[c] for c in TARGET_COLUMNS)

            if stats.read % 25_000 == 0:
                print(f"  ... {stats.read:,} 행 읽음", flush=True)
    finally:
        wb.close()


def merge_stage_into_food_refs(cur) -> None:
    """`food_refs_stage` 를 이름 그룹당 한 행으로 합쳐 `food_refs` 에 upsert 한다.

    같은 파일 안에 식품코드가 중복될 수 있어 DISTINCT ON 으로 한 건만 남긴 뒤 묶는다.
    묶는 식은 `ix_food_refs_name_normalized` 와 같다(`crud.food._normalized_name`).
    """
    cols = ", ".join(TARGET_COLUMNS)
    exprs = ", ".join(MERGED_EXPRS[c] for c in TARGET_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in TARGET_COLUMNS if c != "id")
    cur.execute(
        f"INSERT INTO food_refs ({cols}) "
        f"SELECT {exprs} "
        f"FROM (SELECT DISTINCT ON (id) * FROM food_refs_stage ORDER BY id) AS s "
        f"GROUP BY s.category, replace(replace(s.name, ' ', ''), '_', '') "
        f"ON CONFLICT (id) DO UPDATE SET {updates}"
    )


def load(path: Path, category: FoodCategory, dataset_version: str) -> None:
    stats = Stats()
    cols = ", ".join(TARGET_COLUMNS)
    print(f"\n[{category.value}] {path.name}")

    with engine.begin() as conn:
        cur = conn.connection.cursor()
        cur.execute(
            "CREATE TEMP TABLE food_refs_stage "
            "(LIKE food_refs INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        with cur.copy(f"COPY food_refs_stage ({cols}) FROM STDIN") as copy:
            for record in iter_rows(path, category, dataset_version, stats):
                copy.write_row(record)

        merge_stage_into_food_refs(cur)
        inserted = cur.rowcount
        cur.execute("SELECT count(*), count(DISTINCT id) FROM food_refs_stage")
        staged, distinct_ids = cur.fetchone()

    print(f"  읽은 행       : {stats.read:,}")
    print(f"  staging 적재  : {staged:,}  (식품코드 없는 행 {stats.skipped_no_id:,}건 제외)")
    print(f"  중복 식품코드 : {staged - distinct_ids:,}건 제외")
    print(f"  food_refs 반영: {inserted:,}  (같은 이름 {distinct_ids - inserted:,}건을 평균으로 병합)")
    if stats.bad_numeric:
        print(f"  숫자 변환 실패: {stats.bad_numeric:,}개 셀 → NULL")
    units = sorted(stats.serving_units.items(), key=lambda kv: -kv[1])
    print("  기준량 단위   : " + ", ".join(f"{u} {c:,}건" for u, c in units[:6]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("category", choices=[c.value for c in FoodCategory])
    parser.add_argument("excel_path", type=Path)
    parser.add_argument("--dataset-version", default="20260828")
    args = parser.parse_args()

    if not args.excel_path.exists():
        raise SystemExit(f"파일 없음: {args.excel_path}")
    load(args.excel_path, FoodCategory(args.category), args.dataset_version)


if __name__ == "__main__":
    sys.exit(main())
