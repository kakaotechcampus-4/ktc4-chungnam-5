import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

/// 홈 — 투약 상태 · 위 게이지 · 오늘의 식사.
///
/// Figma `hOxrHBitBpjwIBBg2GO49y` node `60:189`.
///
/// **지금은 레이아웃 뼈대다.** 보이는 값은 전부 아래 `_dummy*` 이고,
/// 실제로는 `GET /home` 한 번으로 전부 채워진다.
///
/// 상태는 `GET /home` 이 단일 호출이라 화면 전체가 하나로 움직인다.
/// 연동할 때 [_state] 를 응답에 따라 바꾸고, `error.code` 로 다음을 분기한다.
/// - `PROFILE_REQUIRED` → 프로필 입력 화면
/// - `STAGE_NOT_SET`    → 투약 정보 입력 화면 (`MedicationInfoScreen`)
///
/// 두 경우는 실패가 아니라 이동이므로 [LoadState.failed] 로 두지 않는다.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  /// 연동 전까지는 정상 화면을 보여 준다.
  /// 나머지 분기도 아래에 다 구현돼 있으니 값만 바꿔 확인할 수 있다.
  LoadState _state = LoadState.ready;

  // ── 더미 데이터 (GET /home 응답 자리) ──────────────────────────
  static const String _dummyDate = '8월 21일 금요일';
  static const String _dummyStage = '유지기';
  static const String _dummyMedication = '위고비 1.0mg';
  static const String _dummyDoseInfo = '12회차 · 투약 10주차';
  static const String _dummyDDay = 'D-3';
  static const String _dummyNextDoseDate = '8/24 (일)';
  static const String _dummyDoseChange = '증량 예정 없음';
  static const int _dummySatiety = 68;
  static const String _dummyLastMeal = '마지막 식사 후 2시간 20분';
  static const String _dummyTip = '“단백질을 조금 더 먹어야 해요!!”';

  /// `safetyStatus: BLOCKED` 여부. true 면 팁 문장 자리에 상담 안내가 들어간다.
  static const bool _dummyBlocked = false;

  static const List<_HomeMeal> _dummyMeals = [
    _HomeMeal('아침', '08:20', '토스트, 그릭요거트', '적정', 90, 80),
    _HomeMeal('점심', '12:40', '현미밥, 된장국, 두부조림', '적정', 80, 68),
  ];

  void _reload() {
    // TODO: GET /home 재요청.
    setState(() => _state = LoadState.ready);
  }

  void _openMealInput() {
    // TODO: 식사 입력 화면(3번)이 머지되면 연결한다.
  }

  void _openMedicationInfo() {
    // TODO: 투약 정보 화면(1번)이 머지되면 연결한다.
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.screenHorizontal,
          AppSpacing.titleTop,
          AppSpacing.screenHorizontal,
          AppLayout.scrollBottomPadding,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 날짜는 로컬 값이라 어떤 상태에서도 바로 보여 준다.
            Text(_dummyDate, style: AppTypography.bodySecondary),
            const SizedBox(height: AppSpacing.md),
            ...switch (_state) {
              LoadState.loading => _buildLoading(),
              LoadState.failed => _buildFailed(),
              LoadState.ready => _buildReady(),
            },
          ],
        ),
      ),
    );
  }

  List<Widget> _buildLoading() {
    return [
      const SkeletonBox(width: double.infinity, height: 75),
      const SizedBox(height: AppSpacing.xl),
      // 게이지는 실루엣을 그대로 그리고 채움만 0 으로 둔다.
      // 자리를 비우면 완료 시점에 아래 내용이 통째로 밀린다.
      const Center(child: StomachGauge(satiety: 0, showValue: false)),
      const SizedBox(height: AppSpacing.xl),
      const _SectionHeader(trailing: null),
      const SizedBox(height: AppSpacing.md),
      const MealCardSkeleton(),
      const SizedBox(height: AppSpacing.cardGap),
      const MealCardSkeleton(),
    ];
  }

  List<Widget> _buildFailed() {
    return [
      const SizedBox(height: AppSpacing.xxl),
      RetryBlock(onRetry: _reload),
    ];
  }

  List<Widget> _buildReady() {
    final hasMeals = _dummyMeals.isNotEmpty;

    return [
      _MedicationStatusCard(
        stage: _dummyStage,
        medication: _dummyMedication,
        doseInfo: _dummyDoseInfo,
        dDay: _dummyDDay,
        nextDoseDate: _dummyNextDoseDate,
        doseChange: _dummyDoseChange,
        onTap: _openMedicationInfo,
      ),
      const SizedBox(height: AppSpacing.xl),

      // 오늘 기록이 없으면 게이지는 0% 로 둔다.
      Center(child: StomachGauge(satiety: hasMeals ? _dummySatiety : 0)),
      const SizedBox(height: AppSpacing.lg),

      if (!hasMeals)
        const EmptyBlock(message: '아직 기록된 식사가 없어요')
      else if (_dummyBlocked)
        // 안전 차단이어도 게이지·식사 목록은 그대로 둔다.
        // 판단을 덧붙이지 않고 상담 안내만 한다(§7).
        const NoticeBlock()
      else
        Center(
          child: Column(
            children: [
              Text(_dummyLastMeal, style: AppTypography.bodySecondary),
              // 문구 생성이 실패하면 이 줄만 빠지고 나머지는 정상이다(§7 규칙 3).
              Text(_dummyTip, style: AppTypography.bodySecondary),
            ],
          ),
        ),
      const SizedBox(height: AppSpacing.xl),

      _SectionHeader(trailing: '${_dummyMeals.length}끼 기록됨'),
      const SizedBox(height: AppSpacing.md),

      for (final meal in _dummyMeals) ...[
        MealCard(
          mealTypeLabel: meal.mealTypeLabel,
          time: meal.time,
          foodNames: meal.foodNames,
          quantityLabel: meal.quantityLabel,
          quality: meal.quality,
          satiety: meal.satiety,
        ),
        const SizedBox(height: AppSpacing.cardGap),
      ],

      // 부제(`아래 카메라 버튼으로...`)는 중앙 카메라 버튼이 미확정이라 넣지 않는다.
      AddMealCard(label: '＋ 저녁 식사 기록하기', onTap: _openMealInput),
    ];
  }
}

/// 오늘의 식사 목록 한 끼. 연동 시 모델로 대체된다.
class _HomeMeal {
  const _HomeMeal(
    this.mealTypeLabel,
    this.time,
    this.foodNames,
    this.quantityLabel,
    this.quality,
    this.satiety,
  );

  final String mealTypeLabel;
  final String time;
  final String foodNames;
  final String quantityLabel;
  final int quality;
  final int satiety;
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.trailing});

  /// `2끼 기록됨`. 로딩 중에는 개수를 모르므로 비운다.
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text('오늘의 식사', style: AppTypography.sectionHead),
        const Spacer(),
        if (trailing != null) Text(trailing!, style: AppTypography.caption),
      ],
    );
  }
}

/// 투약 상태 카드 — 좌측 현재 투약, 우측 다음 투약.
class _MedicationStatusCard extends StatelessWidget {
  const _MedicationStatusCard({
    required this.stage,
    required this.medication,
    required this.doseInfo,
    required this.dDay,
    required this.nextDoseDate,
    required this.doseChange,
    this.onTap,
  });

  final String stage;
  final String medication;
  final String doseInfo;
  final String dDay;
  final String nextDoseDate;
  final String doseChange;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadius.lgRadius,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Row(
                        children: [
                          // Figma 는 주사기 일러스트다. 에셋이 들어오면 교체한다.
                          const Icon(
                            Icons.vaccines_outlined,
                            size: 20,
                            color: AppColors.primary,
                          ),
                          const SizedBox(width: AppSpacing.sm),
                          StageBadge(label: stage),
                        ],
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      Text(medication, style: AppTypography.sectionHead),
                      const SizedBox(height: 2),
                      Text(doseInfo, style: AppTypography.caption),
                    ],
                  ),
                ),
                const VerticalDivider(width: AppSpacing.lg),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('다음 투약', style: AppTypography.caption),
                    const SizedBox(height: AppSpacing.xs),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: [
                        Text(
                          dDay,
                          style: AppTypography.sectionHead.copyWith(
                            color: AppColors.primary,
                          ),
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Text(nextDoseDate, style: AppTypography.caption),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(doseChange, style: AppTypography.caption),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 위(胃) 게이지. 채움 높이 = 포만감 %.
///
/// [_StomachPainter] 의 패스는 Figma 실루엣을 손으로 근사한 것이다.
/// 원본 벡터를 SVG 로 내보내 교체하는 게 최종 형태다(`flutter_svg` + `ClipPath`).
/// 지금은 의존성·에셋을 늘리지 않으려고 `CustomPaint` 로만 그린다.
class StomachGauge extends StatelessWidget {
  const StomachGauge({
    super.key,
    required this.satiety,
    this.showValue = true,
  });

  /// 0~100.
  final int satiety;

  /// 로딩 중에는 수치를 감추고 실루엣만 남긴다.
  final bool showValue;

  static const double _width = 208;
  static const double _height = 235;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: _width,
      height: _height,
      child: Stack(
        // Figma 는 수치를 정중앙이 아니라 살짝 아래(채운 영역 한가운데)에 둔다.
        // 위 실루엣이 좌측으로 치우쳐 있어 가로도 조금 왼쪽으로 민다.
        alignment: const Alignment(-0.08, 0.22),
        children: [
          Positioned.fill(
            child: CustomPaint(
              painter: _StomachPainter(fillRatio: satiety / 100),
            ),
          ),
          if (showValue)
            Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '$satiety%',
                  style: AppTypography.gaugeValue.copyWith(
                    // 채움이 낮으면 수치가 회색 면 위에 얹혀 대비가 죽는다.
                    color: satiety >= 35
                        ? AppColors.textInverse
                        : AppColors.textPrimary,
                  ),
                ),
                Text(
                  '지금 포만감',
                  style: AppTypography.body.copyWith(
                    color: satiety >= 35
                        ? AppColors.textInverse
                        : AppColors.textSecondary,
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _StomachPainter extends CustomPainter {
  const _StomachPainter({required this.fillRatio});

  /// 0.0 ~ 1.0.
  final double fillRatio;

  /// 패스를 그린 기준 좌표계. 실제 크기에 맞춰 스케일한다.
  static const Size _reference = Size(208, 235);

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(
      size.width / _reference.width,
      size.height / _reference.height,
    );

    canvas.clipPath(_buildPath());

    // 빈 부분. Figma 에는 어두운 윤곽선이 없고 밝은 회색 면으로만 보인다
    // (디자인 가이드 §5 의 1.5px 윤곽선과 다르며, Figma 를 기준으로 삼았다).
    canvas.drawRect(
      Offset.zero & _reference,
      Paint()..color = AppColors.surfaceMuted,
    );

    // 채운 부분. 아래에서 위로 fillRatio 만큼.
    final ratio = fillRatio.clamp(0.0, 1.0);
    if (ratio > 0) {
      final fillRect = Rect.fromLTRB(
        0,
        _reference.height * (1 - ratio),
        _reference.width,
        _reference.height,
      );
      canvas.drawRect(
        fillRect,
        Paint()
          ..shader = const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [AppColors.gaugeFrom, AppColors.gaugeTo],
          ).createShader(fillRect),
      );
    }

    canvas.restore();
  }

  /// 위 몸통 + 상단 분문(식도) + 우하단 유문 쪽 꼬리를 하나로 합친다.
  ///
  /// 세 조각의 감는 방향이 서로 달라 상쇄되는 걸 막으려고
  /// `Path.combine` 으로 명시적으로 합집합을 만든다.
  Path _buildPath() {
    // 몸통. 좌측은 볼록(대만곡), 우측은 오목(소만곡)하게 잡는다.
    final body = Path()
      ..moveTo(96, 36)
      ..cubicTo(60, 42, 38, 70, 34, 104)
      ..cubicTo(30, 134, 10, 154, 8, 182)
      ..cubicTo(5, 216, 40, 240, 82, 240)
      ..cubicTo(126, 240, 160, 216, 166, 184)
      ..cubicTo(171, 160, 162, 142, 150, 126)
      ..cubicTo(137, 108, 130, 92, 130, 68)
      ..cubicTo(130, 46, 116, 34, 96, 36)
      ..close();

    // 분문 — 위 몸통 위로 뻗는 짧은 관.
    final cardia = Path()
      ..moveTo(94, 8)
      ..cubicTo(94, 0, 120, 0, 120, 8)
      ..lineTo(126, 70)
      ..lineTo(96, 70)
      ..close();

    // 유문 — 우하단으로 빠지는 짧은 꼬리.
    final pylorus = Path()
      ..moveTo(156, 166)
      ..cubicTo(176, 172, 190, 188, 199, 208)
      ..cubicTo(205, 219, 192, 227, 186, 216)
      ..cubicTo(177, 198, 166, 186, 150, 180)
      ..close();

    return Path.combine(
      PathOperation.union,
      body,
      Path.combine(PathOperation.union, cardia, pylorus),
    );
  }

  @override
  bool shouldRepaint(_StomachPainter oldDelegate) =>
      oldDelegate.fillRatio != fillRatio;
}
