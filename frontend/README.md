# frontend — Flutter App (Android)

사용자가 매 끼니 식사를 기록하고 Q/Q/S 점수·피드백을 확인하는 **네이티브 앱**.

- 스택: **Flutter / Android 우선** (D1, APK 직배포 — 스토어 심사 불필요)

---

## 왜 웹이 아니라 네이티브인가

**식사 전 포만감 알림**이 이 서비스의 핵심 사용 흐름인데, 백그라운드 푸시가 웹(특히 iOS Safari)에서 안 된다.
이 하나 때문에 네이티브를 골랐다 — 다른 이유는 부차적이다.

---

## 이 파트가 지켜야 하는 절대 규칙

1. **총점·가중치 숫자를 화면에 노출하지 않는다.** 표시되는 건 Q/Q/S 각 1~5점과
   `stageEmphasis`(이번 단계에서 가장 엄격한 축 이름)뿐이다 (D9).
2. **처방 톤 금지.** "이만큼 드세요"가 아니라 "평소 대비 이만큼" 같은 서술형으로만 표현한다.
   서버가 `safetyStatus=BLOCKED`를 내리면 피드백 대신 **상담 안내 화면**을 띄운다.
3. **점수는 즉시, 피드백은 늦게.** 확정 버튼을 누르면 Q/Q/S는 동기 응답으로 바로 뜬다.
   피드백 문장만 나중에 채워진다. 피드백이 실패해도 점수 화면은 정상이다.
4. **이미지는 서버를 경유하지 않는다.** BE에서 presigned URL을 받아 **S3로 직접 업로드**한다.
5. **민감정보를 로컬에 남기지 않는다.** 약제명·용량을 캐시·로그에 쓰지 않는다.
   토큰은 secure storage에만 저장한다.

---

## 핵심 화면 흐름 — 한 끼 기록

```
 ① 사진 촬영      → presigned URL 요청 → S3 직접 업로드
 ② 분석 요청      → 202 응답 + mealId   (0.1초)
 ③ 진행 확인      → 1.5초 폴링          (인식 10~30초)
 ④ 결과 확인·수정 → 음식 항목 교정 + 식전/식후 포만감 입력
 ⑤ 확정          → Q/Q/S 즉시 표시     (동기)
 ⑥ 피드백 도착   → 폴링, 45초 초과 시 푸시로 전환
```

**폴링 규칙:** 1.5초 간격 · 45초 초과 시 폴링을 끊고 FCM 푸시로 인계한다.
앱이 백그라운드로 가도 결과를 놓치지 않게 하기 위함이다.

**포만감은 사용자가 직접 입력한다** (AI가 추정하지 않는다).
식전 %와 식후 %를 받아 그 델타로 점수를 낸다 (D5 결정 문서 §5).

---

## 화면 목록 (MVP)

| 화면 | 내용 |
|---|---|
| 온보딩 · 로그인 | 카카오 OAuth (D3) |
| 포즈 정보 입력 | 단계 분류에 필요한 최소 정보 + **포즈 전 평소 식사 사진** (개인 baseline 산출용, D8) |
| 홈 | 오늘의 끼니 · 단계 표시 · 다음 식사 알림 |
| 식사 기록 | 위 ①~⑥ 흐름 |
| 결과 상세 | Q/Q/S 3축 + 피드백 문장 |
| 주간 추이 | 포즈 전 대비 변화 (before/after 비교) |
| 설정 | 알림 시간 · 알레르기/못 먹는 음식 |

> MVP 제외: 목표 체중, 알람 늘리기, 부작용·스트레스 입력, 포만감 단독 기록, 증상 트래킹.
> 지금 스코프 밖이며, 편해 보여도 되돌리지 않는다.

---

## 상태 처리

앱은 서버의 `meals.status` 4개를 그대로 따른다.

| status | 앱 동작 |
|---|---|
| `ANALYZING` | 진행 인디케이터 + 폴링 |
| `REVIEW_REQUIRED` | 인식 결과 교정 화면 (`clarifyQuestion`이 있으면 함께 표시) |
| `EVALUATED` | Q/Q/S 표시. `feedbackStatus`는 별도로 폴링 |
| `FAILED` | 재시도 안내 |

---

## 실행

```bash
flutter pub get
flutter run                       # 디버그
flutter build apk --release       # 배포용 APK
```

## 웹 (평가용 창구)

이 프로젝트는 앱 서비스이고, 웹은 **평가자가 링크 하나로 바로 접속하게 하는 창구**일 뿐이다.
웹 파일은 BE 이미지 안에 들어가 `https://<도메인>/web/` 에서 내려가고, API(`/api/v1`)와 같은
도메인이라 CORS가 없다.

```bash
flutter build web --release --base-href /web/
```

- **웹 빌드에 `DEMO_KEY`를 넣지 않는다.** 웹 파일은 키 없이 받을 수 있어서, 빌드에 박으면 도메인을 아는
  누구나 JS에서 키를 꺼낸다. 키는 평가자에게 주는 링크에 담는다:
  `https://<도메인>/web/?k=<데모 키>`.
- 링크를 열면 앱이 `k`를 브라우저(localStorage)에 저장하고 **주소창에서 지운다**. 이후에는 `/web/`만
  열어도 저장된 키를 쓴다. 키가 없거나 틀리면 API가 403이고 화면은 "평가용 링크로 접속해 주세요"를 띄운다.
  구현: `lib/api/web_demo_key*.dart` (앱 빌드는 빈 함수).
- 웹 릴리스 빌드의 API 주소는 웹을 내려받은 도메인(`Uri.base.origin` + `/api/v1`)이다. phase와
  `API_BASE_URL`은 무시한다. **디버그 실행**(`flutter run -d chrome`)은 앱처럼 phase 주소를 쓴다.
- 사진(`Image.network`)도 웹에서 `X-Demo-Key` 헤더가 실제로 붙는다(Flutter 3.44 에서 확인).
- 웹은 브라우저마다 사용자가 따로 생긴다(`userId`가 localStorage에 저장). 평가자 한 명당 브라우저 하나.
- **Windows Git Bash에서는** `--base-href /web/` 의 `/web/`을 경로(`C:/Program Files/Git/web/`)로 바꿔 버려
  빌드가 실패한다. `MSYS_NO_PATHCONV=1 flutter build web ...` 로 실행하거나 PowerShell을 쓴다.
- 링크는 평가자에게만 직접 전달한다(= 키). 공개 채널·README·PR에 올리지 않는다.

## 환경변수

`--dart-define` 또는 `.env`(gitignore됨)로 주입한다. **키를 소스에 하드코딩하지 않는다.**

| 키 | 설명 |
|---|---|
| `APP_PHASE` | 붙을 환경: `local`(기본) · `dev` · `staging` · `prod`. 아래 표 참고. 잘못 적으면 앱 시작 때 바로 터진다 |
| `API_BASE_URL` | BE 공개 API 주소 (`/api/v1` 포함). 주면 phase 주소 대신 쓴다. **`local` · `dev` 에서만** 쓸 수 있다(에뮬레이터 `10.0.2.2`·`adb reverse` 용). `staging` · `prod` 에서 주면 시작 때 터진다 |
| `DEMO_KEY` | 데모 서버 접근 키(`X-Demo-Key`). `staging` · `prod` **앱** 빌드에만 넣고, 그 서버의 키를 넣는다. **웹 빌드에는 넣지 않는다**(위 "웹" 참고) |
| `KAKAO_NATIVE_APP_KEY` | 카카오 로그인 |
| `FCM_SENDER_ID` | 푸시 |

### phase — 서버 주소가 자동으로 정해진다

| phase | 서버 | 키 | 용도 |
|---|---|---|---|
| `local` (기본) | `localhost:4010` — `frontend/mock` 의 mock 서버(고정 응답) | 없음 | 서버 없이 화면 보기 |
| `dev` | `127.0.0.1:8000` — 내 PC Docker BE | 없음 | 실제 BE 로 개발 |
| `staging` | `https://glp1-staging.duckdns.org/api/v1` — develop 코드, 운영과 DB·사진 분리 | 스테이징 키 | 머지된 변경 확인·시험 데이터 |
| `prod` | `https://glp1-demo.duckdns.org/api/v1` — main 코드 | 운영 키 | 데모·평가 |

```bash
flutter run --dart-define=APP_PHASE=dev
flutter build apk --release --dart-define=APP_PHASE=staging --dart-define=DEMO_KEY=<스테이징 키>
flutter build apk --release --dart-define=APP_PHASE=prod    --dart-define=DEMO_KEY=<운영 키>
```

- 스테이징 키로 운영 서버에, 운영 키로 스테이징 서버에 붙으면 403 이다(서버가 키를 나누면 — 인프라 담당).
- 환경은 헤더가 아니라 **빌드(phase)** 로 나눈다. 키는 환경 구분이 아니라 JWT 전까지 쓰는 임시 접근 장치다.

---

## 참고

- 개발 규칙: [`../CLAUDE.md`](../CLAUDE.md)
