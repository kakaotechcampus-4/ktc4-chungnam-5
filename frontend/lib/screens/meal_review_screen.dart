import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_evaluation_screen.dart';
import 'shared_meal_widgets.dart';

/// 음식 확인·수정 — 분석 결과를 사용자가 고치는 화면(5번).
///
/// 고친 내용은 바로 서버에 보낸다. 고친 뒤 서버 상태는 `ANALYZING` +
/// `isRecalculation` 이 되지만, 이건 "확인 화면에서 고치는 중"이라 폴링하지
/// 않는다(`MealDetail` 참고).
///
/// "확인하고 평가받기" 는 고친 걸 다 보내고 평가 화면(6번)으로 넘어간다.
/// 확정(`confirm`)은 식후 포만감이 필요해서 6번이 포만감과 함께 한다.
///
/// 양 스테퍼는 누를 때마다 보내지 않고 잠깐 모았다가 한 번에 `PATCH` 한다.
/// 실패하면 마지막으로 서버가 받은 값으로 되돌린다.
class MealReviewScreen extends StatefulWidget {
  final String mealId;

  const MealReviewScreen({super.key, required this.mealId});

  @override
  State<MealReviewScreen> createState() => _MealReviewScreenState();
}

class _MealReviewScreenState extends State<MealReviewScreen> {
  /// 스테퍼를 마지막으로 누른 뒤 이만큼 조용하면 모아서 보낸다.
  static const _patchDelay = Duration(milliseconds: 700);

  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  MealDetail? _meal;
  List<MealItem> _items = const [];

  /// 서버가 마지막으로 받은 값. 양 수정이 실패하면 이걸로 되돌린다.
  final Map<String, MealItem> _saved = {};

  /// 아직 보내지 않은 양 수정(itemId).
  final Set<String> _pendingAmounts = {};
  Timer? _patchTimer;
  Future<void>? _patching;

  LoadState _state = LoadState.loading;
  String? _errorMessage;
  bool _leaving = false;

  @override
  void initState() {
    super.initState();
    _loadMeal();
  }

  @override
  void dispose() {
    _patchTimer?.cancel();
    super.dispose();
  }

  Future<void> _loadMeal() async {
    setState(() {
      _state = LoadState.loading;
      _errorMessage = null;
    });
    try {
      final meal = await _api.fetchMeal(widget.mealId);
      if (!mounted) return;
      if (meal.isFirstAnalysis || meal.status == 'FAILED') {
        setState(() {
          _errorMessage = meal.status == 'FAILED'
              ? '음식을 알아보지 못한 식사예요. 다시 기록해 주세요.'
              : '아직 분석 중이에요. 잠시 후 다시 확인해 주세요.';
          _state = LoadState.failed;
        });
        return;
      }
      setState(() {
        _meal = meal;
        _items = [...meal.items];
        _saved
          ..clear()
          ..addEntries(meal.items.map((i) => MapEntry(i.itemId, i)));
        _state = LoadState.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '불러오는 데 실패했어요: $e';
        _state = LoadState.failed;
      });
    }
  }

  void _replace(MealItem item) {
    setState(() {
      _items = [for (final i in _items) i.itemId == item.itemId ? item : i];
    });
  }

  void _showError(String message) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  // ── 양 ──

  /// 단위별 한 칸. 무게·부피는 10, 개수는 1, 그 밖(인분·공기)은 0.5.
  static double _stepOf(String? unit) => switch (unit) {
    '개' => 1,
    'g' || 'ml' => 10,
    _ => 0.5,
  };

  void _changeAmount(String itemId, double delta) {
    // 연달아 누르면 그려진 카드보다 값이 앞서 있다. 지금 값에서 센다.
    final item = _items.firstWhere((i) => i.itemId == itemId);
    final amount = item.amount;
    if (amount == null) return;
    final next = amount + delta;
    if (next <= 0) return;
    _replace(item.copyWith(amount: next));
    _pendingAmounts.add(itemId);
    _patchTimer?.cancel();
    // 실패는 _sendAmounts 가 되돌리고 알린다. 여기서는 삼키기만 한다.
    _patchTimer = Timer(
      _patchDelay,
      () => _flushAmounts().then((_) {}, onError: (_) {}),
    );
  }

  /// 모아 둔 양 수정을 보낸다. 확정 전에도 부른다.
  Future<void> _flushAmounts() {
    _patchTimer?.cancel();
    return _patching ??= _sendAmounts().whenComplete(() => _patching = null);
  }

  Future<void> _sendAmounts() async {
    if (_pendingAmounts.isEmpty) return;
    final ids = {..._pendingAmounts};
    _pendingAmounts.clear();
    final changed = _items.where((i) => ids.contains(i.itemId)).toList();
    if (changed.isEmpty) return;
    try {
      await _api.updateItems(widget.mealId, changed);
      for (final i in changed) {
        _saved[i.itemId] = i;
      }
    } catch (e) {
      if (!mounted) return;
      for (final i in changed) {
        final saved = _saved[i.itemId];
        if (saved != null) _replace(i.copyWith(amount: saved.amount));
      }
      _showError('양을 바꾸지 못해 되돌렸어요: $e');
      rethrow;
    }
  }

  // ── 추가·삭제 ──

  Future<void> _deleteItem(MealItem item) async {
    final index = _items.indexOf(item);
    setState(() => _items = [..._items]..remove(item));
    _pendingAmounts.remove(item.itemId);
    try {
      await _api.deleteItem(widget.mealId, item.itemId);
      _saved.remove(item.itemId);
    } catch (e) {
      if (!mounted) return;
      setState(() => _items = [..._items]..insert(index, item));
      _showError('삭제하지 못했어요: $e');
    }
  }

  Future<void> _addItem() async {
    final input = await showModalBottomSheet<_NewItem>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
      builder: (_) => const _AddItemSheet(),
    );
    if (input == null || !mounted) return;
    try {
      await _flushAmounts();
      await _api.addItem(
        widget.mealId,
        displayName: input.name,
        amount: input.amount,
        unit: input.unit,
      );
      if (!mounted) return;
      // 응답에 이름·양이 없어 다시 받는다.
      await _loadMeal();
    } catch (e) {
      if (mounted) _showError('추가하지 못했어요: $e');
    }
  }

  // ── 영양 정보 ──

  /// 공공 DB 후보에서 골라 영양 정보를 정한다.
  Future<void> _pickNutrition(MealItem item) async {
    final candidate = await showModalBottomSheet<FoodCandidate>(
      context: context,
      isScrollControlled: true,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
      builder: (_) =>
          _CandidateSheet(api: _api, initialQuery: item.displayName),
    );
    if (candidate == null || !mounted) return;
    try {
      final (:nutrition, :notice) = await _api.setNutrition(
        widget.mealId,
        item.itemId,
        foodRefId: candidate.foodRefId,
      );
      if (!mounted) return;
      // 양이 g 으로 환산되지 않아 영양 정보를 붙이지 못한 경우. 양을 g 으로
      // 바꾸면 다시 고를 수 있다.
      if (nutrition == null) {
        _showError(notice ?? '이 양으로는 영양 정보를 계산하지 못했어요. 양을 g 으로 바꿔 보세요.');
      }
      final current = _items.firstWhere(
        (i) => i.itemId == item.itemId,
        orElse: () => item,
      );
      final updated = current.copyWith(
        matched: nutrition != null,
        nutrition: nutrition,
      );
      _replace(updated);
      _saved[item.itemId] = updated;
    } catch (e) {
      if (mounted) _showError('영양 정보를 바꾸지 못했어요: $e');
    }
  }

  // ── 다음 ──

  /// 고친 걸 다 보낸 뒤 평가 화면(6번)으로 교체 이동한다. 확정은 그 화면이
  /// 식후 포만감과 함께 한다.
  Future<void> _goToEvaluation() async {
    setState(() => _leaving = true);
    try {
      await _flushAmounts();
    } catch (_) {
      if (mounted) setState(() => _leaving = false);
      return;
    }
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => MealEvaluationScreen(
          mealId: widget.mealId,
          imageUrl: _meal?.imageUrl,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: switch (_state) {
          LoadState.loading => const Center(child: CircularProgressIndicator()),
          LoadState.failed => Column(
            children: [
              _buildHeader(),
              Expanded(
                child: Center(
                  child: RetryBlock(
                    message: _errorMessage ?? '잠시 후 다시 시도해 주세요',
                    onRetry: _loadMeal,
                  ),
                ),
              ),
            ],
          ),
          LoadState.ready => _buildContent(_meal!),
        },
      ),
    );
  }

  Widget _buildContent(MealDetail m) {
    return Column(
      children: [
        _buildHeader(),
        if (m.clarifyQuestion != null) _buildClarifyBanner(m.clarifyQuestion!),
        Expanded(
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.screenHorizontal,
              vertical: AppSpacing.lg,
            ),
            itemCount: _items.length + 1,
            separatorBuilder: (_, _) =>
                const SizedBox(height: AppSpacing.cardGap),
            itemBuilder: (context, index) {
              if (index == _items.length) return _buildAddButton();
              return _buildFoodCard(_items[index]);
            },
          ),
        ),
        _buildConfirmButton(),
      ],
    );
  }

  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.screenHorizontal,
      ),
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
          Text(
            _state == LoadState.ready ? '${_items.length}개' : '',
            style: AppTypography.bodySecondary,
          ),
          const SizedBox(width: 8),
        ],
      ),
    );
  }

  Widget _buildClarifyBanner(String question) {
    return Container(
      margin: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal,
        0,
      ),
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.help_outline, size: 18, color: AppColors.primary),
          const SizedBox(width: AppSpacing.sm),
          Expanded(child: Text(question, style: AppTypography.body)),
        ],
      ),
    );
  }

  Widget _buildFoodCard(MealItem item) {
    final nutrition = item.nutrition;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
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
                    Flexible(
                      child: Text(
                        item.displayName,
                        style: AppTypography.cardTitle,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.sm),
                    GestureDetector(
                      onTap: () => _pickNutrition(item),
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
          const SizedBox(height: AppSpacing.xs),
          if (item.matched && nutrition != null)
            Text(_nutritionLine(nutrition), style: AppTypography.bodySecondary)
          else
            GestureDetector(
              onTap: () => _pickNutrition(item),
              child: Text(
                '영양정보 없음 · 찾아서 고르기',
                style: AppTypography.bodySecondary.copyWith(
                  color: AppColors.primary,
                ),
              ),
            ),
          const SizedBox(height: AppSpacing.md),
          Row(
            children: [
              _buildStepper(item),
              const Spacer(),
              if (item.confidence != null)
                _buildConfidenceBadge(item.confidence!),
            ],
          ),
        ],
      ),
    );
  }

  static String _nutritionLine(MealNutrition n) => [
    if (n.kcal != null) '${n.kcal!.round()}kcal',
    if (n.proteinG != null) '단백질 ${n.proteinG!.round()}g',
    if (n.fiberG != null) '식이섬유 ${n.fiberG!.round()}g',
  ].join(' · ');

  static String _formatAmount(double v) =>
      v == v.roundToDouble() ? '${v.toInt()}' : v.toStringAsFixed(1);

  Widget _buildStepper(MealItem item) {
    final amount = item.amount;
    if (amount == null) {
      return Text('양 모름', style: AppTypography.bodySecondary);
    }
    final step = _stepOf(item.unit);
    return Row(
      children: [
        _stepButton(Icons.remove, () => _changeAmount(item.itemId, -step)),
        const SizedBox(width: AppSpacing.sm),
        Text(
          '${_formatAmount(amount)}${item.unit ?? ''}',
          style: AppTypography.cardTitle,
        ),
        const SizedBox(width: AppSpacing.sm),
        _stepButton(Icons.add, () => _changeAmount(item.itemId, step)),
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
      padding: const EdgeInsets.symmetric(
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
        padding: const EdgeInsets.all(AppSpacing.cardPadding),
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
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.sm,
        AppSpacing.screenHorizontal,
        AppSpacing.lg,
      ),
      child: SizedBox(
        width: double.infinity,
        height: AppLayout.primaryButtonHeight,
        child: ElevatedButton(
          // 음식이 하나도 없으면 평가할 게 없다.
          onPressed: _leaving || _items.isEmpty ? null : _goToEvaluation,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
          child: _leaving
              ? const CircularProgressIndicator(color: Colors.white)
              : Text('확인하고 평가받기', style: AppTypography.buttonLabel),
        ),
      ),
    );
  }
}

/// 음식 추가 입력값.
class _NewItem {
  const _NewItem(this.name, this.amount, this.unit);

  final String name;
  final double amount;
  final String unit;
}

/// 음식 추가 시트 — 이름·양·단위.
class _AddItemSheet extends StatefulWidget {
  const _AddItemSheet();

  @override
  State<_AddItemSheet> createState() => _AddItemSheetState();
}

class _AddItemSheetState extends State<_AddItemSheet> {
  final _name = TextEditingController();
  final _amount = TextEditingController(text: '1');
  final _unit = TextEditingController(text: '인분');

  @override
  void dispose() {
    _name.dispose();
    _amount.dispose();
    _unit.dispose();
    super.dispose();
  }

  _NewItem? get _value {
    final name = _name.text.trim();
    final amount = double.tryParse(_amount.text.trim());
    final unit = _unit.text.trim();
    if (name.isEmpty || amount == null || amount <= 0 || unit.isEmpty) {
      return null;
    }
    return _NewItem(name, amount, unit);
  }

  @override
  Widget build(BuildContext context) {
    final value = _value;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.lg,
        AppSpacing.screenHorizontal,
        AppSpacing.lg + MediaQuery.of(context).viewInsets.bottom,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('음식 추가', style: AppTypography.sectionHead),
          const SizedBox(height: AppSpacing.md),
          TextField(
            controller: _name,
            autofocus: true,
            decoration: const InputDecoration(labelText: '음식 이름'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _amount,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(labelText: '양'),
                  onChanged: (_) => setState(() {}),
                ),
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: TextField(
                  controller: _unit,
                  decoration: const InputDecoration(labelText: '단위 (g·개·인분)'),
                  onChanged: (_) => setState(() {}),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          FilledButton(
            onPressed: value == null
                ? null
                : () => Navigator.of(context).pop(value),
            child: const Text('추가'),
          ),
        ],
      ),
    );
  }
}

/// 영양 정보 후보 검색 시트 — 공공 식품영양성분 DB.
class _CandidateSheet extends StatefulWidget {
  const _CandidateSheet({required this.api, required this.initialQuery});

  final MealApiService api;
  final String initialQuery;

  @override
  State<_CandidateSheet> createState() => _CandidateSheetState();
}

class _CandidateSheetState extends State<_CandidateSheet> {
  late final _query = TextEditingController(text: widget.initialQuery);
  List<FoodCandidate>? _results;
  String? _error;
  bool _searching = false;

  @override
  void initState() {
    super.initState();
    _search();
  }

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final q = _query.text.trim();
    if (q.isEmpty) return;
    setState(() {
      _searching = true;
      _error = null;
    });
    try {
      final results = await widget.api.searchCandidates(q);
      if (mounted) setState(() => _results = results);
    } catch (e) {
      if (mounted) setState(() => _error = '검색하지 못했어요: $e');
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final results = _results;
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.7,
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          AppSpacing.screenHorizontal,
          AppSpacing.lg,
          AppSpacing.screenHorizontal,
          MediaQuery.of(context).viewInsets.bottom,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('영양 정보 고르기', style: AppTypography.sectionHead),
            const SizedBox(height: AppSpacing.xs),
            Text(
              '공공 식품영양성분 DB에서 가장 비슷한 음식을 골라 주세요.',
              style: AppTypography.bodySecondary,
            ),
            const SizedBox(height: AppSpacing.md),
            TextField(
              controller: _query,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => _search(),
              decoration: InputDecoration(
                hintText: '음식 이름',
                suffixIcon: IconButton(
                  icon: const Icon(Icons.search),
                  onPressed: _search,
                ),
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            Expanded(
              child: _searching
                  ? const Center(child: CircularProgressIndicator())
                  : _error != null
                  ? Center(child: Text(_error!, style: AppTypography.body))
                  : results == null || results.isEmpty
                  ? Center(
                      child: Text(
                        '찾은 음식이 없어요. 다른 이름으로 검색해 보세요.',
                        style: AppTypography.bodySecondary,
                      ),
                    )
                  : ListView.separated(
                      itemCount: results.length,
                      separatorBuilder: (_, _) => const Divider(height: 1),
                      itemBuilder: (context, i) {
                        final c = results[i];
                        final serving = c.servingSizeG;
                        final kcal = c.nutrition.kcal;
                        return ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(
                            c.displayName,
                            style: AppTypography.cardTitle,
                          ),
                          subtitle: Text(
                            [
                              if (serving != null) '${serving.round()}g 기준',
                              if (kcal != null) '${kcal.round()}kcal',
                            ].join(' · '),
                            style: AppTypography.bodySecondary,
                          ),
                          onTap: () => Navigator.of(context).pop(c),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}
