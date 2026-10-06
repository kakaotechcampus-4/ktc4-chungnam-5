# frontend — 개발 환경 · 실행 방법

제품 스펙·화면 정의·절대 규칙은 [`README.md`](./README.md)를 본다.
이 문서는 **개발 환경 세팅과 실행 방법**만 다룬다.

---

## 언어 / 스택

| 항목 | 값 |
|---|---|
| 언어 | **Dart** (SDK `^3.12.2`) |
| 프레임워크 | **Flutter** 3.44.8 stable |
| UI | Material 3 (`useMaterial3: true`) |
| 배포 타깃 | **Android** (APK 직배포) |
| 상태관리 | **Provider** (`lib/state/`) |
| 라우팅 | `Navigator.push` + `MaterialPageRoute` (라이브러리 없음) |
| 폰트 | Pretendard (번들, `assets/fonts/`) |
| 의존성 | `provider`, `dio`(HTTP), `shared_preferences`(기기 저장), `image_picker`(카메라·갤러리), `cupertino_icons`, `flutter_lints` |

Dart는 Flutter 전용 언어라 따로 설치할 필요가 없다. `flutter` SDK를 깔면 Dart가 같이 들어온다.

### 왜 Android 네이티브인가

**식사 전 포만감 알림(백그라운드 푸시)** 이 핵심 흐름인데 웹에서는 이게 안 된다.
그래서 제품 타깃은 Android 앱이고, **웹 실행은 개발 중 UI를 빠르게 확인하는 용도로만 쓴다**
(푸시·카메라·secure storage 관련 기능은 웹에서 검증되지 않는다).

---

## 준비

```bash
flutter --version     # 3.44.8 이상인지 확인
flutter doctor        # 툴체인 점검
```

`flutter doctor`에서 필요한 것:

- **앱(Android)으로 돌릴 때** — Android Studio + Android SDK + 실기기 또는 에뮬레이터
- **웹으로 돌릴 때** — Chrome 또는 Edge만 있으면 된다 (Android SDK 불필요)

의존성 설치는 프로젝트 최초 1회, 그리고 `pubspec.yaml`이 바뀔 때마다:

```bash
cd frontend
flutter pub get
```

---

## 실행

### 환경(phase) — local / dev / prod

앱은 같은 phase 의 서버하고만 통신한다. 빌드할 때 `--dart-define=APP_PHASE` 로 고르고,
바뀌는 건 **서버 주소뿐**이다(`lib/api/api_client.dart` 의 `ApiConfig`). 화면 코드에 더미 분기를 두지 않는다.

| phase | 옵션 | 서버 | 언제 |
|---|---|---|---|
| **local**(기본) | 없음 | `http://localhost:4010` — [mock 서버](#mock-서버-local) | BE 없이 화면만 볼 때. **정해진 응답만** 온다(저장해도 다음 조회 값이 그대로) |
| **dev** | `--dart-define=APP_PHASE=dev` | `http://127.0.0.1:8000` — Docker 로 띄운 실제 BE | 회원가입 → 투약 → 식사처럼 흐름을 이어서 볼 때. [실기기 테스트](#실기기-테스트-실서버) 참고 |
| **prod** | `--dart-define=APP_PHASE=prod --dart-define=API_BASE_URL=<주소>` | 아직 없음 | 주소 없이 켜면 시작하자마자 오류 |

`API_BASE_URL` 을 주면 phase 주소 대신 쓴다(에뮬레이터는 `http://10.0.2.2:<포트>/api/v1`).

> 화면은 모두 API 를 부른다. local 에서는 사진을 올려도 고정 응답이라 분석 결과·점수가 늘 같다.

### mock 서버 (local)

BE 스키마(`mock/openapi.json`)에 고정 응답(`mock/examples.json`)을 끼워 [Prism](https://github.com/stoplightio/prism) 으로 띄운다.
Node.js 만 있으면 된다(처음 실행 때 Prism 을 받아 온다).

```bash
cd frontend/mock
npm start                    # http://localhost:4010 — 끄려면 Ctrl+C
```

- **로직이 없다.** 요청 body 와 상관없이 `examples.json` 의 응답만 돌려준다. 예시가 없는 API 는
  Prism 이 스키마로 기본값(`"string"`, `0`)을 만들어 준다 — 그 화면을 볼 일이 생기면 `examples.json` 에 추가한다.
- 날짜는 `{{today}}` · `{{today-3}}` · `{{month}}` 로 적으면 서버를 띄운 날 기준으로 바뀐다(`build-spec.mjs`).
- 위젯 테스트도 같은 `examples.json` 을 기본 응답으로 쓴다(`test/support/fake_api.dart`).
- 요청 형식은 검사한다. `X-User-Id` 없이 부르거나 body 가 스키마와 다르면 4xx 가 온다.
- CORS 가 열려 있어 **웹에서도** 붙는다.
- **BE API 가 바뀌면** dev BE 를 띄운 상태에서 `npm run update-spec` 으로 `openapi.json` 을 새로 받고,
  응답 모양이 바뀐 곳은 `examples.json` 도 고친다. **떠 있는 BE 의 스키마를 받으므로**, develop 을 받은 뒤
  BE 이미지를 먼저 다시 빌드한다(`docker compose ... up -d --build`, [2. BE 띄우기](#2-be-띄우기-docker)). 안 그러면 옛 스키마가 온다.

### 웹 (UI 빠르게 확인할 때 — local phase)

```bash
cd frontend/mock && npm start             # 다른 터미널에서 mock 서버를 먼저 띄운다
cd frontend
flutter run -d chrome --web-port=5555     # 또는 -d edge. 포트를 고정하면 주소가 매번 같다
```

브라우저에서 `http://localhost:5555` 가 열린다. 핫 리로드는 터미널에서 `r`, 핫 리스타트는 `R`, 종료는 `q`.

- **웹에서 dev(실서버)에 붙이려면** 브라우저 보안 검사를 끈 테스트용 크롬으로 띄운다. BE 에 CORS 설정이
  없어 그냥 띄우면 브라우저가 요청을 막는다(`NETWORK_ERROR`). Flutter 가 띄우는 크롬에만 적용되고 평소
  쓰는 크롬과는 별개다 — 이 창에서 다른 사이트는 열지 않는다.

  ```bash
  flutter run -d chrome --web-port=5556 --dart-define=APP_PHASE=dev --web-browser-flag=--disable-web-security
  ```

  dev 주소가 `localhost` 가 아니라 `127.0.0.1` 인 이유: 윈도우 크롬은 `localhost` 를 IPv6(`::1`)로 먼저
  찾는데 Docker BE 는 거기서 응답하지 않는다.
- **처음부터(회원가입부터) 다시 보려면** 개발자도구(F12) → Application → Local Storage 를
  지우고 새로고침한다. `flutter run` 이 띄운 Chrome 은 매번 새 프로필이라 새로 띄워도 된다.
  local 은 고정 응답이라 회원가입 뒤 투약 입력으로 이어지지 않고 바로 홈으로 간다(`onboardingStatus: READY`).

빌드 결과물만 필요하면:

```bash
flutter build web            # build/web/ 에 생성
```

> ⚠️ 웹 빌드는 서비스 워커가 이전 빌드를 캐싱한다.
> 색상·레이아웃을 바꿨는데 브라우저에 반영이 안 되면 캐시 때문이므로,
> 시크릿 창을 쓰거나 다른 포트로 새로 띄워서 확인한다.

### 앱 (Android — 실제 배포 타깃)

```bash
cd frontend
flutter devices              # 연결된 기기 확인
flutter run                  # 기기가 하나면 자동 선택
flutter run -d <device-id>   # 기기가 여러 개일 때
```

배포용 APK:

```bash
flutter build apk --release  # build/app/outputs/flutter-apk/app-release.apk
```

### 실기기 테스트 (실서버)

USB 로 연결한 Android 폰에서 로컬 BE 에 붙여 보는 순서다. Windows · PowerShell 기준.

#### 1. 폰 준비

1. 설정 → 휴대전화 정보 → 소프트웨어 정보 → **빌드번호 7번 탭** → 개발자 옵션 켜짐
2. 개발자 옵션 → **USB 디버깅** 켜기
3. USB 케이블로 PC 에 연결 → 폰에 뜨는 "USB 디버깅 허용" 수락
4. 인식 확인:

```powershell
flutter devices        # 예: SM S928N (mobile) • R3CX804GY4F • android-arm64
```

#### 2. BE 띄우기 (Docker)

Python 을 따로 깔지 않고 전부 Docker 로 띄운다. **Docker Desktop 을 먼저 실행**해 둔다.

```powershell
cd infra
docker compose -f docker-compose.yml -f docker-compose.ai-stub.yml -f docker-compose.be.yml up -d --build
```

DB·AI 스텁·API(`:8000`)·워커 컨테이너 4개가 뜬다.

**DB 마이그레이션** — BE 이미지에 `alembic/` 이 들어 있지 않아서, `backend/` 를 붙인 임시 컨테이너로 돌린다.
처음 한 번, 그리고 BE 에 마이그레이션이 추가될 때마다:

```powershell
# infra 폴더에서 실행
$backend = (Resolve-Path ..\backend).Path
docker run --rm --network infra_default -v "${backend}:/src" -w /src `
  -e DB_URL=db:5432/glp1_dev -e DB_USER=glp1 -e DB_PASSWORD=glp1_local_dev `
  glp1-be alembic upgrade head
```

**음식 DB(공공 식품영양성분 DB) 넣기** — 처음 한 번. 안 넣으면 음식 확인 화면의 영양 정보 검색이
늘 빈 목록이고, 양·질 점수도 나오지 않는다(`backend/README.md` 참고):

```powershell
# infra 폴더에서 실행 (33만 건, 1~2분). PowerShell 은 `<` 를 못 써서 cmd 로 넘긴다.
cmd /c "docker exec -i glp1-db pg_restore -U glp1 -d glp1_dev --data-only --no-owner < seed\food_refs_20260828.dump"
```

확인:

```powershell
curl http://localhost:8000/health     # {"status":"ok"}
```

> AI 는 아직 스텁이라 무엇을 올려도 "참치김밥 · 삶은 계란"으로 인식하고, 자동으로 영양 정보를 붙이지
> 못한다. 음식 확인 화면에서 "찾아서 고르기"로 고르면 점수가 나온다.
> 내리기: `docker compose -f docker-compose.ai-stub.yml -f docker-compose.be.yml down`
> (`--remove-orphans` 는 DB 컨테이너까지 지우니 쓰지 않는다 — `infra/README.md` 참고)

#### 3. 폰에서 PC 서버로 연결 (`adb reverse`)

폰의 `localhost:8000` 을 PC 의 `localhost:8000` 으로 넘긴다. **케이블을 다시 꽂을 때마다** 다시 건다.

```powershell
$adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
& $adb reverse tcp:8000 tcp:8000
& $adb reverse --list                  # UsbFfs tcp:8000 tcp:8000
```

> 에뮬레이터라면 `adb reverse` 없이 `API_BASE_URL=http://10.0.2.2:8000/api/v1` 로 PC 에 닿는다.
> 폰에서 local(mock) 을 보려면 `adb reverse tcp:4010 tcp:4010` 을 걸고 옵션 없이 실행한다.

#### 4. dev phase 로 실행

```powershell
cd frontend
flutter run -d <device-id> --dart-define=APP_PHASE=dev
```

첫 빌드는 몇 분 걸린다. 서버에 요청이 들어오는지는 `docker logs -f glp1-api` 로 본다.

> ⚠️ **경로에 한글이 있으면 Android 빌드가 실패한다.**
> `Your project path contains non-ASCII characters` — Gradle(Android 플러그인)이 막는다.
> 리포지터리를 `C:\dev\` 같은 **영문 경로에 클론**하는 게 가장 간단하다.
> 당장 옮기기 어려우면 `frontend` 만 영문 경로로 복사해서 거기서 실행한다
> (코드를 고치면 다시 복사해야 한다):
>
> ```powershell
> robocopy . C:\dev\glp1-frontend /MIR /XD build .dart_tool .gradle
> cd C:\dev\glp1-frontend
> flutter run -d <device-id> --dart-define=APP_PHASE=dev
> ```
>
> `gradle.properties` 에 `android.overridePathCheck=true` 를 넣어 검사를 끄는 방법도 있지만,
> 빌드가 다른 곳에서 깨질 수 있고 팀 전체 설정이 바뀌므로 커밋하지 않는다.

#### 5. 자주 쓰는 것

| 하고 싶은 것 | 방법 |
|---|---|
| 회원가입부터 다시(로그아웃) | `& $adb shell pm clear com.ktc4.chungnam5.frontend` → 앱 다시 실행. 기기에 저장된 사용자 ID 만 지워진다(서버 DB 의 계정은 남음) |
| 앱 다시 켜기 | `& $adb shell monkey -p com.ktc4.chungnam5.frontend -c android.intent.category.LAUNCHER 1` |
| "Lost connection to device" | 케이블이 빠졌거나 화면이 꺼져 디버그 연결이 끊긴 것. 앱은 계속 쓸 수 있지만 **`adb reverse` 를 다시 걸어야** 서버에 닿는다. 핫 리로드가 필요하면 `flutter run` 을 다시 |
| DB 를 비우고 처음부터 | `docker compose -f docker-compose.yml down -v` 후 2단계부터 다시(볼륨까지 지운다) |

### 데스크톱

지원하지 않는다. Windows 데스크톱 빌드는 Visual Studio C++ 툴체인이 별도로 필요한데,
제품 타깃이 아니므로 세팅하지 않는다.

---

## 검사 · 테스트

```bash
dart analyze                 # 정적 분석
flutter test                 # 전부. 서버는 필요 없다
```

- 테스트는 서버 대신 `test/support/fake_api.dart` 의 가짜 응답을 받는다. 기본은 mock 서버와 같은
  `mock/examples.json` 이고, 흐름이 필요한 테스트(회원가입 → 투약 등)만 그 안에서 응답을 바꾼다.
- `test/api_services_test.dart` 는 각 화면 API 서비스가 보내는 요청(경로·메서드·body)과 응답 해석을 본다.

> ⚠️ **경로에 한글이 있으면 `flutter analyze`가 죽는다.**
> (`바탕 화면`, `카카오테크캠퍼스` 같은 상위 폴더명 때문에 analysis server가
> `FormatException: Unterminated string`으로 종료된다.)
> **`dart analyze`를 쓰면 정상 동작한다** — 검사 내용은 동일하다.
> 근본적으로 피하려면 리포지터리를 `C:\dev\` 같은 영문 경로에 클론한다.

---

## 디렉터리 구조

```
frontend/lib/
├─ main.dart              앱 진입점 · MaterialApp 설정 · MultiProvider 등록 · 첫 화면 선택
├─ api/                   ApiClient(dio · 응답 래퍼 · X-User-Id) · ApiConfig(phase) · ApiException · 회원·투약 API
├─ common/
│  └─ api_format.dart     API 값 ↔ 화면 표시 변환(단계·끼니 라벨, 날짜 파싱·포맷)
├─ popups/                하루 한 번 팝업(컨디션 기록) · 포만감 체크인 · PopupGate
├─ theme/
│  ├─ app_colors.dart     색상 상수
│  ├─ app_spacing.dart    여백 스케일 (xs~xxl) · AppLayout
│  ├─ app_radius.dart     모서리 반경 (sm/md/lg/xl/pill)
│  ├─ app_typography.dart 타이포그래피 (Pretendard)
│  └─ app_theme.dart      ThemeData 조립
├─ state/
│  ├─ app_state.dart      화면 2개 이상이 공유하는 상태 (ChangeNotifier) 예시
│  ├─ medication_state.dart 현재 투약 — 홈·컨디션 팝업·투약 화면이 같이 본다
│  ├─ profile_state.dart  내 프로필 — 마이 탭·컨디션 팝업·온보딩 확인이 같이 본다
│  ├─ tab_state.dart      현재 탭
│  └─ user_session.dart   현재 사용자(userId) — 기기에 저장
├─ navigation/
│  └─ root_shell.dart     하단 탭 4개(홈·피드백·기록·마이) 셸
└─ screens/               탭별 화면
```

색상·여백·모서리·타이포그래피 값은 **하드코딩하지 말고** `theme/`의 상수를 쓴다.
디자인 토큰의 출처와 의미는 [`docs/design-system.md`](./docs/design-system.md)를 본다
(디자인 규칙 문서다 — API 명세 아님).
디자인 토큰이 바뀔 때 한 곳만 고치면 되도록 하기 위함이다.

---

## 상태관리 — Provider

- **화면 하나에서만 쓰는 상태**는 그 화면의 `StatefulWidget`/`setState`로 충분하다. 전역으로 올리지 않는다.
- **화면 2개 이상이 같이 봐야 하는 상태**만 `ChangeNotifier`로 만들어 `lib/state/`에 두고
  `main.dart`의 `MultiProvider`에 등록한다.
- 기능별로 별도 `ChangeNotifier` 클래스를 만든다 (예: `MealState`, `AuthState`).
  하나의 거대한 상태 클래스로 합치지 않는다.
- 화면에서는 `context.watch<T>()`(빌드 시 구독) / `context.read<T>()`(콜백 안에서 1회 접근)로 꺼내 쓴다.

`lib/state/app_state.dart`가 패턴을 보여주는 예시다. 실제 기능(로그인, 오늘의 식사 등)을 추가할 때
이 파일을 참고해 새 클래스를 만들고, 예시 코드는 지워도 된다.

---

## 화면 간 이동 — Navigator.push

라우팅 라이브러리(`go_router` 등)는 쓰지 않는다. 딥링크·웹 URL 동기화가 필요한 제품이 아니고,
기본 `Navigator`만으로 README의 화면 흐름(①촬영→②분석→③폴링→④확인→⑤확정→⑥피드백)을 다 표현할 수 있다.

```dart
Navigator.push(
  context,
  MaterialPageRoute(builder: (_) => MealResultScreen(mealId: mealId)),
);
```

- 화면 위젯은 필요한 값을 **생성자 파라미터로 받는다.** `Navigator` 의 `arguments`나 전역 상태로
  화면 간 값을 넘기지 않는다.
- 이름 있는 라우트(`onGenerateRoute` 등)는 쓰지 않는다. 화면이 늘어나도 딥링크가 필요해지기 전까지는
  `push`/`pop` 만으로 충분하다.

---

## 환경변수

`--dart-define` 또는 `.env`(gitignore됨)로 주입한다. **키를 소스에 하드코딩하지 않는다.**
자세한 키 목록은 [`README.md`](./README.md#환경변수) 참고.

| 키 | 기본값 | 설명 |
|---|---|---|
| `APP_PHASE` | `local` | `local`(mock 서버) · `dev`(Docker BE) · `prod`. [환경(phase)](#환경phase--local--dev--prod) 참고 |
| `API_BASE_URL` | phase 주소 | 주면 phase 주소 대신 쓴다. 에뮬레이터는 `http://10.0.2.2:<포트>/api/v1` |

```bash
flutter run --dart-define=APP_PHASE=prod --dart-define=API_BASE_URL=https://...
```
