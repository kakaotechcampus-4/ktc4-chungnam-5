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
| 의존성 | `cupertino_icons`, `flutter_lints` (아직 외부 패키지 없음) |

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

### 웹 (UI 빠르게 확인할 때)

```bash
cd frontend
flutter run -d chrome        # 또는 -d edge
```

핫 리로드는 터미널에서 `r`, 핫 리스타트는 `R`, 종료는 `q`.

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

### 데스크톱

지원하지 않는다. Windows 데스크톱 빌드는 Visual Studio C++ 툴체인이 별도로 필요한데,
제품 타깃이 아니므로 세팅하지 않는다.

---

## 검사 · 테스트

```bash
dart analyze                 # 정적 분석
flutter test                 # 위젯 테스트
```

> ⚠️ **경로에 한글이 있으면 `flutter analyze`가 죽는다.**
> (`바탕 화면`, `카카오테크캠퍼스` 같은 상위 폴더명 때문에 analysis server가
> `FormatException: Unterminated string`으로 종료된다.)
> **`dart analyze`를 쓰면 정상 동작한다** — 검사 내용은 동일하다.
> 근본적으로 피하려면 리포지터리를 `C:\dev\` 같은 영문 경로에 클론한다.

---

## 디렉터리 구조

```
frontend/lib/
├─ main.dart              앱 진입점 · MaterialApp 설정
├─ theme/
│  ├─ app_colors.dart     색상 상수
│  ├─ app_spacing.dart    여백 스케일 (xs~xxl)
│  ├─ app_radius.dart     모서리 반경 (chip/thumbnail/card)
│  └─ app_theme.dart      ThemeData 조립
├─ navigation/
│  └─ root_shell.dart     하단 탭 4개(홈·피드백·기록·마이) 셸
└─ screens/               탭별 화면
```

색상·여백·모서리 값은 **하드코딩하지 말고** `theme/`의 상수를 쓴다.
디자인 토큰이 바뀔 때 한 곳만 고치면 되도록 하기 위함이다.

---

## 환경변수

`--dart-define` 또는 `.env`(gitignore됨)로 주입한다. **키를 소스에 하드코딩하지 않는다.**
자세한 키 목록은 [`README.md`](./README.md#환경변수) 참고.

```bash
flutter run --dart-define=API_BASE_URL=https://...
```
