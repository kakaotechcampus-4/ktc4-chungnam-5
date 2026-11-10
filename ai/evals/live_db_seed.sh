#!/bin/sh
# postgres 이미지 안에서 실행한다. 실패 시 시드 적재 전체가 롤백된다.
set -eu

expected_sha=43c0962b7f6da1d098647f618aaabad1aeeeb9f1e7763320a166c9c24abfba9b
actual_sha=$(sha256sum /seed/food_refs.dump | cut -d ' ' -f 1)
if [ "$actual_sha" != "$expected_sha" ]; then
    echo "Seed checksum changed; review the experiment snapshot before proceeding." >&2
    exit 1
fi

revision=$(psql -X -At -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')
row_count=$(psql -X -At -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM food_refs')
case "$revision:$row_count" in
    b7e2c4a9d1f3:0)
        pg_restore --data-only --no-owner --exit-on-error --single-transaction \
            --dbname="$PGDATABASE" /seed/food_refs.dump
        echo "Original food_refs seed restored."
        ;;
    b7e2c4a9d1f3:276092|3a9c7e5b2d41:276092)
        echo "Existing experiment seed retained; final migration and counts will be checked."
        ;;
    *)
        echo "Unexpected database state; seed was not inserted, deleted, or overwritten." >&2
        exit 1
        ;;
esac
