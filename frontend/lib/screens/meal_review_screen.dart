import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../popups/popup_gate.dart';
import '../popups/review_leave_popup.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_add_sheet.dart';
import 'meal_evaluation_screen.dart';
import 'meal_item_edit_sheet.dart';
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

  /// 영양정보 없음 안내는 화면에 처음 들어왔을 때 한 번만 띄운다. 고치는 중이나 음식을
  /// 추가한 뒤에는 다시 띄우지 않고, 아래 요약과 평가받기 버튼 문구가 남은 수를 알린다.
  bool _missingNoticeDone = false;

  /// 영양정보가 없는 음식. 카드의 빨간 테두리·"영양정보 없음" 과 같은 기준이다.
  List<MealItem> get _missingItems => [
    for (final i in _items)
      if (!(i.matched && i.nutrition != null)) i,
  ];

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
      _noticeMissingNutrition();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '불러오는 데 실패했어요: $e';
        _state = LoadState.failed;
      });
    }
  }

  /// 처음 불러왔을 때 영양정보 없는 음식이 있으면 팝업으로 알린다. "입력하기" 면 그
  /// 음식의 수정 시트를 바로 연다. 처음 한 번만 — 그 뒤 고치는 중에는 다시 묻지 않는다.
  void _noticeMissingNutrition() {
    if (_missingNoticeDone) return;
    _missingNoticeDone = true;
    final missing = _missingItems;
    if (missing.isEmpty) return;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      final edit = await context.read<PopupGate>().confirmNutritionMissing(
        context,
        mealId: widget.mealId,
        count: missing.length,
      );
      if (edit && mounted) _editItem(missing.first);
    });
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

  void _changeAmount(String itemId, double delta) {
    // 연달아 누르면 그려진 카드보다 값이 앞서 있다. 지금 값에서 센다.
    final item = _items.firstWhere((i) => i.itemId == itemId);
    final amount = item.amount;
    if (amount == null) return;
    _setAmount(itemId, amount + delta);
  }

  /// 숫자 칸에 직접 친 값을 양으로 바꾼다. 0 이하면 무시한다.
  void _setAmount(String itemId, double next) {
    if (next <= 0) return;
    final item = _items.firstWhere((i) => i.itemId == itemId);
    if (item.amount == next) return;
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
    await _refreshNutrition(ids);
  }

  /// 양을 바꾸면 서버가 영양 정보를 그 양으로 다시 계산한다(250g 400kcal →
  /// 500g 800kcal). 화면의 kcal 이 예전 값으로 남지 않게 다시 받아 영양만
  /// 바꾼다. 양은 그사이 사용자가 또 눌렀을 수 있어 화면 값을 둔다.
  Future<void> _refreshNutrition(Set<String> ids) async {
    final MealDetail meal;
    try {
      meal = await _api.fetchMeal(widget.mealId);
    } catch (_) {
      return; // 저장은 됐다. 표시만 늦을 뿐이다.
    }
    if (!mounted) return;
    for (final server in meal.items) {
      if (!ids.contains(server.itemId)) continue;
      final local = _items.where((i) => i.itemId == server.itemId).firstOrNull;
      if (local == null) continue;
      _replace(server.copyWith(amount: local.amount));
      _saved[server.itemId] = server;
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

  /// "＋ 음식 추가" — 시트에서 받은 음식 한 줄을 서버에 보낸다.
  Future<void> _addItem() async {
    final entry = await showMealAddSheet(context);
    if (entry == null || !mounted) return;
    try {
      await _flushAmounts();
    } catch (_) {
      return; // _sendAmounts 가 이미 알렸다.
    }
    try {
      await _api.addItem(
        widget.mealId,
        displayName: entry.name,
        amount: entry.amount,
        unit: entry.unit,
      );
    } catch (e) {
      if (mounted) _showError('추가하지 못했어요: $e');
      return;
    }
    if (!mounted) return;
    // 응답에 이름·양이 없어 다시 받는다.
    await _loadMeal();
  }

  // ── 음식 수정 ──

  /// "음식 수정" 시트로 이름·양·단위·영양정보를 고친다. 저장을 누르면 바뀐
  /// 것만 서버에 보내고 식사를 다시 받아 온다.
  Future<void> _editItem(MealItem item) async {
    final edit = await showMealItemEditSheet(
      context,
      item: item,
      pickCandidate: (query) => showModalBottomSheet<FoodCandidate>(
        context: context,
        isScrollControlled: true,
        backgroundColor: AppColors.surface,
        shape: const RoundedRectangleBorder(
          borderRadius: AppRadius.sheetRadius,
        ),
        builder: (_) => _CandidateSheet(api: _api, initialQuery: query),
      ),
    );
    if (edit == null || !mounted) return;
    try {
      // 남은 양 수정을 먼저 보낸다. 그 뒤 값이 서버 영양 값을 다시 계산하므로
      // 영양정보 지정은 항상 마지막에 보낸다.
      await _flushAmounts();
    } catch (_) {
      return; // _sendAmounts 가 이미 알렸다.
    }
    if (!mounted) return;
    final current = _items.firstWhere(
      (i) => i.itemId == item.itemId,
      orElse: () => item,
    );
    try {
      if (edit.name != current.displayName ||
          edit.amount != current.amount ||
          edit.unit != current.unit) {
        await _api.updateItems(widget.mealId, [
          MealItem(
            itemId: current.itemId,
            displayName: edit.name,
            amount: edit.amount,
            unit: edit.unit,
            confidence: current.confidence,
            matched: current.matched,
            nutrition: current.nutrition,
          ),
        ]);
      }
      final candidate = edit.candidate;
      final manual = edit.manual;
      if (candidate != null || manual != null) {
        final (:nutrition, :notice) = await _api.setNutrition(
          widget.mealId,
          item.itemId,
          foodRefId: candidate?.foodRefId,
          manual: manual,
        );
        // 양이 g 으로 환산되지 않아 영양 정보를 붙이지 못한 경우. 양을 g 으로
        // 바꾸면 다시 고를 수 있다.
        if (nutrition == null && mounted) {
          _showError(notice ?? '이 양으로는 영양 정보를 계산하지 못했어요. 양을 g 으로 바꿔 보세요.');
        }
      }
      if (!mounted) return;
      // 이름·양을 바꾸면 서버가 영양을 다시 계산하므로 통째로 다시 받는다.
      await _loadMeal();
    } catch (e) {
      if (mounted) _showError('음식을 수정하지 못했어요: $e');
    }
  }

  // ── 나가기 ──

  /// 뒤로가기. 확정 전 끼니는 계속·나중에·삭제를 묻는다 — 그냥 나가면 점수 없는
  /// 끼니가 홈·기록에 "기록됨"으로 남기 때문이다. 아직 못 불러왔거나 이미 평가된
  /// 끼니는 지울 일이 없어 바로 나간다.
  Future<void> _requestLeave() async {
    if (_leaving) return;
    final meal = _meal;
    if (_state != LoadState.ready || meal == null || meal.status == 'EVALUATED') {
      Navigator.of(context).pop();
      return;
    }
    final choice = await context.read<PopupGate>().confirmReviewLeave(
      context,
      mealId: widget.mealId,
    );
    if (!mounted) return;
    switch (choice) {
      case ReviewLeaveChoice.discard:
        await _discardMeal();
      case ReviewLeaveChoice.later:
        try {
          await _flushAmounts();
        } catch (_) {
          return; // _sendAmounts 가 이미 알렸다. 못 보낸 채로 나가지 않는다.
        }
        if (mounted) Navigator.of(context).pop();
      case ReviewLeaveChoice.keepEditing || null:
        break;
    }
  }

  /// 기록을 지우고 나간다. 지우지 못하면 화면에 남아 알린다.
  Future<void> _discardMeal() async {
    setState(() => _leaving = true);
    _patchTimer?.cancel();
    try {
      await _api.deleteMeal(widget.mealId);
    } catch (e) {
      if (mounted) {
        setState(() => _leaving = false);
        _showError('삭제하지 못했어요: $e');
      }
      return;
    }
    if (mounted) Navigator.of(context).pop();
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
    return PopScope(
      // 뒤로가기(시스템·화면 버튼 모두)는 _requestLeave 가 받는다.
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _requestLeave();
      },
      child: Scaffold(
        backgroundColor: AppColors.background,
        body: SafeArea(
          child: switch (_state) {
            LoadState.loading => const Center(
              child: CircularProgressIndicator(),
            ),
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
      ),
    );
  }

  Widget _buildContent(MealDetail m) {
    return Column(
      children: [
        _buildHeader(),
        if (m.clarifyQuestion != null) _buildClarifyBanner(m.clarifyQuestion!),
        if (_missingItems.isNotEmpty) _buildMissingBanner(_missingItems.length),
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
      // 상태바·노치 아래에서 한 칸 더 내려 다른 화면 제목과 높이를 맞춘다.
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal,
        0,
      ),
      child: Row(
        children: [
          IconButton(
            icon: const Icon(Icons.chevron_left),
            onPressed: _requestLeave,
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

  /// 영양정보 없는 음식이 남아 있다는 요약. 팝업을 닫은 뒤에도 남은 수를 알린다.
  Widget _buildMissingBanner(int count) {
    return Container(
      margin: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal,
        0,
      ),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      decoration: BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: BorderRadius.circular(13),
      ),
      child: Row(
        children: [
          const Icon(Icons.info_outline, size: 18, color: AppColors.primary),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              '영양정보 없는 음식 $count개 — 입력하거나 삭제해 주세요.',
              style: AppTypography.caption.copyWith(
                fontSize: 11.5,
                fontWeight: FontWeight.w700,
                color: AppColors.primary,
              ),
            ),
          ),
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
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      decoration: BoxDecoration(
        color: AppColors.quantityBg,
        borderRadius: BorderRadius.circular(13),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 20,
            height: 20,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              color: AppColors.quantity,
              shape: BoxShape.circle,
            ),
            child: Text(
              '?',
              style: AppTypography.caption.copyWith(
                fontSize: 11,
                fontWeight: FontWeight.w700,
                color: AppColors.textInverse,
              ),
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  question,
                  style: AppTypography.caption.copyWith(
                    fontSize: 11.5,
                    fontWeight: FontWeight.w700,
                    color: AppColors.quantity,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  'AI가 확신하지 못한 항목이 있어요. 확인하거나 수정해 주세요.',
                  style: AppTypography.caption.copyWith(
                    fontSize: 9.5,
                    color: AppColors.quantity,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFoodCard(MealItem item) {
    final nutrition = item.nutrition;
    final hasNutrition = item.matched && nutrition != null;
    return Container(
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.surface,
        // 영양정보가 없으면 테두리를 빨강으로 눈에 띄게 한다.
        border: Border.all(
          color: hasNutrition ? AppColors.border : AppColors.primary,
          width: hasNutrition ? 1 : 1.5,
        ),
        borderRadius: BorderRadius.circular(14),
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
                      onTap: () => _editItem(item),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: AppColors.surfaceMuted,
                          borderRadius: BorderRadius.circular(9),
                        ),
                        child: Text(
                          '수정',
                          style: AppTypography.caption.copyWith(
                            fontSize: 9,
                            fontWeight: FontWeight.w700,
                            color: AppColors.textSecondary,
                          ),
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
          if (hasNutrition)
            Text(_nutritionLine(nutrition), style: AppTypography.bodySecondary)
          else
            Text(
              '영양정보 없음',
              style: AppTypography.bodySecondary.copyWith(
                color: AppColors.primary,
              ),
            ),
          const SizedBox(height: AppSpacing.md),
          _buildStepper(item),
        ],
      ),
    );
  }

  static String _nutritionLine(MealNutrition n) => [
    if (n.kcal != null) '${n.kcal!.round()}kcal',
    if (n.proteinG != null) '단백질 ${n.proteinG!.round()}g',
    if (n.fiberG != null) '식이섬유 ${n.fiberG!.round()}g',
  ].join(' · ');

  Widget _buildStepper(MealItem item) {
    final amount = item.amount;
    if (amount == null) {
      return Text('양 모름', style: AppTypography.bodySecondary);
    }
    final step = mealAmountStep(item.unit);
    return Row(
      children: [
        _stepButton(Icons.remove, () => _changeAmount(item.itemId, -step)),
        const SizedBox(width: AppSpacing.sm),
        // 숫자는 직접 고칠 수도 있다. 단위는 뒤에 따로 붙인다.
        MealAmountField(
          key: ValueKey('amount-${item.itemId}'),
          width: 48,
          value: amount,
          onChanged: (v) {
            if (v != null) _setAmount(item.itemId, v);
          },
          style: AppTypography.cardTitle,
        ),
        if ((item.unit ?? '').isNotEmpty)
          Text(item.unit!, style: AppTypography.cardTitle),
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

  Widget _buildAddButton() {
    return GestureDetector(
      onTap: _addItem,
      child: CustomPaint(
        painter: const _DashedBorderPainter(
          color: AppColors.borderStrong,
          radius: 13,
        ),
        child: Container(
          height: 46,
          alignment: Alignment.center,
          child: Text(
            '＋ 음식 추가',
            style: AppTypography.body.copyWith(
              fontWeight: FontWeight.w700,
              color: AppColors.textSecondary,
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildConfirmButton() {
    final missing = _missingItems.length;
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
          // 음식이 하나도 없으면 평가할 게 없다. 영양정보가 없는 음식이 남아 있으면 일부만
          // 계산돼 점수가 낮게 나올 수 있어서, 입력하거나 삭제할 때까지 잠근다.
          onPressed: _leaving || _items.isEmpty || missing > 0
              ? null
              : _goToEvaluation,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
            ),
          ),
          child: _leaving
              ? const CircularProgressIndicator(color: Colors.white)
              : Text(
                  missing > 0 ? '영양정보 $missing개 입력 필요' : '확인하고 평가받기',
                  style: AppTypography.buttonLabel,
                ),
        ),
      ),
    );
  }
}

/// 점선 둥근 사각형 테두리(Figma 음식 추가 버튼).
class _DashedBorderPainter extends CustomPainter {
  const _DashedBorderPainter({required this.color, required this.radius});

  final Color color;
  final double radius;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    final rect = (Offset.zero & size).deflate(paint.strokeWidth / 2);
    final path = Path()
      ..addRRect(RRect.fromRectAndRadius(rect, Radius.circular(radius)));
    const dash = 6.0;
    const gap = 4.0;
    for (final metric in path.computeMetrics()) {
      for (var d = 0.0; d < metric.length; d += dash + gap) {
        canvas.drawPath(metric.extractPath(d, d + dash), paint);
      }
    }
  }

  @override
  bool shouldRepaint(_DashedBorderPainter old) =>
      old.color != color || old.radius != radius;
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
              textAlignVertical: TextAlignVertical.center,
              style: AppTypography.body.copyWith(fontSize: 15),
              decoration: InputDecoration(
                hintText: '음식 이름',
                hintStyle: AppTypography.body.copyWith(
                  fontSize: 15,
                  color: AppColors.textTertiary,
                ),
                isDense: true,
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 12,
                ),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: AppColors.border),
                ),
                enabledBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: AppColors.border),
                ),
                focusedBorder: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(8),
                  borderSide: const BorderSide(color: AppColors.borderStrong),
                ),
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
