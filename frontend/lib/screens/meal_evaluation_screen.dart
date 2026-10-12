import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../common/api_format.dart';
import '../common/api_image.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'next_meal_suggestion_screen.dart';

/// 식사 평가 (Q·Q·S) — Figma `S6BHeo0ZebzY4PAQOSp0EW` node `1:243`(화면 6 · 식사 평가 (Q·Q·S 수정)).
///
/// 식후 포만감은 **이 화면에서만** 받는다. 서버는 확정(`confirm`) 때 식후
/// 포만감을 꼭 받아 그 자리에서 Q·Q·S 를 계산하므로:
///
/// 1. 확정 전 — 포만감 슬라이더만 움직이고 양·질은 "입력하면 계산돼요".
/// 2. "평가 받기" → 확정 → 받은 점수로 양·질·포만감 카드를 채운다.
/// 3. "다음 끼니 제안 보기" → 7번.
///
/// 평가 뒤에도 포만감을 고칠 수 있다. 슬라이더에서 손을 떼면 바뀐 값으로 다시
/// 확정한다 — 서버가 재확정을 허용하고, 점수가 바뀌면 AI 피드백도 새로 만든다
/// (BE `services/evaluation` `_is_stale_feedback`).
class MealEvaluationScreen extends StatefulWidget {
  const MealEvaluationScreen({super.key, required this.mealId, this.imageUrl});

  final String mealId;

  /// 식사 사진(/media/...). 없으면 자리만 잡는다.
  final String? imageUrl;

  @override
  State<MealEvaluationScreen> createState() => _MealEvaluationScreenState();
}

class _MealEvaluationScreenState extends State<MealEvaluationScreen> {
  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  /// 확정 결과. null 이면 아직 확정 전.
  MealEvaluation? _result;
  String? _error;

  /// 식후 포만감(0~100). 확정 전에는 입력값, 확정 뒤에는 고치는 중인 값.
  double _satiety = 50;
  bool _busy = false;

  Future<void> _confirm() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await _api.confirm(
        widget.mealId,
        satietyAfterPct: _satiety.round(),
      );
      if (mounted) setState(() => _result = result);
    } catch (e) {
      if (mounted) setState(() => _error = '평가하지 못했어요: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 평가 뒤 포만감을 고치면 그 값으로 다시 확정한다. 실패하면 원래 값으로
  /// 돌린다. 확정 전에는 아무것도 하지 않는다("평가 받기" 가 보낸다).
  Future<void> _saveSatiety(double value) async {
    final current = _result?.scores.satiety;
    if (current == null || current == value.round()) return;
    setState(() => _busy = true);
    try {
      final result = await _api.confirm(
        widget.mealId,
        satietyAfterPct: value.round(),
      );
      if (mounted) setState(() => _result = result);
    } catch (e) {
      if (!mounted) return;
      setState(() => _satiety = current.toDouble());
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('포만감을 바꾸지 못했어요: $e')));
    } finally {
      if (mounted) setState(() => _busy = false);
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
        leading: IconButton(
          icon: const Icon(Icons.chevron_left),
          onPressed: () => Navigator.of(context).maybePop(),
          tooltip: '뒤로',
        ),
        title: const Text('식사 평가'),
        titleTextStyle: AppTypography.sectionHead.copyWith(fontSize: 14),
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
                borderRadius: AppRadius.xlRadius,
                child: Container(
                  width: double.infinity,
                  height: 247,
                  color: AppColors.surfaceSage,
                  child: imageUrl == null ? null : ApiImage(imageUrl),
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              _QuantityCard(result: result),
              const SizedBox(height: AppSpacing.md),
              _QualityCard(result: result),
              const SizedBox(height: AppSpacing.md),
              // 식후 포만감은 여기서만 받는다. 평가 뒤에 고치면 다시 확정한다.
              _SatietyCard(
                value: _satiety,
                onChanged: _busy ? null : (v) => setState(() => _satiety = v),
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
              const SizedBox(height: AppSpacing.lg),

              SizedBox(
                width: double.infinity,
                height: 56,
                child: FilledButton(
                  onPressed: _busy
                      ? null
                      : result == null
                      ? _confirm
                      : () => _viewNextMealSuggestion(result),
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    shape: const RoundedRectangleBorder(
                      borderRadius: AppRadius.xlRadius,
                    ),
                  ),
                  child: _busy
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: AppColors.textInverse,
                          ),
                        )
                      : Text(
                          result == null ? '평가 받기' : '다음 끼니 제안 보기',
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

/// 확정 전 양·질 카드 안내.
const _beforeConfirm = '포만감을 입력하면 계산돼요';

/// 영양 정보가 있는 음식이 없어 계산하지 못했을 때.
const _noNutrition = '영양 정보가 있는 음식이 없어 계산하지 못했어요';

/// Q·Q·S 카드 공통 틀. 왼쪽에 원형 배지(Q/S), 오른쪽에 제목 줄과 [body] 를 쌓는다.
///
/// 제목 · 막대 · 설명이 모두 배지 오른쪽의 같은 세로선에서 시작한다(Figma).
/// [body] 는 제목 줄 아래에 놓인다.
class _AxisCard extends StatelessWidget {
  const _AxisCard({
    required this.letter,
    required this.badgeColor,
    required this.badgeBackground,
    required this.title,
    required this.englishLabel,
    required this.trailing,
    required this.body,
  });

  final String letter;
  final Color badgeColor;
  final Color badgeBackground;
  final String title;
  final String englishLabel;

  /// 제목 줄 오른쪽 끝(`AI 계산` · `내가 입력해요`).
  final Widget trailing;
  final Widget body;

  static const double _badgeSize = 26;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 14),
      decoration: _cardDecoration,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
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
              style: AppTypography.emphasis.copyWith(
                fontSize: 12.5,
                color: badgeColor,
              ),
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    Text(
                      title,
                      style: AppTypography.cardTitle.copyWith(fontSize: 13.5),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      englishLabel,
                      style: AppTypography.caption.copyWith(
                        fontSize: 10.5,
                        color: AppColors.textSecondary,
                      ),
                    ),
                    const Spacer(),
                    trailing,
                  ],
                ),
                body,
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 카드 바탕(흰색 + 회색 테두리, 둥근 16).
final BoxDecoration _cardDecoration = BoxDecoration(
  color: AppColors.surface,
  border: Border.all(color: AppColors.border),
  borderRadius: AppRadius.xlRadius,
);

/// 양·질 카드 오른쪽 위 `AI 계산`.
const Widget _aiLabel = _AiLabel();

class _AiLabel extends StatelessWidget {
  const _AiLabel();

  @override
  Widget build(BuildContext context) {
    return Text(
      'AI 계산',
      style: AppTypography.caption.copyWith(
        fontSize: 9.5,
        color: AppColors.textPrimary,
      ),
    );
  }
}

/// "내가 입력해요" 같은 다크 필 배지.
///
/// NOTE: 전용 색이 없어 AppColors.textPrimary/textInverse 를 빌려 썼다.
class _DarkPill extends StatelessWidget {
  const _DarkPill({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: const BoxDecoration(
        color: AppColors.textPrimary,
        borderRadius: AppRadius.pillRadius,
      ),
      child: Text(
        label,
        style: AppTypography.emphasis.copyWith(
          color: AppColors.textInverse,
          fontSize: 9.5,
          height: 1.25,
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
    return _AxisCard(
      letter: 'Q',
      badgeColor: AppColors.quantity,
      badgeBackground: AppColors.quantityBg,
      title: '양',
      englishLabel: 'Quantity',
      trailing: _aiLabel,
      body: _ScoreBody(
        score: score,
        color: AppColors.quantity,
        description: r == null
            ? _beforeConfirm
            : score == null
            ? _noNutrition
            : '${stageLabel(r.stage)} 기준 한 끼 양이에요',
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
    return _AxisCard(
      letter: 'Q',
      badgeColor: AppColors.quality,
      badgeBackground: AppColors.qualityBg,
      title: '질',
      englishLabel: 'Quality',
      trailing: _aiLabel,
      body: _ScoreBody(
        score: score,
        color: AppColors.quality,
        description: r == null
            ? _beforeConfirm
            : score == null
            ? _noNutrition
            : _summary(r.nutrients),
      ),
    );
  }
}

/// 막대 + 오른쪽 숫자 + 아래 설명. 제목과 같은 왼쪽 선에서 시작한다.
class _ScoreBody extends StatelessWidget {
  const _ScoreBody({
    required this.score,
    required this.color,
    required this.description,
  });

  final int? score;
  final Color color;
  final String description;

  @override
  Widget build(BuildContext context) {
    final s = score;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(child: _ScoreBar(score: s, color: color)),
            const SizedBox(width: 12),
            SizedBox(
              width: 28,
              child: Text(
                s == null ? '-' : '$s',
                textAlign: TextAlign.right,
                style: AppTypography.cardTitle.copyWith(fontSize: 14),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Text(
          description,
          style: AppTypography.bodySecondary.copyWith(height: 1.25),
        ),
      ],
    );
  }
}

/// 0~100 점수 막대. 점수가 생기면 왼쪽에서부터 차오른다. 없으면 빈 막대.
class _ScoreBar extends StatelessWidget {
  const _ScoreBar({required this.score, required this.color});

  final int? score;
  final Color color;

  /// 막대가 차오르는 시간.
  static const Duration _fillDuration = Duration(milliseconds: 700);

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      // begin 이 0 이라 처음 그릴 때도 0 에서부터 차오른다. 점수가 바뀌면 지금 값에서 이어서 움직인다.
      tween: Tween<double>(begin: 0, end: (score ?? 0) / 100),
      duration: _fillDuration,
      curve: Curves.easeOutCubic,
      builder: (context, value, _) => ClipRRect(
        borderRadius: AppRadius.pillRadius,
        child: LinearProgressIndicator(
          value: value,
          minHeight: 8,
          backgroundColor: AppColors.surfaceMuted,
          color: color,
        ),
      ),
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
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(13, 14, 13, 12),
      decoration: _cardDecoration,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 배지 · 제목 · 안내 문구는 양·질 카드와 같은 선에서 시작한다.
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: _AxisCard._badgeSize,
                height: _AxisCard._badgeSize,
                alignment: Alignment.center,
                decoration: const BoxDecoration(
                  color: AppColors.satietyBg,
                  shape: BoxShape.circle,
                ),
                child: Text(
                  'S',
                  style: AppTypography.emphasis.copyWith(
                    fontSize: 12.5,
                    color: AppColors.satiety,
                  ),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          '포만감',
                          style: AppTypography.cardTitle.copyWith(
                            fontSize: 13.5,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          'Satiety',
                          style: AppTypography.caption.copyWith(
                            fontSize: 10.5,
                            color: AppColors.textSecondary,
                          ),
                        ),
                        const Spacer(),
                        const _DarkPill(label: '내가 입력해요'),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '식사 후 지금, 얼마나 부르세요?',
                      style: AppTypography.bodySecondary.copyWith(height: 1.25),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // 슬라이더는 카드 안쪽 왼쪽 끝에서 시작해 오른쪽 입력 상자 앞에서 끝난다.
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      SliderTheme(
                        data: SliderThemeData(
                          trackHeight: 8,
                          trackShape: const _FullWidthTrackShape(),
                          thumbShape: const _RingThumbShape(),
                          overlayShape: SliderComponentShape.noOverlay,
                          activeTrackColor: AppColors.satiety,
                          inactiveTrackColor: AppColors.surfaceMuted,
                          disabledActiveTrackColor: AppColors.satiety,
                          disabledInactiveTrackColor: AppColors.surfaceMuted,
                        ),
                        child: Slider(
                          value: value,
                          min: 0,
                          max: 100,
                          onChanged: onChanged,
                          onChangeEnd: onChangeEnd,
                        ),
                      ),
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            '0%',
                            style: AppTypography.caption.copyWith(fontSize: 8),
                          ),
                          Text(
                            '100%',
                            style: AppTypography.caption.copyWith(fontSize: 8),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 20),
                Container(
                  width: 80,
                  height: 32,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: AppColors.surface,
                    borderRadius: BorderRadius.circular(9),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        '${value.toInt()}',
                        style: AppTypography.cardTitle.copyWith(fontSize: 15),
                      ),
                      const SizedBox(width: 2),
                      Text(
                        '%',
                        style: AppTypography.caption.copyWith(fontSize: 10),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 슬라이더 막대가 위젯 폭을 그대로 쓰게 한다. 기본 모양은 양 끝을 손잡이 반지름만큼 비워
/// 다른 막대와 길이가 맞지 않는다.
class _FullWidthTrackShape extends RoundedRectSliderTrackShape {
  const _FullWidthTrackShape();

  @override
  Rect getPreferredRect({
    required RenderBox parentBox,
    Offset offset = Offset.zero,
    required SliderThemeData sliderTheme,
    bool isEnabled = false,
    bool isDiscrete = false,
  }) {
    final height = sliderTheme.trackHeight ?? 8;
    final top = offset.dy + (parentBox.size.height - height) / 2;
    return Rect.fromLTWH(offset.dx, top, parentBox.size.width, height);
  }
}

/// 흰 바탕에 포만감 색 테두리를 두른 손잡이(Figma 22px).
class _RingThumbShape extends SliderComponentShape {
  const _RingThumbShape();

  static const double _radius = 11;

  @override
  Size getPreferredSize(bool isEnabled, bool isDiscrete) =>
      const Size.fromRadius(_radius);

  @override
  void paint(
    PaintingContext context,
    Offset center, {
    required Animation<double> activationAnimation,
    required Animation<double> enableAnimation,
    required bool isDiscrete,
    required TextPainter labelPainter,
    required RenderBox parentBox,
    required SliderThemeData sliderTheme,
    required TextDirection textDirection,
    required double value,
    required double textScaleFactor,
    required Size sizeWithOverflow,
  }) {
    final canvas = context.canvas;
    canvas.drawShadow(
      Path()..addOval(Rect.fromCircle(center: center, radius: _radius)),
      AppColors.textPrimary,
      2,
      true,
    );
    canvas.drawCircle(center, _radius, Paint()..color = AppColors.surface);
    canvas.drawCircle(
      center,
      _radius - 1.25,
      Paint()
        ..color = AppColors.satiety
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5,
    );
  }
}
