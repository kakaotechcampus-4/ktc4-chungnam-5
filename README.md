# ktc4-team-24
카카오테크 캠퍼스 4기 2단계 팀 프로젝트 — 충남대 5팀

## 문서

- [테크스펙 + 협업문서](https://kakaotechcampus-4.github.io/ktc4-chungnam-5/project/) — GitHub Pages
  - 테크스펙: 요구사항 · 아키텍처 설계 · 인터페이스 명세 · 데이터 모델 · 테스트 전략 · 리스크
  - 협업문서: 그라운드 룰 · 브랜치 전략 · 코딩 컨벤션 · 코드 리뷰 기록

## 프로젝트 소개

GLP-1 계열 약을 쓰는 사용자가 매 끼니 식사 사진을 올리면, AI 가 음식을 인식하고
**Q/Q/S(Quality · Quantity · Satiety) 점수**와 피드백을 돌려주는 Android 앱 서비스다.
점수는 투약 단계(`PRE_DOSE` · `INITIAL` · `TITRATION` · `MAINTENANCE`)별 기준선과
사용자의 평소 한 끼(baseline)를 기준으로 매긴다. 의료 판단(증량·감량·단약)은 하지 않는다.

---

## 저장소 구성

| 디렉터리 | 내용 | 스택 | 자세한 문서 |
|---|---|---|---|
| [`frontend/`](frontend/) | 사용자 앱 (Android 우선). 평가용 웹 빌드도 여기서 나온다 | Flutter 3.44 | [frontend/README.md](frontend/README.md) |
| [`backend/`](backend/) | 공개 API · 내부 API · 비동기 워커 · Q/Q/S 채점 | Python 3.12 · FastAPI · SQLAlchemy · Alembic · PostgreSQL | [backend/README.md](backend/README.md) |
| [`ai/`](ai/) | 음식 인식 · 피드백 생성 · 챗봇 코치 (LLM 호출) | Python 3.12 · FastAPI | [ai/README.md](ai/README.md) |
| [`infra/`](infra/) | 로컬 Docker Compose · 운영/스테이징 배포 스크립트 · Caddy · 영양DB 시드 | Docker Compose · Caddy · AWS EC2/SSM | [infra/README.md](infra/README.md) |
| [`docs/`](docs/) | 설계 문서 (Q/Q/S 채점 규칙 등) | | |
| [`.github/workflows/`](.github/workflows/) | CI (AI 회귀 테스트 · 컨벤션 검사) · 배포 | GitHub Actions | |


## 로컬 실행

### 준비물

| 도구 | 버전 | 용도 |
|---|---|---|
| Docker Desktop (Compose v2) | 최신 | DB · AI · BE 컨테이너 |
| Python | 3.12 | backend · ai 를 venv 로 띄울 때 |
| Flutter SDK | 3.44.x (`backend/Dockerfile` 의 `FLUTTER_VERSION` 과 같게) | 앱 실행 · 빌드 |
| Node.js | 18 이상 | FE mock 서버 (`frontend/mock`) — 서버 없이 화면만 볼 때 |

AI 서버는 실제 LLM 을 부른다. `LLM_API_KEY` · `LLM_VISION_URL` · `LLM_REASONING_URL` · `LLM_COACH_URL`
값은 팀 비밀 채널에서 받아 `ai/.env` 에만 넣는다 (커밋 금지). 키가 없으면 AI 서버가 부팅 검증에서 멈춘다.

### 포트

| 서비스 | 호스트 포트 | 비고 |
|---|---|---|
| BE api | 8000 | Swagger: http://127.0.0.1:8000/docs |
| ai | 8002 | 컨테이너 안에서는 `ai:8000` |
| PostgreSQL | 5433 | 컨테이너 안에서는 `db:5432` |
| FE mock 서버 | 4010 | `APP_PHASE=local` 이 붙는 곳 |

### 1. 전부 Docker 로 띄우기

DB · AI · api · worker 를 모두 컨테이너로 띄운다. 가장 간단한 방법이다.

```powershell
cp ai/.env.example ai/.env      # LLM 키 · URL 을 채운다
# 컨테이너끼리는 서비스 이름으로 붙으므로 ai/.env 의 BE 주소도 바꾼다:
#   BE_INTERNAL_BASE_URL=http://api:8000/internal/v1

cd infra
$env:BE_AI_SERVICE_BASE_URL = "http://ai:8000"   # BE 를 실제 AI 에 붙인다 (Git Bash: export BE_AI_SERVICE_BASE_URL=http://ai:8000)
docker compose `
  -f docker-compose.yml `
  -f docker-compose.ai.yml `
  -f docker-compose.be.yml `
  up -d --build
```

> `BE_AI_SERVICE_BASE_URL` 을 빼먹으면 BE 가 기본값인 `ai-stub` 주소를 불러 식사 분석이 전부 실패한다.
> 같은 터미널에서 `up` 할 때마다 설정돼 있어야 한다.

처음 한 번은 스키마를 맞추고 공공 영양DB 시드(약 33만 건)를 넣는다.

```powershell
docker exec glp1-api alembic upgrade head
docker exec -i glp1-db pg_restore -U glp1 -d glp1_dev --data-only --no-owner < seed/food_refs_20260828.dump
```

> PowerShell 은 `<` 리다이렉션을 지원하지 않으므로 시드 복원 줄은 **Git Bash** 에서 실행한다.

확인: http://127.0.0.1:8000/docs 와 http://127.0.0.1:8002/health 가 열리면 된다.
내릴 때는 같은 `-f` 묶음으로 `down` 한다 (`--remove-orphans` 는 쓰지 않는다 — `-f` 에 빠진 컨테이너가 지워진다).

```powershell
docker compose -f docker-compose.yml -f docker-compose.ai.yml -f docker-compose.be.yml down
```

### 2. 백엔드를 venv 로 띄우기 (BE 개발용)

코드를 고치며 `--reload` 로 돌릴 때. DB 와 AI 만 컨테이너로 띄운다.

```powershell
cd infra
docker compose -f docker-compose.yml -f docker-compose.ai.yml up -d --build

cd ../backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1               # macOS · Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`backend/.env` 에서 AI 관련 두 줄을 바꾼다. BE 는 호스트에, AI 는 컨테이너에 있기 때문이다.

```dotenv
AI_SERVICE_BASE_URL=http://localhost:8002
# AI 컨테이너가 사진을 받아 가는 주소 — 컨테이너에서 127.0.0.1 은 자기 자신이다
LOCAL_STORAGE_BASE_URL=http://host.docker.internal:8000/media
```

```powershell
alembic upgrade head
# 시드 복원은 1번과 같다 (처음 한 번)

uvicorn app.main:app --reload              # API  — http://127.0.0.1:8000/docs
python -m app.worker_main                  # Worker — 터미널을 하나 더 열어서
```

세팅 확인 · 큐 상태 보기 · 마이그레이션 작성법은 [backend/README.md](backend/README.md) 와
[infra/README.md](infra/README.md) 를 본다.

### 3. AI 서버를 venv 로 띄우기 (AI 개발용)

```bash
cd ai
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # LLM 키 · URL 을 채운다 (커밋 금지)
uvicorn app.main:app --reload --port 8002
```

이때 BE 를 venv 로 띄웠다면 `backend/.env` 는 `AI_SERVICE_BASE_URL=http://localhost:8002`,
`LOCAL_STORAGE_BASE_URL=http://127.0.0.1:8000/media`(기본값) 그대로 쓴다.

### 4. 앱 (Flutter)

```bash
cd frontend
flutter pub get

# 서버 없이 화면만 — mock 서버(고정 응답)에 붙는다
(cd mock && npm start)                     # 별도 터미널, :4010
flutter run                                # APP_PHASE=local (기본)

# 로컬 BE 에 붙기 (위 1 또는 2 를 띄운 상태)
flutter run --dart-define=APP_PHASE=dev
```

| `APP_PHASE` | 붙는 서버 |
|---|---|
| `local` (기본) | `localhost:4010` — mock 서버 |
| `dev` | `127.0.0.1:8000` — 내 PC 의 BE |
| `staging` | 스테이징 서버 (develop) — 스테이징 데모 키 필요 |
| `prod` | 운영 서버 (main) — 운영 데모 키 필요 |

Android 에뮬레이터에서 로컬 BE 에 붙을 때는 `--dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1`
을 주거나 `adb reverse tcp:8000 tcp:8000` 을 쓴다.

### 테스트

```bash
cd backend && pytest       # BE (로컬 DB 필요)
cd ai && pytest            # AI (모델 호출은 모킹 — 키 불필요)
cd frontend && flutter test
```

---

## 배포

운영과 스테이징은 **EC2 1대**에 Docker Compose 로 함께 올라간다. 서버 접속은 SSH 가 아니라
**AWS SSM Session Manager** 로 하고, 외부에 열린 포트는 Caddy 의 80 · 443 뿐이다.
상세한 절차 · 롤백 · 장애 대응은 [infra/README.md](infra/README.md#운영-배포) 에 있다.

| 환경 | 브랜치 | 서버 경로 | 배포 스크립트 | compose 프로젝트 |
|---|---|---|---|---|
| 운영 | `main` | `/opt/glp1` | `infra/deploy.sh` | `glp1-prod` |
| 스테이징 | `develop` | `/opt/glp1-staging` | `infra/deploy-staging.sh` | `glp1-staging` |

### 브랜치 흐름

```
be/feat-… · fe/… · ai/…  ──PR──▶  develop  ──(스테이징 배포)──▶  main  ──(버전 태그 push)──▶  운영 배포
```

- `main` 에 머지된 커밋만 운영에 배포할 수 있다 (`deploy.sh` 가 거절한다). 핫픽스도 `main` 에 머지한 뒤 배포한다.

### 운영 배포 — 버전 태그 push (기본)

```bash
git checkout main && git pull
git tag v2026.11.09          # 같은 날 두 번째부터 v2026.11.09.1, .2 …
git push origin v2026.11.09
```

태그가 push 되면 GitHub Actions [`Deploy (EC2)`](.github/workflows/deploy.yml) 가
OIDC 로 배포 역할을 받아 `aws ssm send-command` 로 서버의 `infra/deploy.sh <SHA>` 를 실행하고,
`/health` 응답의 `sha` 가 이번 커밋인지까지 확인한다. AWS 액세스 키는 쓰지 않는다.

급할 때는 Actions 탭 → **Deploy (EC2)** → Run workflow(`main`) 로 수동 실행하거나,
Session Manager 에서 직접 실행한다.

```bash
/opt/glp1/infra/deploy.sh                # origin/main
/opt/glp1/infra/deploy.sh v2026.11.09    # 특정 태그 또는 main 의 커밋 SHA
```

> `docker compose up` 을 직접 부르지 말고 항상 `deploy.sh` 를 쓴다. 이미지 태그가 비어 api · worker 이미지를 찾지 못한다.

### Blue/Green 무중단 배포

api 는 `api-blue` · `api-green` 두 벌이고 Caddy 는 한쪽에만 트래픽을 보낸다. `deploy.sh` → `bg-deploy.sh` 흐름:

1. 쉬고 있는 쪽 이미지를 빌드한다 (그동안 지금 쪽이 계속 받는다)
2. 워커를 내리고 `migrate`(`alembic upgrade head`)로 스키마를 맞춘다 — **옛 코드와 호환되는 마이그레이션만** 넣는다 (Expand / Contract)
3. 새 쪽을 띄우고 `/health/ready` 가 healthy 가 될 때까지 기다린다
4. Caddy upstream 을 새 쪽으로 바꾸고 `caddy reload` (연결이 끊기지 않는다)
5. 15초 뒤 옛 쪽 api 를 **정지만** 해 둔다 (롤백용)
6. worker · ai 를 새 이미지로 올린다

1~3 에서 실패하면 전환하지 않고 옛 버전이 계속 서비스한다.

### 롤백

```bash
/opt/glp1/infra/bg-switch.sh prod                 # 정지해 둔 옛 쪽으로 되돌린다 (재빌드 없음, 약 20초)
/opt/glp1-staging/infra/bg-switch.sh staging
```

DB 는 되돌리지 않는다. 호환되지 않는 마이그레이션이 들어갔다면 `alembic downgrade` 뒤 이전 태그로 다시 배포한다
([infra/README.md](infra/README.md#다시-배포-bluegreen) 참고).

### 스테이징 배포

```bash
/opt/glp1-staging/infra/deploy-staging.sh            # origin/develop
/opt/glp1-staging/infra/deploy-staging.sh <커밋 SHA>
```

운영과 같은 Caddy(80/443)를 도메인으로 나눠 쓰고, DB · 사진 · 데모 키는 운영과 분리돼 있다.
`deploy.sh` 와 같은 잠금을 써서 두 배포가 동시에 돌지 않는다.

### 서버 최초 구성

```bash
sudo mkdir -p /opt/glp1 && sudo chown ubuntu:ubuntu /opt/glp1
git clone https://github.com/kakaotechcampus-4/ktc4-chungnam-5.git /opt/glp1 && cd /opt/glp1/infra
cp .env.prod.example .env.prod && vi .env.prod      # 비밀값은 openssl rand -hex 32 로 만든다
chmod 600 .env.prod
./deploy.sh

# 공공 영양DB 시드 — 처음 한 번만
docker exec -i glp1-prod-db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --no-owner' \
  < seed/food_refs_20260828.dump
```

`.env.prod` 에 들어가는 키 목록은 [`infra/.env.prod.example`](infra/.env.prod.example) 에 있다.
GitHub 저장소 Variables 로 `AWS_ACCOUNT_ID` · `EC2_INSTANCE_ID` · `DEPLOY_DOMAIN` 이 필요하다 (Secrets 아님).

### 앱 · 웹 배포

- **Android 앱** — APK 직배포. 붙을 서버의 phase 와 데모 키를 넣어 빌드한다.

  ```bash
  flutter build apk --release --dart-define=APP_PHASE=prod    --dart-define=DEMO_KEY=<운영 키>
  flutter build apk --release --dart-define=APP_PHASE=staging --dart-define=DEMO_KEY=<스테이징 키>
  ```

- **평가용 웹** — 따로 배포하지 않는다. BE 이미지(`backend/Dockerfile` 의 `release` 단계)가
  `flutter build web --base-href /web/` 결과를 품고 api 가 `/web/` 에서 내려준다. 그래서 BE 배포 · 롤백 때
  웹도 같은 버전으로 함께 바뀐다. 접속 링크(`/web/?k=<데모 키>`)는 키를 담고 있으므로 **평가자에게만 직접**
  전달하고 README · PR · 공개 채널에 올리지 않는다.
