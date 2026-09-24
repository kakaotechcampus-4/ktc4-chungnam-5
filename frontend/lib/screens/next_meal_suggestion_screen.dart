import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

class NutrientStatus {
  final String code;
  final String label;
  final double current;
  final double? target;
  final String unit;
  final String? state;

  NutrientStatus({
    required this.code,
    required this.label,
    required this.current,
    required this.unit,
    this.target,
    this.state,
  });

  factory NutrientStatus.fromJson(Map<String, dynamic> json) {
    return NutrientStatus(
      code: json['code'] as String,
      label: json['label'] as String,
      current: (json['current'] as num).toDouble(),
      target: json['target'] == null
          ? null
          : (json['target'] as num).toDouble(),
      unit: json['unit'] as String,
      state: json['state'] as String?,
    );
  }
}

class MealEvaluation {
  final String stage;
  final List<NutrientStatus> nutrients;

  MealEvaluation({required this.stage, required this.nutrients});

  factory MealEvaluation.fromJson(Map<String, dynamic> json) {
    return MealEvaluation(
      stage: json['stage'] as String,
      nutrients: (json['nutrients'] as List<dynamic>)
          .map((e) => NutrientStatus.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}

class SuggestionNutrient {
  final String code;
  final double amountG;

  SuggestionNutrient({required this.code, required this.amountG});

  factory SuggestionNutrient.fromJson(Map<String, dynamic> json) {
    return SuggestionNutrient(
      code: json['code'] as String,
      amountG: (json['amountG'] as num).toDouble(),
    );
  }
}

class MealSuggestion {
  final String foodName;
  final List<SuggestionNutrient> nutrients;
  final String advice;

  MealSuggestion({
    required this.foodName,
    required this.nutrients,
    required this.advice,
  });

  factory MealSuggestion.fromJson(Map<String, dynamic> json) {
    return MealSuggestion(
      foodName: json['foodName'] as String,
      nutrients: (json['nutrients'] as List<dynamic>)
          .map((e) => SuggestionNutrient.fromJson(e as Map<String, dynamic>))
          .toList(),
      advice: json['advice'] as String,
    );
  }
}

class MealFeedback {
  final String feedbackStatus;
  final String? summary;
  final List<MealSuggestion> suggestions;
  final Map<String, int>? expectedSatietyPct;
  final String safetyStatus;

  MealFeedback({
    required this.feedbackStatus,
    required this.suggestions,
    required this.safetyStatus,
    this.summary,
    this.expectedSatietyPct,
  });

  factory MealFeedback.fromJson(Map<String, dynamic> json) {
    return MealFeedback(
      feedbackStatus: json['feedbackStatus'] as String,
      summary: json['summary'] as String?,
      suggestions: json['suggestions'] == null
          ? []
          : (json['suggestions'] as List<dynamic>)
                .map((e) => MealSuggestion.fromJson(e as Map<String, dynamic>))
                .toList(),
      expectedSatietyPct: json['expectedSatietyPct'] == null
          ? null
          : Map<String, int>.from(json['expectedSatietyPct'] as Map),
      safetyStatus: json['safetyStatus'] as String,
    );
  }
}

class NextMealApiService {
  Future<MealEvaluation> fetchEvaluation(String mealId) async {
    await Future.delayed(const Duration(milliseconds: 300));
    return MealEvaluation.fromJson({
      'stage': 'MAINTENANCE',
      'nutrients': [
        {
          'code': 'PROTEIN',
          'label': '단백질',
          'current': 18,
          'target': 30,
          'unit': 'g',
          'state':
              'SHORT',
        },
        {
          'code': 'FIBER',
          'label': '식이섬유',
          'current': 4,
          'target': 12,
          'unit': 'g',
          'state': 'SHORT',
        },
        {
          'code': 'SODIUM',
          'label': '나트륨',
          'current': 1620,
          'target': 1300,
          'unit': 'mg',
          'state': 'OVER',
        },
      ],
    });
  }

  Future<MealFeedback> fetchFeedback(String mealId) async {
    await Future.delayed(const Duration(milliseconds: 300));
    return MealFeedback.fromJson({
      'feedbackStatus': 'READY',
      'summary': '유지기 기준 포만감이 부족한 식사예요.',
      'suggestions': [
        {
          'foodName': '두부 반 모 + 시금치나물',
          'nutrients': [
            {'code': 'PROTEIN', 'amountG': 14},
            {'code': 'FIBER', 'amountG': 4},
          ],
          'advice': '부족분을 거의 다 채워요',
        },
        {
          'foodName': '국물은 절반만, 채소부터',
          'nutrients': [],
          'advice': '나트륨을 낮추면서 포만 신호를 더 빨리 받는 순서예요',
        },
      ],
      'expectedSatietyPct': {'current': 62, 'after': 79},
      'safetyStatus': 'SAFE',
    });
  }

  Future<void> saveSuggestion(String mealId) async {
    await Future.delayed(const Duration(milliseconds: 200));
  }

  Future<MealFeedback> refreshSuggestion(String mealId) async {
    await Future.delayed(const Duration(milliseconds: 200));
    return fetchFeedback(mealId);
  }
}

class NextMealSuggestionScreen extends StatefulWidget {
  final String mealId;

  const NextMealSuggestionScreen({super.key, required this.mealId});

  @override
  State<NextMealSuggestionScreen> createState() =>
      _NextMealSuggestionScreenState();
}

class _NextMealSuggestionScreenState extends State<NextMealSuggestionScreen> {
  final NextMealApiService _api = NextMealApiService();

  MealEvaluation? evaluation;
  MealFeedback? feedback;
  bool isLoading = true;
  bool isRefreshing = false;
  bool isSaving = false;
  String? errorMessage;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      isLoading = true;
      errorMessage = null;
    });
    try {
      final results = await Future.wait([
        _api.fetchEvaluation(widget.mealId),
        _api.fetchFeedback(widget.mealId),
      ]);
      setState(() {
        evaluation = results[0] as MealEvaluation;
        feedback = results[1] as MealFeedback;
      });
    } catch (e) {
      setState(() => errorMessage = '불러오는 데 실패했어요: $e');
    } finally {
      setState(() => isLoading = false);
    }
  }

  Future<void> _onRefreshTap() async {
    setState(() => isRefreshing = true);
    try {
      final result = await _api.refreshSuggestion(widget.mealId);
      setState(() => feedback = result);
    } catch (e) {
      setState(() => errorMessage = '다시 추천받기 실패: $e');
    } finally {
      setState(() => isRefreshing = false);
    }
  }

  Future<void> _onSaveTap() async {
    setState(() => isSaving = true);
    try {
      await _api.saveSuggestion(widget.mealId);
      if (!mounted) return;
      // 저장 완료 → 식사 기록 흐름을 모두 닫고 홈 탭(RootShell 0번)으로 돌아간다.
      // 기록 탭에서 시작했어도 홈으로 간다.
      context.read<TabState>().setIndex(0);
      Navigator.of(context).popUntil((route) => route.isFirst);
    } catch (e) {
      if (!mounted) return;
      setState(() => errorMessage = '저장 실패: $e');
    } finally {
      if (mounted) setState(() => isSaving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: isLoading
            ? const Center(child: CircularProgressIndicator())
            : errorMessage != null && evaluation == null
            ? Center(child: Text(errorMessage!, style: AppTypography.body))
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

              SizedBox(
                height: AppSpacing.xxl,
              ),
              Text('그래서 이렇게 채워보세요', style: AppTypography.sectionHead),
              SizedBox(height: AppSpacing.md),
              ...feedback!.suggestions.map(
                (s) => Padding(
                  padding: EdgeInsets.only(bottom: AppSpacing.cardGap),
                  child: _buildSuggestionCard(s),
                ),
              ),

              SizedBox(height: AppSpacing.md),
              _buildRefreshButton(),
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
          '${_stageLabel(stage)} 1끼 기준',
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

  String _stageLabel(String stage) {
    switch (stage) {
      case 'INITIAL':
        return '초기';
      case 'TITRATION':
        return '증량기';
      case 'MAINTENANCE':
        return '유지기';
      default:
        return stage;
    }
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
    if (n.target == null) {
      return '${n.current.toInt()}${n.unit}';
    }
    final diff = (n.current - n.target!).abs().toInt();
    final diffLabel = switch (n.state) {
      'SHORT' => '$diff${n.unit} 부족',
      'OVER' => '$diff${n.unit} 초과',
      'OK' => '적정',
      _ => '',
    };
    final base = '${n.current.toInt()} / ${n.target!.toInt()}${n.unit}';
    return diffLabel.isEmpty ? base : '$base · $diffLabel';
  }

  Widget _buildNutrientRow(NutrientStatus n) {
    final hasTarget = n.target != null && n.target! > 0;
    final ratio = hasTarget ? (n.current / n.target!).clamp(0.0, 1.0) : 0.3;
    final color = _stateColor(n.state);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            SizedBox(
              width: 56,
              child: Text(
                n.label,
                style: AppTypography
                    .cardTitle,
              ),
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

  Widget _buildRefreshButton() {
    return GestureDetector(
      onTap: isRefreshing ? null : _onRefreshTap,
      child: Container(
        padding: EdgeInsets.symmetric(vertical: AppSpacing.md),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          border: Border.all(color: AppColors.borderStrong),
          borderRadius: BorderRadius.circular(12),
        ),
        child: isRefreshing
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : Text('다른거 추천받기', style: AppTypography.body),
      ),
    );
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
          onPressed: isSaving ? null : _onSaveTap,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
          child: isSaving
              ? const CircularProgressIndicator(color: Colors.white)
              : Text('제안 저장하고 홈으로', style: AppTypography.buttonLabel),
        ),
      ),
    );
  }
}
