import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../common/api_format.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

/// 다음 끼니 제안(7번) — 이번 끼니 영양소 상태 + AI 제안.
///
/// 제안 문장은 AI 가 만들어 늦게 온다. 처음 `GET /meals/{id}/feedback` 을
/// 부르면 서버가 생성 작업을 걸고, 준비될 때까지(`PENDING`·`GENERATING`)
/// 잠깐씩 다시 묻는다. 영양소 상태는 그와 상관없이 먼저 보인다.
///
/// 맨 아래에는 그 끼니를 먹은 날의 하루 요약(`/insights/daily`)을 같이 보인다.
/// 없거나 낡았으면 새로 만들어 달라고 한 뒤 기다린다.
///
/// 제안을 저장하거나 다시 받는 API 는 아직 없어 그 버튼은 두지 않는다.
class NextMealSuggestionScreen extends StatefulWidget {
  final String mealId;

  /// 평가 화면에서 받은 확정 결과. 없으면(다른 곳에서 열면) 서버에서 받는다.
  final MealEvaluation? evaluation;

  /// 먹은 시각(KST 벽시계). 하루 요약의 날짜다. 없으면 끼니를 조회해 알아낸다.
  final DateTime? eatenAt;

  const NextMealSuggestionScreen({
    super.key,
    required this.mealId,
    this.evaluation,
    this.eatenAt,
  });

  @override
  State<NextMealSuggestionScreen> createState() =>
      _NextMealSuggestionScreenState();
}

class _NextMealSuggestionScreenState extends State<NextMealSuggestionScreen> {
  /// 제안이 준비됐는지 다시 묻는 간격과 횟수(약 30초).
  static const _feedbackPoll = Duration(milliseconds: 1500);
  static const _feedbackPollLimit = 20;

  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  late MealEvaluation? evaluation = widget.evaluation;
  MealFeedback? feedback;
  bool isLoading = true;
  String? errorMessage;

  /// 제안을 기다리다 포기했는지(실패·시간 초과).
  bool feedbackUnavailable = false;

  /// 하루 요약. [dailyDate] 는 그 날짜(KST).
  DateTime? dailyDate;
  DailyFeedback? daily;
  bool dailyUnavailable = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      isLoading = evaluation == null;
      errorMessage = null;
    });
    if (evaluation == null) {
      try {
        final result = await _api.fetchEvaluation(widget.mealId);
        if (!mounted) return;
        setState(() => evaluation = result);
      } catch (e) {
        if (mounted) setState(() => errorMessage = '불러오는 데 실패했어요: $e');
        return;
      } finally {
        if (mounted) setState(() => isLoading = false);
      }
    }
    _pollFeedback();
    _loadDaily();
  }

  /// 그날 하루 요약을 받는다. 없거나 낡았으면 새로 만들어 달라고 하고
  /// 준비될 때까지 잠깐씩 다시 묻는다(약 30초). 실패해도 화면은 그대로다.
  Future<void> _loadDaily() async {
    try {
      final date =
          widget.eatenAt ?? (await _api.fetchMeal(widget.mealId)).eatenAt;
      if (!mounted) return;
      setState(() => dailyDate = date);
      var result = await _api.fetchDailyFeedback(date);
      if (!mounted) return;
      setState(() => daily = result);
      if (!result.needsRefresh && !result.isGenerating) return;
      final interval = result.needsRefresh
          ? await _api.refreshDailyFeedback(date)
          : _feedbackPoll;
      for (var i = 0; i < _feedbackPollLimit; i++) {
        await Future.delayed(interval);
        if (!mounted) return;
        result = await _api.fetchDailyFeedback(date);
        if (!mounted) return;
        setState(() => daily = result);
        if (!result.isGenerating && !result.needsRefresh) return;
      }
    } catch (_) {
      // 아래 한 줄일 뿐이다. 실패해도 끼니 피드백은 그대로 쓴다.
    }
    if (mounted) setState(() => dailyUnavailable = true);
  }

  Future<void> _pollFeedback() async {
    for (var i = 0; i < _feedbackPollLimit && mounted; i++) {
      try {
        final result = await _api.fetchFeedback(widget.mealId);
        if (!mounted) return;
        setState(() => feedback = result);
        if (!result.isPending) {
          if (result.feedbackStatus == 'FAILED') {
            setState(() => feedbackUnavailable = true);
          }
          return;
        }
      } catch (_) {
        // 한 번 실패는 넘기고 다음 차례에 다시 묻는다.
      }
      await Future.delayed(_feedbackPoll);
    }
    if (mounted) setState(() => feedbackUnavailable = true);
  }

  /// 식사 기록 흐름을 모두 닫고 홈 탭(RootShell 0번)으로 돌아간다.
  /// 기록 탭에서 시작했어도 홈으로 간다.
  void _goHome() {
    context.read<TabState>().setIndex(0);
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: isLoading
            ? const Center(child: CircularProgressIndicator())
            : evaluation == null
            ? Center(
                child: RetryBlock(
                  message: errorMessage ?? '잠시 후 다시 시도해 주세요',
                  onRetry: _load,
                ),
              )
            : _buildContent(),
      ),
    );
  }

  Widget _buildContent() {
    return Column(
      children: [
        _buildHeader(),
        Expanded(
          child: ListView(
            padding: EdgeInsets.symmetric(
              horizontal: AppSpacing.screenHorizontal,
              vertical: AppSpacing.lg,
            ),
            children: [
              SizedBox(height: AppSpacing.xl),

              _buildNutrientHeader(evaluation!.stage),
              SizedBox(height: AppSpacing.md),
              _buildNutrientCard(evaluation!),

              SizedBox(height: AppSpacing.xxl),
              Text('그래서 이렇게 채워보세요', style: AppTypography.sectionHead),
              SizedBox(height: AppSpacing.md),
              ..._buildSuggestions(),

              SizedBox(height: AppSpacing.xl),
              ..._buildDaily(),
            ],
          ),
        ),
        _buildSaveButton(),
      ],
    );
  }

  Widget _buildHeader() {
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.screenHorizontal),
      child: Row(
        children: [
          IconButton(
            icon: const Icon(Icons.chevron_left),
            onPressed: () => Navigator.pop(context),
          ),
        ],
      ),
    );
  }

  Widget _buildNutrientHeader(String stage) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Text('현재 영양소 상태', style: AppTypography.sectionHead),
        Text(
          '${stageLabel(stage)} 1끼 기준',
          style: AppTypography.caption.copyWith(color: AppColors.textTertiary),
        ),
      ],
    );
  }

  Widget _buildNutrientCard(MealEvaluation eval) {
    return Container(
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (int i = 0; i < eval.nutrients.length; i++) ...[
            _buildNutrientRow(eval.nutrients[i]),
            if (i != eval.nutrients.length - 1) SizedBox(height: AppSpacing.lg),
          ],
        ],
      ),
    );
  }

  Color _stateColor(String? state) {
    switch (state) {
      case 'SHORT':
        return AppColors.primary;
      case 'OVER':
        return AppColors.textSecondary;
      case 'OK':
      default:
        return AppColors.textTertiary;
    }
  }

  String _statusText(NutrientStatus n) {
    final current = n.current;
    // 영양 정보가 있는 음식이 없으면 이 영양소는 계산하지 못했다.
    if (current == null) return '정보 없음';
    if (n.target == null) {
      return '${current.toInt()}${n.unit}';
    }
    final diff = (current - n.target!).abs().toInt();
    final diffLabel = switch (n.state) {
      'SHORT' => '$diff${n.unit} 부족',
      'OVER' => '$diff${n.unit} 초과',
      'OK' => '적정',
      _ => '',
    };
    final base = '${current.toInt()} / ${n.target!.toInt()}${n.unit}';
    return diffLabel.isEmpty ? base : '$base · $diffLabel';
  }

  Widget _buildNutrientRow(NutrientStatus n) {
    final hasTarget = n.target != null && n.target! > 0;
    final current = n.current ?? 0;
    final ratio = hasTarget ? (current / n.target!).clamp(0.0, 1.0) : 0.3;
    final color = _stateColor(n.state);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            SizedBox(
              width: 56,
              child: Text(n.label, style: AppTypography.cardTitle),
            ),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: LinearProgressIndicator(
                  value: ratio,
                  minHeight: 8,
                  backgroundColor: AppColors.surfaceMuted,
                  valueColor: AlwaysStoppedAnimation(color),
                ),
              ),
            ),
          ],
        ),
        SizedBox(height: AppSpacing.xs),
        Padding(
          padding: const EdgeInsets.only(left: 56),
          child: Text(
            _statusText(n),
            style: AppTypography.caption.copyWith(color: color),
          ),
        ),
      ],
    );
  }

  /// "오늘 하루 요약" — 그 끼니를 먹은 날 전체에 대한 한 줄.
  List<Widget> _buildDaily() {
    final date = dailyDate;
    final now = DateTime.now();
    final isToday =
        date != null &&
        date.year == now.year &&
        date.month == now.month &&
        date.day == now.day;
    final title = date == null || isToday
        ? '오늘 하루 요약'
        : '${date.month}월 ${date.day}일 하루 요약';
    final d = daily;
    final summary = d?.summary;
    final Widget body;
    if (summary != null) {
      body = Text(summary, style: AppTypography.body);
    } else if (dailyUnavailable ||
        (d != null && !d.isGenerating && !d.needsRefresh)) {
      body = Text('지금은 하루 요약을 만들지 못했어요.', style: AppTypography.bodySecondary);
    } else {
      body = const SkeletonBox(width: double.infinity, height: 40);
    }
    return [
      Text(title, style: AppTypography.sectionHead),
      SizedBox(height: AppSpacing.md),
      Container(
        width: double.infinity,
        padding: EdgeInsets.all(AppSpacing.cardPadding),
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(12),
        ),
        child: body,
      ),
    ];
  }

  List<Widget> _buildSuggestions() {
    // 의료 판단이 필요한 내용이면 제안 대신 상담 안내만 보인다(§7).
    if (feedback?.isBlocked == true) return [const NoticeBlock()];
    final suggestions = feedback?.suggestions ?? const [];
    if (suggestions.isNotEmpty) {
      return [
        for (final s in suggestions)
          Padding(
            padding: EdgeInsets.only(bottom: AppSpacing.cardGap),
            child: _buildSuggestionCard(s),
          ),
      ];
    }
    if (feedbackUnavailable || feedback?.isPending == false) {
      return [
        Text(
          '지금은 제안을 만들지 못했어요. 나중에 기록 탭에서 다시 볼 수 있어요.',
          style: AppTypography.bodySecondary,
        ),
      ];
    }
    return [
      const SkeletonBox(width: double.infinity, height: 72),
      SizedBox(height: AppSpacing.cardGap),
      const SkeletonBox(width: double.infinity, height: 72),
    ];
  }

  Widget _buildSuggestionCard(MealSuggestion s) {
    return Container(
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(
              color: AppColors.surfaceMuted,
              shape: BoxShape.circle,
            ),
            child: const Icon(Icons.add, color: AppColors.textSecondary),
          ),
          SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(s.foodName, style: AppTypography.cardTitle),
                SizedBox(height: AppSpacing.xs),
                if (s.nutrients.isNotEmpty)
                  Text(
                    s.nutrients
                        .map(
                          (n) =>
                              '${_nutrientLabel(n.code)} +${n.amountG.toInt()}g',
                        )
                        .join(', '),
                    style: AppTypography.bodySecondary,
                  ),
                SizedBox(height: AppSpacing.xs),
                Text(
                  s.advice,
                  style: AppTypography.body.copyWith(color: AppColors.primary),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _nutrientLabel(String code) {
    switch (code) {
      case 'PROTEIN':
        return '단백질';
      case 'FIBER':
        return '식이섬유';
      case 'SODIUM':
        return '나트륨';
      default:
        return code;
    }
  }

  Widget _buildSaveButton() {
    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.sm,
        AppSpacing.screenHorizontal,
        AppSpacing.lg,
      ),
      child: SizedBox(
        width: double.infinity,
        height: 52,
        child: ElevatedButton(
          onPressed: _goHome,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
          child: Text('홈으로', style: AppTypography.buttonLabel),
        ),
      ),
    );
  }
}
