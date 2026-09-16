# GLP-1 식사 코치 — 디자인 가이드 (Flutter)

UI를 새로 만들거나 고치기 전에 이 문서를 먼저 읽는다.

> ### 이 문서의 성격
>
> **이 문서는 디자인 규칙 문서다. API 명세가 아니다.**
>
> - 다루는 것: 색·타이포·여백·형태·컴포넌트 생김새·카피 톤 등 **화면에 보이는 것**.
> - 다루지 않는 것: 엔드포인트, 요청/응답 스키마, 상태 코드, 인증, 데이터 모델.
>   그건 `contracts/` 와 `frontend/README.md` 의 연동 규칙 소관이다.
> - 여기 나오는 `Q·Q·S`, `포만감 %`, `투약 용량` 같은 말은 **표시 대상의 이름**일 뿐,
>   필드명이나 계약이 아니다. 실제 필드명·타입은 API 명세를 따른다.
> - 제품 로직·안전 규칙(총점 비노출, `safetyStatus` 처리 등)의 원본은 `frontend/README.md` 다.
>   이 문서는 그중 **화면 설계에 영향을 주는 부분만** §7 에 옮겨 적는다. 충돌하면 README 가 기준.

- **원본**: Figma `hOxrHBitBpjwIBBg2GO49y` → 프레임 `프로토타입` (node `60:6`)
- **구현**: Flutter. 모든 토큰은 `lib/theme/` 에 Dart 상수로 존재하며, 아래 표의 `AppXxx.yyy` 가 실제 심볼이다.
- 이 문서와 Figma가 다르면 **Figma가 기준**이다. 다르다는 걸 발견하면 코드를 고치기 전에 먼저 알린다.
- 이 문서와 `lib/theme/` 이 다르면 **이 문서가 기준**이다. 둘은 항상 같이 수정한다.
- 토큰에 없는 값(생 hex, 임의 폰트 크기, 임의 radius, 임의 그림자)을 쓰지 않는다. 필요하면 토큰을 먼저 추가한다.

### 파일 매핑

| 문서 절 | Dart 파일 | 클래스 |
|---|---|---|
| §1 캔버스와 레이아웃 | `lib/theme/app_spacing.dart` | `AppSpacing`, `AppLayout` |
| §2 컬러 토큰 | `lib/theme/app_colors.dart` | `AppColors` |
| §3 타이포그래피 | `lib/theme/app_typography.dart` | `AppTypography` |
| §4 형태 | `lib/theme/app_radius.dart` | `AppRadius` |
| §5 컴포넌트 규칙 | `lib/theme/app_theme.dart` | `AppTheme.light` |

---

## 1. 캔버스와 레이아웃

| 항목 | 값 | Dart |
|---|---|---|
| 기준 화면 | 375 × 812 (세로 전용) | `AppLayout.designWidth` / `.designHeight` |
| 좌우 거터 | 24px → 콘텐츠 폭 **327px** | `AppSpacing.screenHorizontal` / `AppLayout.contentWidth` |
| 카드 사이 간격 | 12~16px | `AppSpacing.cardGap` (12), 넓히면 `AppSpacing.lg` |
| 카드 내부 패딩 | 16px (좁은 칩·배지는 12px) | `AppSpacing.cardPadding` / `AppSpacing.md` |
| 하단 탭바 영역 | 약 60px + safe area | `AppLayout.bottomBarHeight` |
| 스크롤 콘텐츠 하단 패딩 | 최소 80px (탭바에 가리지 않게) | `AppLayout.scrollBottomPadding` |
| 주 버튼 높이 | 52px | `AppLayout.primaryButtonHeight` |

여백 스케일: `AppSpacing.xs` 4 · `sm` 8 · `md` 12 · `lg` 16 · `xl` 24 · `xxl` 32.

- 모든 주요 블록은 폭 327px 카드로 떨어진다. 카드 밖으로 나가는 요소는 그래프의 축 라벨 정도뿐이다.
- 화면 타이틀은 상태바 아래 24px(`AppSpacing.titleTop`) 지점, 좌측 정렬.

---

## 2. 컬러 토큰

`lib/theme/app_colors.dart` — 위젯에서는 `AppColors.primary` 처럼 참조한다.

**배경 · 표면**

| Dart | HEX | 용도 |
|---|---|---|
| `AppColors.background` | `#FDFCF7` | 화면 배경 (따뜻한 오프화이트) |
| `AppColors.surface` | `#FFFFFF` | 카드 |
| `AppColors.surfaceMuted` | `#F3F5F4` | 트랙, 비활성 세그먼트 |
| `AppColors.surfaceSage` | `#EDF1F0` | 보조 블록, 그래프 바 |
| `AppColors.overlay` | `#0D0A08` @ 48% | 팝업 딤 |

**텍스트**

| Dart | HEX | 용도 |
|---|---|---|
| `AppColors.textPrimary` | `#3A2E22` | 본문 · 제목 |
| `AppColors.textSecondary` | `#8A8073` | 보조 설명 |
| `AppColors.textTertiary` | `#9AA6A2` | 축 라벨, 메타 |
| `AppColors.textInverse` | `#FDFCF7` | 코랄 위 텍스트 |
| `AppColors.textChartLabel` | `#5A6A66` | 그래프 계열 이름 |

**브랜드**

| Dart | HEX | 용도 |
|---|---|---|
| `AppColors.primary` | `#E85C4A` | 주 액션, 활성 탭, 강조 |
| `AppColors.primaryStrong` | `#C0492A` | pressed |
| `AppColors.primaryTint` | `#FBE6E1` | 브랜드 배경 블록 |
| `AppColors.gaugeFrom` | `#F4A99C` | 위 게이지 그라디언트 시작 |
| `AppColors.gaugeTo` | `#E85C4A` | 위 게이지 그라디언트 끝 |

**Q·Q·S 시맨틱**

| Dart | HEX | 의미 |
|---|---|---|
| `AppColors.quantity` / `.quantityBg` | `#9C7A2E` / `#F3E9D6` | 양 |
| `AppColors.quality` / `.qualityBg` | `#5A7A47` / `#DEEAD8` | 질 |
| `AppColors.satiety` / `.satietyBg` | `#C0663B` / `#F7DCCB` | 포만감 |

**선 · 비활성**

| Dart | HEX | 용도 |
|---|---|---|
| `AppColors.border` | `#E1E6E4` | 기본 구분선, 카드 테두리, 탭바 상단선 |
| `AppColors.borderStrong` | `#CBD8D4` | 아웃라인 버튼, 선택 가능한 카드 |
| `AppColors.inactive` | `#A9B4B0` | 비활성 탭 아이콘 · 라벨 |

**규칙**

- 화면 배경은 순백이 아니라 `AppColors.background`. 흰색은 카드에만 쓴다. (`AppTheme.light` 의 `scaffoldBackgroundColor` 로 이미 적용돼 있다.)
- 파랑·보라 계열은 쓰지 않는다. 팔레트는 웜 뉴트럴 + 세이지 + 코랄뿐이다.
- 강조색은 화면당 한 군데만. 코랄이 두 곳 이상에서 경쟁하면 하나는 `AppColors.textPrimary` 로 내린다.
- Q·Q·S 세 색은 **의미가 고정**되어 있다. 다른 용도로 재사용하지 않는다.
- 구버전 프레임(`원본 기준`, `색감수정`)의 플럼 계열(`#3A2E52`, `#F2603C`)은 폐기됐다. 쓰지 않는다.
- 초기 구현에 있던 `divider #EFEDEA` 는 팔레트에 없는 값이라 삭제됐다. 구분선은 `AppColors.border` 하나로 통일한다.

---

## 3. 타이포그래피

Figma 원본 폰트는 Inter지만 한글 UI이므로 구현은 **Pretendard** 를 쓴다.
굵기는 Regular(400) / Bold(700) 두 단계만 쓴다.

- 반입 방식: **번들.** `assets/fonts/Pretendard-Regular.ttf` · `-Bold.ttf` 를 저장소에 두고
  `pubspec.yaml` 의 `fonts:` 항목으로 등록한다. 네트워크 없이 첫 실행부터 바로 렌더된다.
- 출처: [orioncactus/pretendard](https://github.com/orioncactus/pretendard) 공식 릴리스 `v1.3.9`,
  `public/static/alternative/` 의 TTF. 라이선스는 SIL OFL 1.1 (`assets/fonts/OFL.txt` 동봉, 상업적 사용 가능).
- 폰트 파일이 손상·누락된 극단적 상황을 대비한 fallback: `-apple-system` → `system-ui` →
  `Apple SD Gothic Neo` → `Malgun Gothic`.
- 폰트 지정은 `AppTheme.light` 의 `fontFamily`/`fontFamilyFallback` 과 `AppTypography._base` 두 곳이
  같은 값을 가리킨다. 굵기를 늘릴 일이 생기면 파일 추가 + `pubspec.yaml` weight 등록이 먼저다.

> Noto Sans KR + `google_fonts` 런타임 다운로드 방식을 검토했으나(2026-09-10), 웹/Android 동작 차이와
> 첫 실행 네트워크 의존, 이 디자인의 10~13px 본문에서 폰트 교체 시 줄바꿈이 흔들리는 문제로 폐기하고
> 번들 방식으로 확정했다(2026-09-10).

| 역할 | 크기 / 굵기 | 색 | Dart | `TextTheme` |
|---|---|---|---|---|
| 게이지 수치 (홈 포만감 %) | 46 Bold | `primary` | `AppTypography.gaugeValue` | `displayLarge` |
| 화면 타이틀 | 20 Bold | `textPrimary` | `AppTypography.screenTitle` | `titleLarge` |
| 섹션 헤드 | 16 Bold | `textPrimary` | `AppTypography.sectionHead` | `titleMedium` |
| 주 버튼 라벨 | 14 Bold | `textInverse` | `AppTypography.buttonLabel` | `labelLarge` |
| 카드 제목 | 13 Bold | `textPrimary` | `AppTypography.cardTitle` | `titleSmall` |
| 강조 수치 / 배지 | 12 Bold | 문맥색 | `AppTypography.emphasis` | `labelMedium` |
| 본문 | 11 Regular | `textPrimary` | `AppTypography.body` | `bodyLarge` · `bodyMedium` |
| 보조 설명 | 11 Regular | `textSecondary` | `AppTypography.bodySecondary` | `bodySmall` |
| 캡션 · 탭 라벨 · 축 라벨 | 10 Regular | `textTertiary` | `AppTypography.caption` | `labelSmall` |

- Figma에 9.5 / 12.5 / 13.5 같은 반값이 섞여 있는데 **SVG 임포트 과정의 스케일 잔재**다. 위 램프로 스냅해서 구현한다.
- line-height는 본문 1.5, 제목·수치 1.25. (`AppTypography` 에 반영돼 있다.)
- 숫자 강조는 크기가 아니라 굵기와 색으로 만든다.

---

## 4. 형태

| Dart | 값 | 용도 |
|---|---|---|
| `AppRadius.sm` | 8px | 칩, 작은 태그 |
| `AppRadius.md` | 12px | 입력 필드, 버튼 |
| `AppRadius.lg` | 14px | 카드, 선택 항목 |
| `AppRadius.xl` | 16px | 큰 컨테이너, 바텀시트 상단 |
| `AppRadius.pill` | 999px | 세그먼티드 컨트롤, 필터 |

`BorderRadius` 가 필요하면 `AppRadius.smRadius` … `pillRadius` 를 쓴다.
바텀시트 상단만 둥근 형태는 `AppRadius.sheetRadius`.

> 초기 구현의 실측값 9 / 11 / 13 은 §3 에서 말한 SVG 스케일 잔재와 같은 원인이라
> 위 램프(8 / 12 / 14)로 스냅했다. 기존 이름 `chipBorderRadius` · `thumbnailBorderRadius` ·
> `cardBorderRadius` 는 각각 `sm` · `md` · `lg` 별칭으로 남아 있다.

- 테두리는 **1px**. `AppColors.border` 가 기본, 누를 수 있는 요소는 `AppColors.borderStrong`.
- 그림자는 기본적으로 쓰지 않는다. 카드는 테두리와 배경 대비로 띄운다. 예외는 팝업/바텀시트뿐이고, 이때도 딤 오버레이가 주 depth 장치다.

---

## 5. 컴포넌트 규칙

`AppTheme.light` 에 이미 반영된 것과, 위젯을 만들 때 직접 지켜야 하는 것을 구분해 적는다.

### 테마에 이미 들어 있는 것 (`lib/theme/app_theme.dart`)

`Card` · `FilledButton` · `OutlinedButton` · `BottomSheet` · `Dialog` · `AppBar` · `BottomNavigationBar` ·
`Divider` 는 기본 위젯을 그냥 쓰면 아래 규칙대로 나온다. elevation 은 전부 0 으로 눌러 뒀다.

### 컴포넌트별 규칙

**카드** — `AppColors.surface`, `AppRadius.lg`, 1px `AppColors.border`, 패딩 `AppSpacing.cardPadding`(16).
제목 `AppTypography.cardTitle` 좌측 + 메타 `AppTypography.caption` 우측 정렬이 기본 헤더 형태.
→ `Card` 위젯이 색·모서리·테두리를 이미 갖고 있다. 패딩만 넣으면 된다.

**주 버튼** — `FilledButton`. 폭은 부모 가득(327), 높이 `AppLayout.primaryButtonHeight`(52),
`AppRadius.md`, 배경 `AppColors.primary`, 텍스트 `AppTypography.buttonLabel`. 화면당 하나.

**보조 버튼** — `OutlinedButton`. 같은 크기, 배경 투명, 1px `AppColors.borderStrong`, 텍스트 `AppColors.textPrimary`.

**선택 카드 (약물 · 용량 · 강도)** — 미선택은 `AppColors.borderStrong` 1px + 투명 배경,
선택은 `AppColors.primary` 1px + `AppColors.primaryTint` 배경 + 텍스트 `AppColors.primary`. 체크 아이콘은 우상단.
→ 테마 기본 `Card` 와 테두리색이 달라 직접 그린다.

**세그먼티드 컨트롤 (7일/28일/전체)** — 컨테이너 `AppColors.surfaceMuted` + `AppRadius.pill`,
활성 탭만 `AppColors.surface` 알약 + `AppTypography.emphasis` `textPrimary`,
비활성은 12 Regular `AppColors.textTertiary`.

**Q·Q·S 배지** — 배경은 각 항목의 `Bg` 색, 텍스트는 본색, `AppTypography.emphasis`(또는 10 Bold), `AppRadius.sm`.
세 개를 가로 3분할로 배치하고 각 항목 아래 가는 진행 바(높이 5, `AppRadius.pill`)를 둔다.

**위(胃) 게이지** — 윤곽선은 `AppColors.textPrimary` 1.5px,
채움은 `AppColors.gaugeFrom → gaugeTo` 세로 `LinearGradient`. 채움 높이 = 포만감 %.
중앙에 `AppTypography.gaugeValue` 수치. `CustomPaint` 로 그린다.

**탭바** — 4탭(홈·피드백·기록·마이). 상단 1px `AppColors.border`
(`RootShell` 이 `DecoratedBox` 로 그린다 — 그림자가 아니다).
활성은 아이콘·라벨 모두 `AppColors.primary`, 비활성은 `AppColors.inactive`.
아이콘 `AppLayout.tabIconSize`(18), 라벨 `AppTypography.caption`(10).

**팝업 / 바텀시트** — 뒤 화면에 `AppColors.overlay` 딤, 시트는 `AppColors.surface` + `AppRadius.sheetRadius`.
액션은 하단 가로 2분할(보조 좌 / 주 우).
→ `showModalBottomSheet` 가 테마에서 딤·모서리를 가져간다.

**그래프** — 격자선 1px `AppColors.border`, 막대는 `AppColors.surfaceSage`,
꺾은선은 `AppColors.primary`(포만감) / `AppColors.quantity` 점선(투약 용량).
계열 이름은 `AppColors.textChartLabel`, 축 라벨은 `AppTypography.caption`.

---

## 6. 화면 맵 (Figma node id)

| # | 화면 | node | 구현 파일 | 상태 |
|---|---|---|---|---|
| 1 | 투약 정보 입력 | `60:11` | — | ⬜ |
| 2 | 홈 (위 게이지 · 오늘의 식사) | `60:189` | `lib/screens/home_screen.dart` | 🚧 뼈대 |
| 2-a | 컨디션 기록 팝업 | `74:8` | — | ⬜ |
| 3 | 식사 입력 | `86:6` | — | ⬜ |
| 4 | AI 분석 진행 | `89:6` | — | ⬜ |
| 5 | 음식 확인·수정 | `92:6` | — | ⬜ |
| 6 | 식사 평가 (Q·Q·S) | `60:270` | — | ⬜ |
| 7 | 단기 피드백 | `60:331` | — | ⬜ |
| 8 | 장기 피드백 | `60:103` | `lib/screens/long_term_feedback_screen.dart` | 🚧 뼈대 |
| 9 | 기록 (달력) | `94:6` | `lib/screens/meal_history_screen.dart` | 🚧 뼈대 |
| 10 | 사후 포만감 체크인 | `100:6` | — | ⬜ |
| 11 | 영양정보 직접 입력 | `102:6` | — | ⬜ |

탭바 4탭은 `lib/navigation/root_shell.dart` 가 묶는다. 현재 **마이** 탭에 대응하는 Figma 노드가 없다
(`lib/screens/my_screen.dart` 는 자리만 잡아 둔 상태). 설정/프로필 화면이 확정되면 이 표에 추가한다.

특정 화면을 구현할 때는 위 node id로 `get_design_context` 를 호출해 실제 배치를 확인한다. 스크린샷만 보고 추측해서 그리지 않는다.
Figma MCP 가 연결돼 있지 않으면 이 호출은 불가능하다. 그 경우 임의로 그리지 말고 연결을 먼저 요청한다.

---

## 7. 카피 · 상태 규칙 (화면에 드러나는 부분만)

> 아래는 제품 안전 규칙 중 **화면 설계에 직접 영향을 주는 것**을 옮겨 적은 것이다.
> 원본과 전체 목록은 `frontend/README.md` 에 있고, 충돌하면 그쪽이 기준이다.

- 총점·체중 같은 **단일 종합 수치를 노출하지 않는다.** 축별 Q·Q·S(1–5)와 이번 단계에서 가장 엄격한 축만 보여준다.
- 안전 차단 상태에서는 피드백 화면 대신 **상담 안내 화면**을 띄운다.
- Q·Q·S 점수는 확정 즉시 표시되고, 피드백 문구는 나중에 도착한다.
  **문구가 실패해도 점수 화면이 깨지면 안 된다.** 로딩·실패 상태를 반드시 함께 디자인한다.
- 의료·처방 판단은 하지 않는다. 해당 질문에는 판단 없이 담당 의사 상담을 안내한다.
- AI가 추정한 음식·양은 **항상 사용자가 확인·수정할 수 있는 상태**로 노출한다. 되돌릴 수 없는 확정 단계를 만들지 않는다.
- 신뢰도가 낮으면 임의로 확정하지 말고, 영향이 큰 정보 하나만 되묻는다.
- 인식 실패 시 재촬영 / 텍스트 직접 입력 폴백을 반드시 제공한다.
- 문장은 평서형 존댓말("~해요"). 사용자를 탓하는 표현을 쓰지 않는다.

---

## 8. 하지 말 것

- 화면 배경에 `#FFFFFF` 사용
- 파랑·보라 계열, 팔레트 밖 색 추가
- 그림자로 카드 구분 (테두리로 한다)
- 이모지를 아이콘 대용으로 사용
- 폰트 크기를 1px 단위로 임의 조정
- Q·Q·S 색을 다른 의미로 재사용
- `lib/theme/` 토큰을 문서 수정 없이 바꾸기 (반대도 마찬가지)
- 이 문서에 엔드포인트·응답 스키마·필드명 적기 (여기는 디자인 문서다)

---

## 9. 미확정

- 하단 탭바 **중앙 카메라 버튼**: 기획에는 있으나 현재 프로토타입 탭바는 4탭 구조로 카메라 버튼이 없다. 확정 전까지 임의로 추가하지 않는다.
- AI 분석 폴링 타임아웃 값 미정 (화면 4).
- **다크 모드 미정.** 현재 `AppTheme` 는 light 만 정의한다. 지원하기로 하면 §2 토큰의 다크 대응값이 먼저 필요하다.
- **마이 탭 화면** 디자인 미확정 (§6 참고).