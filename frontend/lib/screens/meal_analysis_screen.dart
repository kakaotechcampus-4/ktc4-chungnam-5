import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_review_screen.dart';

/// 분석 진행 3단계. 서버 `steps[]` 의 `key` 순서와 같다.
enum AnalysisStep { recognizeFood, matchNutritionDb, applyDoseStage }

enum _StepState { done, inProgress, pending }

/// 분석 화면이 멈춘 이유.
enum _Stopped { failed, timedOut }

/// AI 분석 진행 — Figma `hOxrHBitBpjwIBBg2GO49y` node `89:6`.
///
/// `GET /meals/{mealId}` 를 서버가 준 간격(`pollIntervalMs`)으로 불러
/// 최초 분석이 끝나면(`REVIEW_REQUIRED`) 음식 확인 화면으로 넘어간다.
/// 서버가 준 한도(`timeoutMs`)를 넘기면 폴링만 멈춘다 — 분석은 서버에서
/// 계속되므로 기록 탭에서 이어 볼 수 있다. 알림으로 넘기는 건 FE-14 몫이다.
class MealAnalysisScreen extends StatefulWidget {
  const MealAnalysisScreen({super.key, required this.meal, this.photo});

  final MealCreated meal;

  /// 올린 사진(미리보기용). 텍스트로 입력했으면 null.
  final Uint8List? photo;

  @override
  State<MealAnalysisScreen> createState() => _MealAnalysisScreenState();
}

class _MealAnalysisScreenState extends State<MealAnalysisScreen> {
  /// 끝난 걸 보여 주고 넘어가기까지 잠깐 둔다(3단계 완료 표시).
  static const _doneHold = Duration(milliseconds: 600);

  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  AnalysisStep _step = AnalysisStep.recognizeFood;
  _Stopped? _stopped;

  @override
  void initState() {
    super.initState();
    _poll();
  }

  Future<void> _poll() async {
    final deadline = DateTime.now().add(widget.meal.timeout);
    while (mounted) {
      await Future.delayed(widget.meal.pollInterval);
      if (!mounted) return;
      final MealDetail meal;
      try {
        meal = await _api.fetchMeal(widget.meal.mealId);
      } catch (_) {
        // 한 번 실패는 넘기고 다음 차례에 다시 묻는다. 한도는 그대로 센다.
        if (DateTime.now().isAfter(deadline)) return _stop(_Stopped.timedOut);
        continue;
      }
      if (!mounted) return;
      if (meal.status == 'FAILED') return _stop(_Stopped.failed);
      if (!meal.isFirstAnalysis) return _finish();
      if (DateTime.now().isAfter(deadline)) return _stop(_Stopped.timedOut);
    }
  }

  void _stop(_Stopped reason) {
    if (mounted) setState(() => _stopped = reason);
  }

  /// 서버는 음식 인식이 끝나면 바로 확인 단계로 넘어간다(DB 매칭·단계 적용은
  /// 확정 때 한다). 남은 단계를 완료로 보여 주고 음식 확인 화면으로 간다.
  Future<void> _finish() async {
    setState(() => _step = AnalysisStep.applyDoseStage);
    await Future.delayed(_doneHold);
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => MealReviewScreen(mealId: widget.meal.mealId),
      ),
    );
  }

  _StepState _stateFor(AnalysisStep step) {
    if (step.index < _step.index) return _StepState.done;
    if (step.index == _step.index) return _StepState.inProgress;
    return _StepState.pending;
  }

  void _cancel() => Navigator.of(context).pop();

  String get _title => switch (_stopped) {
    null => '식사를 분석하고 있어요',
    _Stopped.failed => '음식을 알아보지 못했어요',
    _Stopped.timedOut => '분석이 오래 걸리고 있어요',
  };

  String get _subtitle => switch (_stopped) {
    null => '보통 5~10초 걸려요. 화면을 벗어나도 계속 분석돼요.',
    _Stopped.failed => '사진을 다시 찍거나 먹은 음식을 직접 적어 주세요.',
    _Stopped.timedOut => '분석은 계속돼요. 끝나면 기록 탭에서 이어서 확인할 수 있어요.',
  };

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
              // 올린 사진. 텍스트로 입력했으면 자리만 잡는다.
              ClipRRect(
                borderRadius: AppRadius.lgRadius,
                child: Container(
                  width: double.infinity,
                  height: 240,
                  color: AppColors.surfaceSage,
                  child: widget.photo == null
                      ? null
                      : Image.memory(widget.photo!, fit: BoxFit.cover),
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              Text(_title, style: AppTypography.sectionHead),
              const SizedBox(height: AppSpacing.xs),
              Text(_subtitle, style: AppTypography.bodySecondary),
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
                        description: '투약 단계 기준으로 Q·Q·S를 계산해요',
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

              if (_stopped == null)
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
                )
              else
                SizedBox(
                  width: double.infinity,
                  height: AppLayout.primaryButtonHeight,
                  child: FilledButton(
                    onPressed: _cancel,
                    child: Text(
                      _stopped == _Stopped.failed ? '다시 기록하기' : '닫기',
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
