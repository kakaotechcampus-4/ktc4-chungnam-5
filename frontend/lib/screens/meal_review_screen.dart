import 'dart:async';
import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_evaluation_screen.dart';

class Nutrition {
  final int kcal;
  final double proteinG;
  final double fatG;
  final double carbG;
  final double fiberG;
  final double sodiumMg;

  Nutrition({
    required this.kcal,
    required this.proteinG,
    required this.fatG,
    required this.carbG,
    required this.fiberG,
    required this.sodiumMg,
  });

  factory Nutrition.fromJson(Map<String, dynamic> json) {
    return Nutrition(
      kcal: json['kcal'] as int,
      proteinG: (json['proteinG'] as num).toDouble(),
      fatG: (json['fatG'] as num).toDouble(),
      carbG: (json['carbG'] as num).toDouble(),
      fiberG: (json['fiberG'] as num).toDouble(),
      sodiumMg: (json['sodiumMg'] as num).toDouble(),
    );
  }
}

class MealItem {
  final String itemId;
  String displayName;
  double amount;
  final String unit;
  final double confidence;
  final bool matched;
  final String? nutritionSource;
  bool userConfirmed;
  Nutrition? nutrition;

  MealItem({
    required this.itemId,
    required this.displayName,
    required this.amount,
    required this.unit,
    required this.confidence,
    required this.matched,
    this.nutritionSource,
    this.userConfirmed = false,
    this.nutrition,
  });

  factory MealItem.fromJson(Map<String, dynamic> json) {
    return MealItem(
      itemId: json['itemId'] as String,
      displayName: json['displayName'] as String,
      amount: (json['amount'] as num).toDouble(),
      unit: json['unit'] as String,
      confidence: (json['confidence'] as num).toDouble(),
      matched: json['matched'] as bool,
      nutritionSource: json['nutritionSource'] as String?,
      userConfirmed: json['userConfirmed'] as bool? ?? false,
      nutrition: json['nutrition'] == null
          ? null
          : Nutrition.fromJson(json['nutrition'] as Map<String, dynamic>),
    );
  }
}

class MealDetail {
  final String mealId;
  final String status;
  final String? clarifyQuestion;
  final List<MealItem> items;

  MealDetail({
    required this.mealId,
    required this.status,
    required this.items,
    this.clarifyQuestion,
  });

  factory MealDetail.fromJson(Map<String, dynamic> json) {
    return MealDetail(
      mealId: json['mealId'] as String,
      status: json['status'] as String,
      clarifyQuestion: json['clarifyQuestion'] as String?,
      items: (json['items'] as List<dynamic>)
          .map((e) => MealItem.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}

class MealApiService {
  Future<MealDetail> fetchMeal(String mealId) async {
    await Future.delayed(const Duration(milliseconds: 300));
    return MealDetail.fromJson({
      'mealId': mealId,
      'status': 'REVIEW_REQUIRED',
      'clarifyQuestion': '김밥 속재료가 참치가 맞나요?',
      'items': [
        {
          'itemId': 'item_1',
          'displayName': '참치김밥',
          'amount': 250,
          'unit': 'g',
          'confidence': 0.62,
          'matched': true,
          'nutritionSource': 'PUBLIC_DB',
          'userConfirmed': false,
          'nutrition': {
            'kcal': 400,
            'proteinG': 12,
            'fatG': 10,
            'carbG': 65,
            'fiberG': 4,
            'sodiumMg': 780,
          },
        },
        {
          'itemId': 'item_2',
          'displayName': '삶은 계란',
          'amount': 2,
          'unit': '개',
          'confidence': 0.96,
          'matched': false,
          'nutritionSource': null,
          'userConfirmed': false,
          'nutrition': null,
        },
        {
          'itemId': 'item_3',
          'displayName': '미역국',
          'amount': 200,
          'unit': 'g',
          'confidence': 0.91,
          'matched': true,
          'nutritionSource': 'PUBLIC_DB',
          'userConfirmed': false,
          'nutrition': {
            'kcal': 45,
            'proteinG': 3,
            'fatG': 1,
            'carbG': 3,
            'fiberG': 1,
            'sodiumMg': 620,
          },
        },
      ],
    });
  }

  Future<bool> updateItem(String mealId, MealItem item) async {
    await Future.delayed(const Duration(milliseconds: 200));
    return true;
  }

  Future<bool> addItem(
    String mealId,
    String displayName,
    double amount,
    String unit,
  ) async {
    await Future.delayed(const Duration(milliseconds: 200));
    return true;
  }

  Future<bool> deleteItem(String mealId, String itemId) async {
    await Future.delayed(const Duration(milliseconds: 200));
    return true;
  }

  Future<Map<String, dynamic>> confirmMeal(
    String mealId, {
    int? satietyAfterPct,
  }) async {
    await Future.delayed(const Duration(milliseconds: 300));
    return {
      'status': 'EVALUATED',
      'scores': {'quantity': 75, 'quality': 83, 'satiety': 68},
    };
  }
}

class MealReviewScreen extends StatefulWidget {
  final String mealId;

  const MealReviewScreen({super.key, required this.mealId});

  @override
  State<MealReviewScreen> createState() => _MealReviewScreenState();
}

class _MealReviewScreenState extends State<MealReviewScreen> {
  final MealApiService _api = MealApiService();

  MealDetail? meal;
  bool isLoading = true;
  bool isConfirming = false;
  String? errorMessage;

  @override
  void initState() {
    super.initState();
    _loadMeal();
  }

  Future<void> _loadMeal() async {
    setState(() {
      isLoading = true;
      errorMessage = null;
    });
    try {
      final result = await _api.fetchMeal(widget.mealId);
      setState(() => meal = result);
    } catch (e) {
      setState(() => errorMessage = '불러오는 데 실패했어요: $e');
    } finally {
      setState(() => isLoading = false);
    }
  }

  Future<void> _changeAmount(MealItem item, double delta) async {
    final newAmount = item.amount + delta;
    if (newAmount <= 0) return;
    setState(() => item.amount = newAmount);
    try {
      await _api.updateItem(widget.mealId, item);
    } catch (e) {
      setState(() => errorMessage = '수정 실패: $e');
    }
  }

  Future<void> _deleteItem(MealItem item) async {
    setState(() => meal!.items.remove(item));
    try {
      await _api.deleteItem(widget.mealId, item.itemId);
    } catch (e) {
      setState(() => errorMessage = '삭제 실패: $e');
    }
  }

  void _addItem() {
  }

  void _editItem(MealItem item) {
  }

  Future<void> _confirmAndEvaluate() async {
    setState(() => isConfirming = true);
    try {
      await _api.confirmMeal(widget.mealId);
      if (!mounted) return;
      // 확정 완료 → 식사 평가 화면(6번)으로 교체 이동.
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => MealEvaluationScreen(mealId: widget.mealId),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => errorMessage = '확정 실패: $e');
    } finally {
      if (mounted) setState(() => isConfirming = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: isLoading && meal == null
            ? const Center(child: CircularProgressIndicator())
            : errorMessage != null && meal == null
            ? Center(child: Text(errorMessage!, style: AppTypography.body))
            : _buildContent(),
      ),
    );
  }

  Widget _buildContent() {
    final m = meal!;
    return Column(
      children: [
        _buildHeader(m),
        if (m.clarifyQuestion != null) _buildClarifyBanner(m.clarifyQuestion!),
        Expanded(
          child: ListView.separated(
            padding: EdgeInsets.symmetric(
              horizontal: AppSpacing.screenHorizontal,
              vertical: AppSpacing.lg,
            ),
            itemCount: m.items.length + 1,
            separatorBuilder: (_, __) => SizedBox(height: AppSpacing.cardGap),
            itemBuilder: (context, index) {
              if (index == m.items.length) return _buildAddButton();
              return _buildFoodCard(m.items[index]);
            },
          ),
        ),
        _buildConfirmButton(),
      ],
    );
  }

  Widget _buildHeader(MealDetail m) {
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.screenHorizontal),
      child: Row(
        children: [
          IconButton(
            icon: const Icon(Icons.chevron_left),
            onPressed: () => Navigator.pop(context),
          ),
          Expanded(
            child: Text(
              '음식 확인',
              textAlign: TextAlign.center,
              style: AppTypography.screenTitle,
            ),
          ),
          Text('${m.items.length}개', style: AppTypography.bodySecondary),
          const SizedBox(width: 8),
        ],
      ),
    );
  }

  Widget _buildClarifyBanner(String question) {
    return Container(
      margin: EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal,
        0,
      ),
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.help_outline, size: 18, color: AppColors.primary),
          SizedBox(width: AppSpacing.sm),
          Expanded(child: Text(question, style: AppTypography.body)),
        ],
      ),
    );
  }

  Widget _buildFoodCard(MealItem item) {
    return Container(
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Row(
                  children: [
                    Text(item.displayName, style: AppTypography.cardTitle),
                    SizedBox(width: AppSpacing.sm),
                    GestureDetector(
                      onTap: () => _editItem(item),
                      child: Text(
                        '수정',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.primary,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              GestureDetector(
                onTap: () => _deleteItem(item),
                child: const Icon(
                  Icons.close,
                  size: 18,
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
          SizedBox(height: AppSpacing.xs),
          if (item.matched && item.nutrition != null)
            Text(
              '${item.nutrition!.kcal}kcal · 단백질 ${item.nutrition!.proteinG.toInt()}g · 식이섬유 ${item.nutrition!.fiberG.toInt()}g',
              style: AppTypography.bodySecondary,
            )
          else
            Text(
              '영양정보 없음 · 직접 입력',
              style: AppTypography.bodySecondary.copyWith(
                color: AppColors.primary,
              ),
            ),
          SizedBox(height: AppSpacing.md),
          Row(
            children: [
              _buildStepper(item),
              const Spacer(),
              _buildConfidenceBadge(item.confidence),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStepper(MealItem item) {
    final step = item.unit == '개' ? 1.0 : 10.0;
    return Row(
      children: [
        _stepButton(Icons.remove, () => _changeAmount(item, -step)),
        SizedBox(width: AppSpacing.sm),
        Text(
          '${item.amount.toInt()}${item.unit}',
          style: AppTypography.cardTitle,
        ),
        SizedBox(width: AppSpacing.sm),
        _stepButton(Icons.add, () => _changeAmount(item, step)),
      ],
    );
  }

  Widget _stepButton(IconData icon, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 28,
        height: 28,
        decoration: BoxDecoration(
          border: Border.all(color: AppColors.borderStrong),
          shape: BoxShape.circle,
        ),
        child: Icon(icon, size: 16, color: AppColors.textPrimary),
      ),
    );
  }

  Widget _buildConfidenceBadge(double confidence) {
    final percent = (confidence * 100).round();
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: AppSpacing.xs,
      ),
      decoration: BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        '신뢰도 $percent%',
        style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
      ),
    );
  }

  Widget _buildAddButton() {
    return GestureDetector(
      onTap: _addItem,
      child: Container(
        padding: EdgeInsets.all(AppSpacing.cardPadding),
        decoration: BoxDecoration(
          border: Border.all(color: AppColors.borderStrong),
          borderRadius: BorderRadius.circular(12),
        ),
        alignment: Alignment.center,
        child: Text('+ 음식 추가', style: AppTypography.body),
      ),
    );
  }

  Widget _buildConfirmButton() {
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
          onPressed: isConfirming ? null : _confirmAndEvaluate,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
          child: isConfirming
              ? const CircularProgressIndicator(color: Colors.white)
              : Text('확인하고 평가받기', style: AppTypography.buttonLabel),
        ),
      ),
    );
  }
}
