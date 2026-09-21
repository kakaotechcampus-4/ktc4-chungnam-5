import 'dart:async';

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 분석 진행 3단계.
///
/// **서버는 이 단계를 모른다.** `meals.status` 는 `ANALYZING` 하나뿐이고
/// `meal.analyze` 워커도 `ANALYZING → REVIEW_REQUIRED` 로 한 번에 넘어간다
/// (`backend/app/worker/jobs/analyze_meal.py`). 즉 아래 3단계는 실제 서버
/// 진행률이 아니라, 폴링 경과 시간에 맞춰 프론트가 연출하는 것이다.
enum AnalysisStep { recognizeFood, matchNutritionDb, applyDoseStage }

enum _StepState { done, inProgress, pending }

/// 분석 상태 폴링 서비스.
///
/// 지금은 더미로 몇 번째 호출인지만 세서 흉내 낸다. 실제 연동 시
/// `GET /meals/{mealId}` 를 1.5초 간격으로 불러 `status` 가
/// `REVIEW_REQUIRED` 인지만 보면 된다 — 그 이상의 세부 단계는 없다.
class MealAnalysisApiService {
  int _pollCount = 0;

  Future<bool> isReviewRequired(String mealId) async {
    await Future.delayed(const Duration(milliseconds: 300));
    _pollCount++;
    return _pollCount >= 4;
  }
}

/// AI 분석 진행 — Figma `hOxrHBitBpjwIBBg2GO49y` node `89:6`.
///
/// README 폴링 규칙(1.5초 간격 · 45초 초과 시 FCM 푸시로 인계)을 그대로
/// 따른다. 화면을 벗어나도(뒤로가기 없이 다른 탭으로 이동 등) 분석 자체는
/// 서버에서 계속되므로, 여기서 취소해도 서버 쪽 분석까지 멈추지는 않는다
/// — "취소"는 폴링을 그만 보는 것뿐이다.
class MealAnalysisScreen extends StatefulWidget {
  const MealAnalysisScreen({super.key, required this.mealId});

  final String mealId;

  @override
  State<MealAnalysisScreen> createState() => _MealAnalysisScreenState();
}

class _MealAnalysisScreenState extends State<MealAnalysisScreen> {
  static const _pollInterval = Duration(milliseconds: 1500);
  static const _pollTimeout = Duration(seconds: 45);

  final MealAnalysisApiService _api = MealAnalysisApiService();
  final Stopwatch _stopwatch = Stopwatch();
  Timer? _pollTimer;

  AnalysisStep _step = AnalysisStep.recognizeFood;

  @override
  void initState() {
    super.initState();
    _stopwatch.start();
    _pollTimer = Timer.periodic(_pollInterval, (_) => _poll());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _poll() async {
    if (_stopwatch.elapsed >= _pollTimeout) {
      _pollTimer?.cancel();
      // TODO: 45초 초과 — 폴링을 끊고 FCM 푸시 인계 안내로 전환한다.
      return;
    }

    final done = await _api.isReviewRequired(widget.mealId);
    if (!mounted) return;

    if (done) {
      _pollTimer?.cancel();
      // TODO: REVIEW_REQUIRED 확정 — 음식 확인·수정 화면(5번)으로 교체 이동한다.
      return;
    }

    setState(() => _step = _stepFor(_stopwatch.elapsed));
  }

  /// 서버 하위 단계가 없어 경과 시간을 3등분해 흉내 낸다.
  /// 화면 문구("보통 5~10초 걸려요") 기준.
  AnalysisStep _stepFor(Duration elapsed) {
    if (elapsed < const Duration(seconds: 3)) {
      return AnalysisStep.recognizeFood;
    }
    if (elapsed < const Duration(seconds: 7)) {
      return AnalysisStep.matchNutritionDb;
    }
    return AnalysisStep.applyDoseStage;
  }

  _StepState _stateFor(AnalysisStep step) {
    if (step.index < _step.index) return _StepState.done;
    if (step.index == _step.index) return _StepState.inProgress;
    return _StepState.pending;
  }

  void _cancel() {
    _pollTimer?.cancel();
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        centerTitle: true,
        leadingWidth: 72,
        leading: TextButton(
          onPressed: _cancel,
          style: TextButton.styleFrom(foregroundColor: AppColors.textSecondary),
          child: Text(
            '취소',
            style: AppTypography.body.copyWith(color: AppColors.textSecondary),
          ),
        ),
        title: Text('분석 중', style: AppTypography.screenTitle),
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
              // 업로드한 사진. 실제 URL 로 교체 예정, 지금은 자리만 잡는다.
              ClipRRect(
                borderRadius: AppRadius.lgRadius,
                child: Container(
                  width: double.infinity,
                  height: 240,
                  color: AppColors.surfaceSage,
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              Text('식사를 분석하고 있어요', style: AppTypography.sectionHead),
              const SizedBox(height: AppSpacing.xs),
              Text(
                '보통 5~10초 걸려요. 화면을 벗어나도 계속 분석돼요.',
                style: AppTypography.bodySecondary,
              ),
              const SizedBox(height: AppSpacing.lg),

              Card(
                child: Padding(
                  padding: const EdgeInsets.all(AppSpacing.md),
                  child: Column(
                    children: [
                      _StepRow(
                        label: '음식 인식',
                        description: '사진에서 음식과 양을 추정했어요',
                        state: _stateFor(AnalysisStep.recognizeFood),
                      ),
                      const Divider(height: AppSpacing.xl),
                      _StepRow(
                        label: '영양 DB 매칭',
                        description: '공공 식품영양성분 DB와 맞추고 있어요',
                        state: _stateFor(AnalysisStep.matchNutritionDb),
                      ),
                      const Divider(height: AppSpacing.xl),
                      _StepRow(
                        label: '투약 단계 기준 적용',
                        description: '유지기 기준으로 Q·Q·S를 계산해요',
                        state: _stateFor(AnalysisStep.applyDoseStage),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.lg),

              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text('전체 진행', style: AppTypography.bodySecondary),
                  Text(
                    '${_step.index + 1} / ${AnalysisStep.values.length} 단계',
                    style: AppTypography.emphasis,
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.sm),
              ClipRRect(
                borderRadius: AppRadius.pillRadius,
                child: LinearProgressIndicator(
                  value: (_step.index + 1) / AnalysisStep.values.length,
                  minHeight: 6,
                  backgroundColor: AppColors.surfaceMuted,
                  color: AppColors.primary,
                ),
              ),
              const SizedBox(height: AppSpacing.lg),

              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(AppSpacing.cardPadding),
                decoration: const BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: AppRadius.lgRadius,
                ),
                child: Text(
                  '분석이 끝나면 음식과 양을 직접 확인·수정할 수 있어요. 확인한 내용만 평가에 사용해요.',
                  style: AppTypography.bodySecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({
    required this.label,
    required this.description,
    required this.state,
  });

  final String label;
  final String description;
  final _StepState state;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _StepIcon(state: state),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: AppTypography.cardTitle),
              const SizedBox(height: 2),
              Text(description, style: AppTypography.bodySecondary),
            ],
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Text(
          _badgeLabel,
          style: AppTypography.emphasis.copyWith(color: _badgeColor),
        ),
      ],
    );
  }

  String get _badgeLabel => switch (state) {
    _StepState.done => '완료',
    _StepState.inProgress => '진행 중',
    _StepState.pending => '대기',
  };

  Color get _badgeColor => switch (state) {
    // NOTE: 완료 초록은 design-system.md 팔레트에 별도 토큰이 없어 임시로
    // AppColors.quality 를 빌려 썼다. §2 규칙("Q·Q·S 색 재사용 금지")에
    // 어긋나므로, 이 화면이 실제로 확정되면 전용 성공/완료 토큰을 추가하고
    // 교체해야 한다.
    _StepState.done => AppColors.quality,
    _StepState.inProgress => AppColors.primary,
    _StepState.pending => AppColors.inactive,
  };
}

class _StepIcon extends StatelessWidget {
  const _StepIcon({required this.state});

  final _StepState state;

  static const double _size = 28;

  @override
  Widget build(BuildContext context) {
    return switch (state) {
      _StepState.done => Container(
        width: _size,
        height: _size,
        decoration: const BoxDecoration(
          // NOTE: _StepRow._badgeColor 와 같은 임시 초록 토큰 이슈.
          color: AppColors.quality,
          shape: BoxShape.circle,
        ),
        child: const Icon(Icons.check, size: 16, color: AppColors.textInverse),
      ),
      _StepState.inProgress => Container(
        width: _size,
        height: _size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: AppColors.primaryTint,
          border: Border.all(color: AppColors.primary, width: 2),
        ),
      ),
      _StepState.pending => Container(
        width: _size,
        height: _size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          border: Border.all(color: AppColors.border, width: 2),
        ),
      ),
    };
  }
}
