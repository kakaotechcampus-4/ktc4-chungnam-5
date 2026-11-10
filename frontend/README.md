# frontend — Flutter App (Android)

사용자가 매 끼니 식사를 기록하고 Q/Q/S 점수·피드백을 확인하는 **네이티브 앱**.

- 스택: **Flutter / Android 우선** (APK 직배포 — 스토어 심사 불필요) · 상태관리 Provider · HTTP dio

---

## 왜 웹이 아니라 네이티브인가

**식사 전 포만감 알림**이 이 서비스의 핵심 사용 흐름인데, 백그라운드 푸시가 웹(특히 iOS Safari)에서 안 된다.
이 하나 때문에 네이티브를 골랐다 — 다른 이유는 부차적이다.

---

## 이 파트가 지켜야 하는 절대 규칙

1. **총점·가중치를 화면에 노출하지 않는다.** 보여 주는 건 양·질·포만도 각각(0~100 정수)과
   라벨(`부족` · `적정` · `과다` 등)뿐이다. 평가 전이거나 영양정보가 없으면 값이 `null` 이다 —
   0 으로 바꿔 그리지 않고 "정보 없음"으로 보여 준다.
2. **처방 톤 금지.** "이만큼 드세요"가 아니라 "평소 대비 이만큼" 같은 서술형으로만 표현한다.
   서버가 `safetyStatus=BLOCKED`(의료 질문 감지)를 내리면 피드백 대신 상담 안내를 보여 준다.
3. **점수는 즉시, 피드백은 늦게.** 확정(`POST /meals/{id}/confirm`)하면 Q/Q/S 는 응답으로 바로 뜬다.
   피드백 문장만 나중에 채워진다(폴링). 피드백이 실패해도 점수 화면은 정상이고, 다시 시도할 수 있다.
4. **사진은 서버를 거쳐 올린다.** 앱이 BE 로 `multipart` 업로드한다(`POST /meals`). 서버가 준 사진은
   `Image.network` 가 아니라 `ApiImage` 로 그린다 — 사진 요청에도 `X-Demo-Key` 가 붙어야 해서다.
5. **민감정보를 로컬·로그에 남기지 않는다.** 약제명·용량을 캐시·로그에 쓰지 않는다. 기기에 저장하는 건
   `userId` 뿐이다(`shared_preferences`).
6. **키를 소스에 넣지 않는다.** `DEMO_KEY` 는 빌드 때 `--dart-define` 으로만 넣는다(아래 "환경변수").

---

## 핵심 화면 흐름 — 한 끼 기록

```
 ① 식사 입력   사진 촬영·선택 / 음식 검색으로 직접 고르기 / "이번 끼니 안 먹었어요"
 ② 분석       (사진일 때) 202 + mealId → 폴링   (직접 고른 음식·식사 안 함은 분석 없이 다음으로)
 ③ 음식 확인   인식된 음식 교정 · 양 수정(−/＋ 또는 숫자 직접 입력) · 음식 추가·삭제 · 영양정보 보완
 ④ 식사 평가   식후 포만감 입력 → 확정 → 양·질·포만도 점수
 ⑤ 끼니 피드백 점수 + 영양소 상태 + AI 피드백 문장 + 다음 음식 제안 / "AI 코치에게 다른 음식 추천받기"
 ⑥ 사후 체크인 식사 후 일정 시간이 지나면 포만감 체크인 팝업
```

**폴링 규칙:** 1.5초에서 시작해 점점 늘려 최대 8초 간격, 총 60초까지 기다린다(`lib/common/polling.dart`).
그 안에 안 끝나면 "아직 만드는 중" 상태로 두고 다시 시도할 수 있게 한다. 푸시 알림은 아직 없다.

**포만감은 사용자가 직접 입력한다** (AI 가 추정하지 않는다). 식전 %는 입력 때, 식후 %는 평가 화면에서 받고,
식후 일정 시간 뒤 체크인 팝업에서 한 번 더 기록한다.

---

## 화면 목록

앱을 처음 열면 `userId` 가 없는 사용자는 **프로필 입력 → 투약 정보 입력** 온보딩을 거친다.
이후 하단 탭 4개(홈 · 피드백 · AI · 마이)가 루트다(`lib/navigation/root_shell.dart`).

| 화면 | 파일 | 내용 |
|---|---|---|
| 프로필 입력 | `profile_input_screen.dart` | 온보딩 1/2. 닉네임·키·체중·평소 한 끼 열량(한국인 평균 안내 포함). 마이 탭에서는 수정 모드 |
| 투약 정보 입력 | `medication_info_screen.dart` | 온보딩 2/2. 투약 단계·시작일. 등록 후에는 잠긴다 |
| 홈 | `home_screen.dart` | 오늘의 끼니 · 양·질·포만도 · 식사 입력 진입 |
| 식사 입력 | `meal_input_screen.dart`, `food_search_screen.dart`, `meal_add_sheet.dart` | 끼니·시각·사진·음식 검색 |
| 분석 | `meal_analysis_screen.dart` | 사진 분석 진행 3단계 |
| 음식 확인 | `meal_review_screen.dart`, `meal_item_edit_sheet.dart` | 인식 결과 교정 |
| 식사 평가 | `meal_evaluation_screen.dart` | 식후 포만감 → Q/Q/S |
| 끼니 피드백 | `next_meal_suggestion_screen.dart` | 점수 · 영양소 · AI 피드백 · 다음 음식 제안 |
| 피드백 탭 | `feedback_branch_popup.dart` → `meal_history_screen.dart` / `long_term_feedback_screen.dart` | 분기 팝업에서 단기(기록 달력)·장기 피드백 선택 |
| AI | `chatbot_screen.dart` | AI 코치(챗봇) |
| 마이 | `my_screen.dart` | 프로필 조회·수정 |

**팝업**은 전부 `PopupGate`(`lib/popups/popup_gate.dart`) 한 곳으로 띄운다 — 우선순위로 한 번에 하나만 뜬다.
(하루 한 번 컨디션 기록 · 포만감 체크인 · 삭제/식사 안 함/나가기 확인 · 영양정보 안내 · 제안 교체 확인)

> 아직 없는 것: 카카오 로그인, FCM 푸시, secure storage 토큰. 사용자 식별은 `X-User-Id` 헤더이고,
> 접근 제한은 `X-Demo-Key` 다(JWT 도입 전 임시 장치).

---

## 상태 처리

앱은 서버의 `meals.status` 를 그대로 따른다.

| status | 앱 동작 |
|---|---|
| `ANALYZING` | 진행 화면 + 폴링 |
| `REVIEW_REQUIRED` | 음식 확인 화면 (`clarifyQuestion` 이 있으면 함께 표시) |
| `EVALUATED` | Q/Q/S 표시. `feedbackStatus` 는 별도로 폴링 |
| `FAILED` | 재시도 안내 |

로딩·빈 상태·오류 표현은 [`docs/design-system.md`](docs/design-system.md) 의 상태 규칙을 따른다.

---

## 실행

```bash
flutter pub get
flutter run                       # APP_PHASE=local — mock 서버(frontend/mock)에 붙는다
flutter build apk --release       # 배포용은 아래 "환경변수" 의 phase · 키를 함께 준다
```

개발 환경 세팅 · 실기기 테스트 · 테스트 방법은 [`README_FLUTTER.md`](README_FLUTTER.md) 를 본다.

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

`--dart-define` 으로 주입한다. 키를 모아 두는 `.env`(gitignore됨, 예시는 `.env.example`)는 앱이 읽지 않는 로컬 메모다.
**키를 소스에 하드코딩하지 않는다.**

| 키 | 설명 |
|---|---|
| `APP_PHASE` | 붙을 환경: `local`(기본) · `dev` · `staging` · `prod`. 아래 표 참고. 잘못 적으면 앱 시작 때 바로 터진다 |
| `API_BASE_URL` | BE 공개 API 주소 (`/api/v1` 포함). 주면 phase 주소 대신 쓴다. **`local` · `dev` 에서만** 쓸 수 있다(에뮬레이터 `10.0.2.2`·`adb reverse` 용). `staging` · `prod` 에서 주면 시작 때 터진다 |
| `DEMO_KEY` | 데모 서버 접근 키(`X-Demo-Key`). `staging` · `prod` **앱** 빌드에만 넣고, 그 서버의 키를 넣는다. **웹 빌드에는 넣지 않는다**(위 "웹" 참고) |

> `.env` 는 앱이 읽지 않는다. 빌드할 때 `--dart-define` 으로 **직접 넘긴 값만** 앱에 들어간다 —
> `DEMO_KEY` 를 빼먹으면 서버가 403 을 준다. 카카오 로그인 · FCM 은 아직 쓰지 않는다.

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
