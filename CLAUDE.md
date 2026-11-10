# 든든지기 — GLP-1 투약 단계별 식사 코치

카카오테크 캠퍼스 4기 2단계 팀 프로젝트 · 충남대 5팀 모노레포.
식사 사진 → AI 음식 인식 → 투약 단계별 Q/Q/S 점수 + 피드백을 주는 Android 앱.

- 프로젝트 소개 · 로컬 실행 · 배포: [README.md](README.md)
- 테크스펙 · 협업문서: https://kakaotechcampus-4.github.io/ktc4-chungnam-5/project/
- 이 파일은 팀 공용이다. 개인 환경 · 개인 워크플로는 `CLAUDE.local.md`(커밋하지 않음)에 둔다.

---

## 파트와 규칙 문서

작업하는 파트의 README 「이 파트가 지켜야 하는 절대 규칙」을 **먼저 읽는다.** 규칙 본문은 거기가 정본이다.

| 디렉터리 | 파트 코드 | 정본 |
|---|---|---|
| `frontend/` | `FE` | [frontend/README.md](frontend/README.md) |
| `backend/` | `BE` | [backend/README.md](backend/README.md) · 트랜잭션 규약 `backend/app/crud/__init__.py` |
| `ai/` | `AI` | [ai/README.md](ai/README.md) · eval [ai/evals/README.md](ai/evals/README.md) |
| `ai-stub/` | `BE` | BE ↔ AI 계약 `ai-stub/schemas.py` |
| `infra/` | `INFRA` | [infra/README.md](infra/README.md) |
| 루트 파일 · `docs/` | `COMMON` | — |

파트를 가로지르는 금지 사항 (자세한 건 각 README):

- **의료 판단 · 처방 톤 금지.** 증량 · 감량 · 단약 판단을 하지 않고, "이만큼 드세요" 대신 "평소 대비 이만큼" 처럼 서술한다.
- **총점 · 가중치를 만들지도 보여주지도 않는다.** 단계별 차이는 기준선의 엄격함으로 표현한다.
- **약제 · 용량 · 이미지 키를 로그 · 캐시에 남기지 않는다.**
- **AI 는 성분 숫자를 생성하지 않는다.** 음식만 지목하고 성분은 BE 가 `food_refs` 에서 채운다.

## 자주 쓰는 명령

```bash
cd backend && pytest -q          # Testcontainers 로 Postgres 를 띄운다 → Docker 가 켜져 있어야 한다
cd ai && python -m pytest -q     # 모델 호출은 모킹 — 키 불필요. CI(AI tests)와 같은 명령
cd frontend && flutter analyze && flutter test
```

- 테이블 정의의 정본은 `backend/app/models/` 다. 스키마를 바꾸면 Alembic revision 을 추가하고(`--autogenerate` 결과는 눈으로 검토), 이미 머지된 revision 은 고치지 않는다.
- 로컬 실행 · 포트 · `.env` 설정은 [README.md](README.md#로컬-실행) 를 따른다.

---

## 커밋 메시지

```
[파트] 타입: 요약
[파트-이슈번호] 타입: 요약
```

```
[BE] feat: 공공 DB 에 매칭된 음식은 확인 화면에 DB 음식명을 보여준다
[FE-37] feat: 끼니 피드백 화면을 읽기 편하게 다듬는다
[HOTFIX] fix: 포만감 체크인 팝업에 끼니 사진을 보여 준다
[INFRA] feat: 평가용 웹을 api 이미지에 넣어 /web 에서 내려준다
[COMMON] docs: 루트 README에 테크스펙·협업문서 페이지 링크를 추가한다
```

- **파트**: `BE` · `FE` · `AI` · `INFRA` · `COMMON` · `HOTFIX`
  - 노션 이슈에 속하면 번호를 붙인다(`[FE-37]`). 이슈 밖 작업은 번호를 뺀다.
  - `HOTFIX` 는 `hotfix/…` 브랜치의 커밋에 쓴다.
- **타입**: `feat` · `fix` · `refactor` · `docs` · `style` · `chore` · `test` · `ci` · `move`
- **요약**: 한국어, 50자 이내, 마침표 없음. **무엇을** 했는지 동사로 끝낸다("…한다").
- **본문**(선택): 판단이 필요했던 커밋에는 **왜** 를 쓴다.
- 두 파트를 함께 건드리면 커밋을 나눈다.
- 한 이슈에 커밋이 여러 개인 게 정상이다. 하나로 몰지 않는다.

### 🚨 `#숫자` 를 쓰지 않는다

GitHub 은 `#5` 를 **이 저장소의 5번 PR** 로 자동 링크한다. 노션 이슈 번호는 `[BE-5]` 형태로만 쓴다.
노션 링크는 커밋이 아니라 PR 본문에만 건다.

---

## 브랜치

```
{파트}/{이슈번호}-{요약}          fe/37-meal-feedback-readability-screen · be/5-meals-create
{파트}/{이슈번호}-{타입}-{요약}    ai/6-feat-food-db-matching · be/5-feat-user-api
{파트}/{타입}-{요약}              be/feat-chatbot-API · infra/feat-staging   ← 이슈 없는 작업
hotfix/{요약}                     hotfix/fe-checkin-photo
```

- 파트: `be` · `fe` · `ai` · `infra` · `common` · `hotfix` (소문자)
- 요약은 영문 + 하이픈
- `develop` 에서 분기하고 `develop` 으로 PR 한다. 작업 브랜치끼리 머지하지 않는다.
- 한 브랜치 = 한 이슈.

## PR · 머지 흐름

```
작업 브랜치 ──PR──▶ develop ──(스테이징)──▶ develop → main PR(멘토 리뷰) ──▶ 버전 태그 push ──▶ 운영 배포
```

- `main` · `develop` 에 직접 push 하지 않는다.
- 팀 내부 PR(→ `develop`)은 같은 파트 1명 승인 후 머지한다.
- 멘토 리뷰용 PR 은 **base 가 `main`** 이어야 멘토가 자동 지정된다 (GitHub 기본값은 `develop`).
- `hotfix/…` 는 `develop` 으로 PR 하고, 운영에 바로 내보내야 하면 `main` 에도 PR 한다.
- 운영 배포는 `main` 의 버전 태그(`vYYYY.MM.DD`)로만 한다 — [README.md](README.md#배포).

## 건드리지 않는 것

- `.github/CODEOWNERS` 의 [운영진 영역], `.github/workflows/` 의 `assign-mentor` · `notify-discord` · `convention-check` — 운영진 소유. 멘토 배정 · 알림이 깨진다.
- `.env` · `.env.prod` 등 비밀값 파일 — 커밋하지 않는다. 예시는 `*.env.example` 에만.
- 다른 파트 디렉터리 — 고칠 일이 생기면 해당 파트 담당과 먼저 맞춘다.

## 노션 이슈 번호

이슈는 노션에서 관리한다 (GitHub 이슈는 만들지 않는다).

- 번호는 불변이다. 중간에 끼워넣지 않고, 취소된 번호는 재사용하지 않는다.
- 노션 이슈 제목 맨 앞에 코드를 둔다 — `BE-5 사용자 프로필 API 구현`.
