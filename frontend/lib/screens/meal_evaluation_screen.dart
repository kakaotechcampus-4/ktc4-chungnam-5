import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 식사 평가 (Q·Q·S) — Figma `hOxrHBitBpjwIBBg2GO49y` node `60:270`.
///
/// 양·질은 AI/서버 계산값을 그대로 보여주기만 하고, 포만감만 이 화면에서
/// 사용자가 직접 입력한다(README "포만감은 사용자가 직접 입력한다").
/// `meal_input_screen.dart`에서 이미 받은 "식전(직전)" 포만감과, 여기서
/// 받는 "식후(지금)" 포만감의 델타로 만족도 점수를 낸다.
class MealEvaluationScreen extends StatefulWidget {
  const MealEvaluationScreen({super.key, required this.mealId});

  final String mealId;

  @override
  State<MealEvaluationScreen> createState() => _MealEvaluationScreenState();
}

class _MealEvaluationScreenState extends State<MealEvaluationScreen> {
  // TODO: 실제 값은 확정 응답(POST 확정 API 의 동기 결과)으로 채운다.
  static const double _quantityRatio = 0.55; // 유지기 권장 범위 내 위치(더미)
  static const int _qualityScore = 83; // 100점 만점(더미, home_screen 스케일과 동일)

  double _afterSatiety = 75;

  void _viewNextMealSuggestion() {
    // TODO: feedback.meal 큐 등록 API 호출 후 "다음 끼니 제안" 화면(7번)으로 교체 이동.
    //   (backend/app/worker/jobs/feedback_meal.py 주석: 이 버튼을 눌렀을 때 큐에 넣는다)
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      // NOTE: design-system.md §1은 "화면 타이틀은 좌측 정렬"이라 하지만
      // 이 화면 Figma 는 가운데 정렬로 그려져 있다. 문서보다 Figma 를
      // 따랐다(§0 메타 규칙: 다르면 Figma 가 기준).
      appBar: AppBar(
        centerTitle: true,
        title: Text('식사 평가', style: AppTypography.screenTitle),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.screenHorizontal,
            AppSpacing.lg,
            AppSpacing.screenHorizontal,
            AppLayout.scrollBottomPadding,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 분석한 식사 사진. 실제 URL 로 교체 예정, 지금은 자리만 잡는다.
              ClipRRect(
                borderRadius: AppRadius.lgRadius,
                child: Container(
                  width: double.infinity,
                  height: 240,
                  color: AppColors.surfaceSage,
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              const _QuantityCard(ratio: _quantityRatio),
              const SizedBox(height: AppSpacing.cardGap),
              const _QualityCard(score: _qualityScore),
              const SizedBox(height: AppSpacing.cardGap),
              _SatietyCard(
                value: _afterSatiety,
                onChanged: (v) => setState(() => _afterSatiety = v),
              ),
              const SizedBox(height: AppSpacing.xl),

              SizedBox(
                width: double.infinity,
                height: AppLayout.primaryButtonHeight,
                child: FilledButton(
                  onPressed: _viewNextMealSuggestion,
                  // NOTE: design-system.md §5 "주 버튼"은 배경을
                  // AppColors.primary(코랄)로 정해 뒀지만, 이 화면 Figma 의
                  // CTA 는 진한 다크 브라운이다. 팔레트에 전용 "다크 버튼"
                  // 토큰이 없어 가장 가까운 AppColors.textPrimary 를 임시로
                  // 빌려 썼다 — 확정되면 문서에 별도 규칙을 추가해야 한다.
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.textPrimary,
                    shape: RoundedRectangleBorder(
                      borderRadius: AppRadius.mdRadius,
                    ),
                  ),
                  child: Text('다음 끼니 제안 보기', style: AppTypography.buttonLabel),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Q·Q·S 카드 공통 헤더. 원형 배지(Q/S) + 한글 제목 + 영문 라벨 + 우측 태그.
class _AxisHeader extends StatelessWidget {
  const _AxisHeader({
    required this.letter,
    required this.badgeColor,
    required this.badgeBackground,
    required this.title,
    required this.englishLabel,
    required this.trailing,
  });

  final String letter;
  final Color badgeColor;
  final Color badgeBackground;
  final String title;
  final String englishLabel;
  final Widget trailing;

  static const double _badgeSize = 28;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: _badgeSize,
          height: _badgeSize,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: badgeBackground,
            shape: BoxShape.circle,
          ),
          child: Text(
            letter,
            style: AppTypography.emphasis.copyWith(color: badgeColor),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Text(title, style: AppTypography.cardTitle),
        const SizedBox(width: AppSpacing.xs),
        Text(englishLabel, style: AppTypography.caption),
        const Spacer(),
        trailing,
      ],
    );
  }
}

/// "내가 입력해요" 같은 다크 필 배지.
///
/// NOTE: `_AxisHeader` 의 CTA 버튼과 같은 임시 다크 토큰 이슈 — 전용 색이
/// 없어 AppColors.textPrimary/textInverse 를 빌려 썼다.
class _DarkPill extends StatelessWidget {
  const _DarkPill({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: AppSpacing.xs,
      ),
      decoration: const BoxDecoration(
        color: AppColors.textPrimary,
        borderRadius: AppRadius.pillRadius,
      ),
      child: Text(
        label,
        style: AppTypography.emphasis.copyWith(
          color: AppColors.textInverse,
          fontSize: 10,
        ),
      ),
    );
  }
}

/// 양(Quantity) — AI 계산, 값은 표시하지 않고 권장 범위 내 위치만 보여준다.
class _QuantityCard extends StatelessWidget {
  const _QuantityCard({required this.ratio});

  /// 0.0~1.0. 유지기 권장 범위 내에서의 상대 위치(더미).
  final double ratio;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _AxisHeader(
              letter: 'Q',
              badgeColor: AppColors.quantity,
              badgeBackground: AppColors.quantityBg,
              title: '양',
              englishLabel: 'Quantity',
              trailing: Text('AI 계산', style: AppTypography.caption),
            ),
            const SizedBox(height: AppSpacing.md),
            ClipRRect(
              borderRadius: AppRadius.pillRadius,
              child: LinearProgressIndicator(
                value: ratio,
                minHeight: 8,
                backgroundColor: AppColors.primaryTint,
                color: AppColors.quality,
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            Text('유지기 권장 범위 안이에요', style: AppTypography.bodySecondary),
          ],
        ),
      ),
    );
  }
}

/// 질(Quality) — AI 계산, 100점 만점 숫자를 함께 보여준다.
class _QualityCard extends StatelessWidget {
  const _QualityCard({required this.score});

  /// 0~100.
  final int score;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _AxisHeader(
              letter: 'Q',
              badgeColor: AppColors.quality,
              badgeBackground: AppColors.qualityBg,
              title: '질',
              englishLabel: 'Quality',
              trailing: Text('AI 계산', style: AppTypography.caption),
            ),
            const SizedBox(height: AppSpacing.md),
            Row(
              children: [
                Expanded(
                  child: ClipRRect(
                    borderRadius: AppRadius.pillRadius,
                    child: LinearProgressIndicator(
                      value: score / 100,
                      minHeight: 8,
                      backgroundColor: AppColors.primaryTint,
                      color: AppColors.quality,
                    ),
                  ),
                ),
                const SizedBox(width: AppSpacing.md),
                Text('$score', style: AppTypography.sectionHead),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            Text('단백질·식이섬유 비율이 좋아요', style: AppTypography.bodySecondary),
          ],
        ),
      ),
    );
  }
}

/// 포만감(Satiety) — 이 화면에서 사용자가 직접 입력하는 유일한 축.
class _SatietyCard extends StatelessWidget {
  const _SatietyCard({required this.value, required this.onChanged});

  /// 0~100.
  final double value;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _AxisHeader(
              letter: 'S',
              badgeColor: AppColors.satiety,
              badgeBackground: AppColors.satietyBg,
              title: '포만감',
              englishLabel: 'Satiety',
              trailing: const _DarkPill(label: '내가 입력해요'),
            ),
            const SizedBox(height: AppSpacing.sm),
            Text('식사 후 지금, 얼마나 부르세요?', style: AppTypography.bodySecondary),
            Row(
              children: [
                Expanded(
                  child: Slider(
                    value: value,
                    min: 0,
                    max: 100,
                    onChanged: onChanged,
                    activeColor: AppColors.primary,
                    inactiveColor: AppColors.surfaceMuted,
                  ),
                ),
                const SizedBox(width: AppSpacing.md),
                Container(
                  width: 76,
                  height: 44,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius: AppRadius.mdRadius,
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Text(
                    '${value.toInt()} %',
                    style: AppTypography.sectionHead,
                  ),
                ),
              ],
            ),
            Row(
              children: [
                Expanded(
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('0%', style: AppTypography.caption),
                      Text('100%', style: AppTypography.caption),
                    ],
                  ),
                ),
                const SizedBox(width: AppSpacing.md),
                const SizedBox(width: 76),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
