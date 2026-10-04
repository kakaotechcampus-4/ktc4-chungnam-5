import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../common/api_format.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'next_meal_suggestion_screen.dart';

/// 식사 평가 (Q·Q·S) — Figma `hOxrHBitBpjwIBBg2GO49y` node `60:270`.
///
/// 확정은 음식 확인 화면(5번)이 식후 포만감과 함께 하고, 이 화면은 그 결과를
/// 바로 보여 준다. 버튼은 "다음 끼니 제안 보기" 하나다.
///
/// 포만감은 여기서 고칠 수 있다. 슬라이더에서 손을 떼면 바뀐 값으로 다시
/// 확정한다 — 서버가 재확정을 허용하고, 점수가 바뀌면 AI 피드백도 새로 만든다
/// (BE `services/evaluation` `_is_stale_feedback`).
///
/// 홈·기록 탭에서 이미 평가한 끼니를 열 때는 [result] 없이 열어 서버 평가를
/// 불러온다.
class MealEvaluationScreen extends StatefulWidget {
  const MealEvaluationScreen({
    super.key,
    required this.mealId,
    this.imageUrl,
    this.result,
  });

  final String mealId;

  /// 식사 사진(/media/...). 없으면 자리만 잡는다.
  final String? imageUrl;

  /// 방금 확정한 결과. 없으면 `GET /meals/{id}/evaluation` 으로 받는다.
  final MealEvaluation? result;

  @override
  State<MealEvaluationScreen> createState() => _MealEvaluationScreenState();
}

class _MealEvaluationScreenState extends State<MealEvaluationScreen> {
  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  late MealEvaluation? _result = widget.result;
  String? _error;

  /// 슬라이더를 움직이는 동안의 값. 손을 떼면 저장한다.
  double? _draftSatiety;
  bool _savingSatiety = false;

  @override
  void initState() {
    super.initState();
    if (_result == null) _loadEvaluation();
  }

  Future<void> _loadEvaluation() async {
    setState(() => _error = null);
    try {
      final result = await _api.fetchEvaluation(widget.mealId);
      if (mounted) setState(() => _result = result);
    } catch (e) {
      if (mounted) setState(() => _error = '평가를 불러오지 못했어요: $e');
    }
  }

  /// 바뀐 식후 포만감으로 다시 확정한다. 실패하면 원래 값으로 돌린다.
  Future<void> _saveSatiety(double value) async {
    final current = _result?.scores.satiety;
    if (current == value.round()) {
      setState(() => _draftSatiety = null);
      return;
    }
    setState(() => _savingSatiety = true);
    try {
      final result = await _api.confirm(
        widget.mealId,
        satietyAfterPct: value.round(),
      );
      if (mounted) setState(() => _result = result);
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('포만감을 바꾸지 못했어요: $e')));
    } finally {
      if (mounted) {
        setState(() {
          _draftSatiety = null;
          _savingSatiety = false;
        });
      }
    }
  }

  /// 다음 끼니 제안 보기 → 다음 끼니 제안 화면(7번)으로 교체 이동.
  /// 피드백 생성은 7번이 GET /meals/{id}/feedback 을 부를 때 서버가 건다.
  void _viewNextMealSuggestion(MealEvaluation result) {
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) =>
            NextMealSuggestionScreen(mealId: widget.mealId, evaluation: result),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    final imageUrl = widget.imageUrl;
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
              // 분석한 식사 사진. 텍스트로 기록했으면 자리만 잡는다.
              ClipRRect(
                borderRadius: AppRadius.lgRadius,
                child: Container(
                  width: double.infinity,
                  height: 240,
                  color: AppColors.surfaceSage,
                  child: imageUrl == null
                      ? null
                      : Image.network(
                          ApiConfig.mediaUrl(imageUrl),
                          fit: BoxFit.cover,
                          errorBuilder: (_, _, _) => const SizedBox(),
                        ),
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              _QuantityCard(result: result),
              const SizedBox(height: AppSpacing.cardGap),
              _QualityCard(result: result),
              const SizedBox(height: AppSpacing.cardGap),
              // 5번에서 입력한 식후 포만감. 고치면 다시 확정한다.
              _SatietyCard(
                value:
                    _draftSatiety ?? (result?.scores.satiety ?? 0).toDouble(),
                onChanged: result == null || _savingSatiety
                    ? null
                    : (v) => setState(() => _draftSatiety = v),
                onChangeEnd: _saveSatiety,
              ),
              if (result?.notice != null) ...[
                const SizedBox(height: AppSpacing.md),
                Text(result!.notice!, style: AppTypography.bodySecondary),
              ],
              if (_error != null) ...[
                const SizedBox(height: AppSpacing.md),
                Text(
                  _error!,
                  style: AppTypography.bodySecondary.copyWith(
                    color: AppColors.primary,
                  ),
                ),
              ],
              const SizedBox(height: AppSpacing.xl),

              SizedBox(
                width: double.infinity,
                height: AppLayout.primaryButtonHeight,
                child: FilledButton(
                  onPressed: result == null
                      ? (_error != null ? _loadEvaluation : null)
                      : _savingSatiety
                      ? null
                      : () => _viewNextMealSuggestion(result),
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
                  child: Text(
                    _error != null ? '다시 불러오기' : '다음 끼니 제안 보기',
                    style: AppTypography.buttonLabel,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 평가를 불러오는 동안 양·질 카드 안내.
const _beforeConfirm = '평가를 불러오고 있어요';

/// 영양 정보가 있는 음식이 없어 계산하지 못했을 때.
const _noNutrition = '영양 정보가 있는 음식이 없어 계산하지 못했어요';

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

/// 양(Quantity) — 서버 계산. 0~100 점수를 막대로 보여 준다.
class _QuantityCard extends StatelessWidget {
  const _QuantityCard({required this.result});

  final MealEvaluation? result;

  @override
  Widget build(BuildContext context) {
    final r = result;
    final score = r?.scores.quantity;
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
            _ScoreBar(score: score),
            const SizedBox(height: AppSpacing.sm),
            Text(
              r == null
                  ? _beforeConfirm
                  : score == null
                  ? _noNutrition
                  : '${stageLabel(r.stage)} 기준 한 끼 양이에요',
              style: AppTypography.bodySecondary,
            ),
          ],
        ),
      ),
    );
  }
}

/// 질(Quality) — 서버 계산. 부족·초과한 영양소를 함께 적는다.
class _QualityCard extends StatelessWidget {
  const _QualityCard({required this.result});

  final MealEvaluation? result;

  /// "단백질 부족 · 나트륨 초과". 다 적정이면 그렇게 말한다.
  static String _summary(List<NutrientStatus> nutrients) {
    final flagged = [
      for (final n in nutrients)
        if (n.state == 'SHORT')
          '${n.label} 부족'
        else if (n.state == 'OVER')
          '${n.label} 초과',
    ];
    return flagged.isEmpty ? '영양 균형이 괜찮아요' : flagged.join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    final r = result;
    final score = r?.scores.quality;
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
            _ScoreBar(score: score),
            const SizedBox(height: AppSpacing.sm),
            Text(
              r == null
                  ? _beforeConfirm
                  : score == null
                  ? _noNutrition
                  : _summary(r.nutrients),
              style: AppTypography.bodySecondary,
            ),
          ],
        ),
      ),
    );
  }
}

/// 0~100 점수 막대 + 숫자. 점수가 없으면 빈 막대.
class _ScoreBar extends StatelessWidget {
  const _ScoreBar({required this.score});

  final int? score;

  @override
  Widget build(BuildContext context) {
    final s = score;
    return Row(
      children: [
        Expanded(
          child: ClipRRect(
            borderRadius: AppRadius.pillRadius,
            child: LinearProgressIndicator(
              value: (s ?? 0) / 100,
              minHeight: 8,
              backgroundColor: AppColors.primaryTint,
              color: AppColors.quality,
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Text(s == null ? '-' : '$s', style: AppTypography.sectionHead),
      ],
    );
  }
}

/// 포만감(Satiety) — 이 화면에서 사용자가 직접 입력하는 유일한 축.
class _SatietyCard extends StatelessWidget {
  const _SatietyCard({
    required this.value,
    required this.onChanged,
    this.onChangeEnd,
  });

  /// 0~100.
  final double value;

  /// null 이면 바꿀 수 없다(불러오는 중·저장 중).
  final ValueChanged<double>? onChanged;

  /// 손을 뗐을 때 — 저장한다.
  final ValueChanged<double>? onChangeEnd;

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
                    onChangeEnd: onChangeEnd,
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
