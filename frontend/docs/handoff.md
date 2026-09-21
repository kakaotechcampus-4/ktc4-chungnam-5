# 인수인계 — 초기 세팅 (`fe/0-feat-setting`)

분업 시작 전 깔아둔 기반. 화면 내용은 전부 빈 뼈대이고, 구조·컨벤션만 잡혀 있다.

## 핵심 파일

| 파일 | 설명 |
|---|---|
| `lib/main.dart` | 진입점. `MultiProvider` + `MaterialApp` 조립. 전역 상태 추가 시 여기에 등록 |
| `lib/navigation/root_shell.dart` | 하단 탭 4개(홈·피드백·기록·마이) 셸. `IndexedStack`으로 탭 전환 |
| `lib/theme/app_colors.dart` | 색상 토큰 27개 |
| `lib/theme/app_spacing.dart` | 여백(`AppSpacing`)·레이아웃 고정값(`AppLayout`) |
| `lib/theme/app_radius.dart` | 모서리 반경 (`sm/md/lg/xl/pill`) |
| `lib/theme/app_typography.dart` | 폰트 스타일 9종 (Pretendard) |
| `lib/theme/app_theme.dart` | 위 토큰들을 모아 `ThemeData` 조립. `AppTheme.light`를 `main.dart`가 씀 |
| `lib/state/app_state.dart` | 전역 상태(`ChangeNotifier`) 예시. 실제 기능은 아직 없음 — 패턴만 참고 |
| `lib/screens/*.dart` | 화면별 파일 |
| `lib/screens/shared_meal_widgets.dart` | **여러 화면이 같이 쓰는 조각.** 식사 카드·Q·Q·S 배지·단계 배지·로딩/실패/빈 상태 블록. **새로 만들지 말고 여기 것을 쓴다** (규칙은 `design-system.md` §10). `lib/widgets/` 로 옮기는 게 맞지만 폴더 구조 확정 전까지 여기 둔다 |
| `assets/fonts/` | Pretendard 폰트 파일 (번들, 네트워크 의존 없음) |
| `docs/design-system.md` | 색·타이포·여백·컴포넌트 규칙. **디자인 문서, API 명세 아님** |
| `README_FLUTTER.md` | 개발 환경·실행 방법·컨벤션(상태관리/라우팅) |
| `README.md` | 제품 스펙·절대 규칙(총점 비노출 등) — 화면 만들기 전 필독 |

## 정해진 컨벤션

- **상태관리: Provider.** 화면 하나짜리는 `setState`, 화면 여러 개가 공유하면 `ChangeNotifier` 만들어 `lib/state/`에 추가 + `main.dart`에 등록.
- **라우팅: `Navigator.push` + `MaterialPageRoute`.** 라이브러리 없음. 화면은 생성자 파라미터로 값을 받는다.
- **색·여백·폰트는 항상 `theme/`의 토큰을 쓴다.** 생 hex·매직 넘버 금지.
- **브랜치명: `fe/<이슈번호>-<작업명>`** (예 `fe/2-home-screen`). 이슈 번호는 화면 번호를 쓴다.
- 배포 타깃은 **Android**. 웹(`flutter run -d chrome`)은 UI 확인용, 데스크톱은 지원 안 함(폴더 자체 삭제).

## 아직 안 된 것 / 다음 사람이 볼 것

- 화면 4개 전부 내용 없음 — Figma 노드 매핑은 `docs/design-system.md` §6 참고.
- **단기 피드백(Figma `60:331`) 화면 파일 없음** — 피드백 탭이 장기만 담당할지 결정 필요.
- 로그인·오늘의 식사 등 실제 전역 상태 없음 — API 명세 나오면 `AuthState`/`MealState` 등으로 추가.
- `.env` 값 비어있음(`.env.example`만 있음) — 카카오 키 등 실제 값은 각자 채워야 함.
- 다크모드 미정, 마이 탭 디자인 미확정.
