import 'package:flutter/material.dart';

import '../api/meal_api.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 단위별 한 칸. 무게·부피는 10, 개수는 1, 그 밖(인분·공기)은 0.5.
double mealAmountStep(String? unit) => switch (unit) {
  '개' => 1,
  'g' || 'ml' => 10,
  _ => 0.5,
};

/// "음식 수정" 시트에서 사용자가 고른 값.
///
/// 영양정보는 [candidate](공공 DB 후보) 와 [manual](직접 입력) 중 하나만 오거나,
/// 건드리지 않았으면 둘 다 null 이다.
class MealItemEdit {
  const MealItemEdit({
    required this.name,
    required this.amount,
    required this.unit,
    this.candidate,
    this.manual,
  });

  final String name;
  final double amount;
  final String unit;
  final FoodCandidate? candidate;
  final MealNutrition? manual;
}

/// 음식 한 줄을 고치는 하단 시트(Figma 화면 5 b-2 · b-1).
///
/// 이름·양·단위를 고치고, 영양정보는 "영양 정보 고르기"(검색) 나
/// "직접 입력하기" 중 하나로 정한다. 저장을 눌러야 [MealItemEdit] 로 돌려주고,
/// 취소·바깥 탭이면 null 이다. 서버에는 보내지 않는다 — 호출한 화면이 보낸다.
///
/// [pickCandidate] 는 "영양정보 고르기" 시트를 띄워 후보를 고르게 한다.
Future<MealItemEdit?> showMealItemEditSheet(
  BuildContext context, {
  required MealItem item,
  required Future<FoodCandidate?> Function(String query) pickCandidate,
}) {
  return showModalBottomSheet<MealItemEdit>(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.surface,
    shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
    builder: (_) =>
        _MealItemEditSheet(item: item, pickCandidate: pickCandidate),
  );
}

enum _NutritionMode { pick, manual }

class _MealItemEditSheet extends StatefulWidget {
  const _MealItemEditSheet({required this.item, required this.pickCandidate});

  final MealItem item;
  final Future<FoodCandidate?> Function(String query) pickCandidate;

  @override
  State<_MealItemEditSheet> createState() => _MealItemEditSheetState();
}

class _MealItemEditSheetState extends State<_MealItemEditSheet> {
  static const _baseUnits = ['g', 'ml', '개', '인분'];

  late final _name = TextEditingController(text: widget.item.displayName);
  late final _search = TextEditingController(text: widget.item.displayName);
  late double _amount = widget.item.amount ?? 1;
  late String _unit = widget.item.unit ?? 'g';

  _NutritionMode _mode = _NutritionMode.pick;
  FoodCandidate? _candidate;

  /// 후보를 고르면서 양을 기준량(g)으로 바꿨는지. 안내 문구에 쓴다.
  bool _switchedToGrams = false;

  /// 서버가 영양을 계산할 수 있는 단위(BE `services.meal.to_grams`).
  /// 개·인분은 g 으로 바꿀 근거가 DB 에 없어 계산하지 않는다 — 앱도 g 으로만 계산한다.
  static const _gramUnits = {'g', 'ml'};

  bool get _unitIsGrams => _gramUnits.contains(_unit);

  late final _kcal = _fieldFor(widget.item.nutrition?.kcal);
  late final _protein = _fieldFor(widget.item.nutrition?.proteinG);
  late final _fat = _fieldFor(widget.item.nutrition?.fatG);
  late final _carb = _fieldFor(widget.item.nutrition?.carbG);
  late final _fiber = _fieldFor(widget.item.nutrition?.fiberG);
  late final _sodium = _fieldFor(widget.item.nutrition?.sodiumMg);

  static TextEditingController _fieldFor(double? v) =>
      TextEditingController(text: v == null ? '' : _formatNumber(v));

  static String _formatNumber(double v) =>
      v == v.roundToDouble() ? '${v.toInt()}' : v.toStringAsFixed(1);

  @override
  void dispose() {
    for (final c in [
      _name,
      _search,
      _kcal,
      _protein,
      _fat,
      _carb,
      _fiber,
      _sodium,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  List<String> get _units => [
    ..._baseUnits,
    if (!_baseUnits.contains(_unit)) _unit,
  ];

  void _step(double delta) {
    final next = _amount + delta;
    if (next <= 0) return;
    setState(() => _amount = next);
  }

  Future<void> _openCandidates() async {
    final query = _search.text.trim();
    if (query.isEmpty) return;
    final picked = await widget.pickCandidate(query);
    if (picked == null || !mounted) return;
    setState(() {
      _candidate = picked;
      // 개·인분이면 영양이 비므로 양을 그 음식의 기준량(g)으로 바꾼다.
      // 기준량은 "1인분" 이 아니라 성분값의 기준이라 사용자가 고치도록 안내한다.
      final serving = picked.servingSizeG;
      _switchedToGrams = !_unitIsGrams && serving != null && serving > 0;
      if (_switchedToGrams) {
        _unit = 'g';
        _amount = serving!;
      }
      final n = _scaled(picked);
      _kcal.text = _textOf(n.kcal);
      _protein.text = _textOf(n.proteinG);
      _fat.text = _textOf(n.fatG);
      _carb.text = _textOf(n.carbG);
      _fiber.text = _textOf(n.fiberG);
      _sodium.text = _textOf(n.sodiumMg);
    });
  }

  static String _textOf(double? v) => v == null ? '' : _formatNumber(v);

  /// 후보의 영양은 기준량(보통 100g) 값이다. 양이 g 이면 먹은 양으로 환산해
  /// 서버가 저장할 값과 같게 보여 준다. g 이 아니면 기준량 그대로다.
  bool _isScaled(FoodCandidate c) => _unitIsGrams && (c.servingSizeG ?? 0) > 0;

  MealNutrition _scaled(FoodCandidate c) {
    final n = c.nutrition;
    if (!_isScaled(c)) return n;
    final f = _amount / c.servingSizeG!;
    double? m(double? v) => v == null ? null : v * f;
    return MealNutrition(
      kcal: m(n.kcal),
      proteinG: m(n.proteinG),
      fatG: m(n.fatG),
      carbG: m(n.carbG),
      fiberG: m(n.fiberG),
      sodiumMg: m(n.sodiumMg),
    );
  }

  /// 칸이 비어 있으면 null(모름), 숫자가 아니면 null 로 두고 [_manualValid] 가 막는다.
  static double? _parse(TextEditingController c) =>
      double.tryParse(c.text.trim());

  bool get _manualValid {
    for (final c in [_kcal, _protein, _fat, _carb, _fiber, _sodium]) {
      final text = c.text.trim();
      if (text.isEmpty) continue;
      final v = double.tryParse(text);
      if (v == null || v < 0) return false;
    }
    return true;
  }

  MealNutrition? get _manualValue {
    final n = MealNutrition(
      kcal: _parse(_kcal),
      proteinG: _parse(_protein),
      fatG: _parse(_fat),
      carbG: _parse(_carb),
      fiberG: _parse(_fiber),
      sodiumMg: _parse(_sodium),
    );
    // 서버는 전부 비어 있는 직접 입력을 거절한다 — 안 보낸 것으로 본다.
    final empty =
        n.kcal == null &&
        n.proteinG == null &&
        n.fatG == null &&
        n.carbG == null &&
        n.fiberG == null &&
        n.sodiumMg == null;
    return empty ? null : n;
  }

  bool get _canSave =>
      _name.text.trim().isNotEmpty &&
      _amount > 0 &&
      (_mode == _NutritionMode.pick || _manualValid);

  void _save() {
    Navigator.of(context).pop(
      MealItemEdit(
        name: _name.text.trim(),
        amount: _amount,
        unit: _unit,
        candidate: _mode == _NutritionMode.pick ? _candidate : null,
        manual: _mode == _NutritionMode.manual ? _manualValue : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: media.size.height * 0.92),
        child: Padding(
          padding: EdgeInsets.only(bottom: media.viewInsets.bottom),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              _buildHeader(),
              Flexible(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.symmetric(
                    horizontal: AppSpacing.screenHorizontal,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _buildSummary(),
                      const _Divider(),
                      _buildNameField(),
                      const SizedBox(height: AppSpacing.lg),
                      _buildAmountRow(),
                      const _Divider(),
                      _buildNutritionSetting(),
                      const SizedBox(height: AppSpacing.lg),
                    ],
                  ),
                ),
              ),
              _buildActions(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        18,
        AppSpacing.screenHorizontal,
        AppSpacing.md,
      ),
      child: Row(
        children: [
          const SizedBox(width: 24),
          Expanded(
            child: Text(
              '음식 수정',
              textAlign: TextAlign.center,
              style: AppTypography.sectionHead.copyWith(fontSize: 16),
            ),
          ),
          GestureDetector(
            onTap: () => Navigator.of(context).pop(),
            child: const Icon(
              Icons.close,
              size: 24,
              color: AppColors.textSecondary,
              semanticLabel: '닫기',
            ),
          ),
        ],
      ),
    );
  }

  /// 지금 음식 이름과 영양 한 줄(바꾸기 전 값).
  Widget _buildSummary() {
    final n = widget.item.nutrition;
    final line = widget.item.matched && n != null
        ? [
            if (n.kcal != null) '${n.kcal!.round()}kcal',
            if (n.proteinG != null) '단백질 ${n.proteinG!.round()}g',
            if (n.fiberG != null) '식이섬유 ${n.fiberG!.round()}g',
          ].join(' · ')
        : null;
    return SizedBox(
      height: 56,
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.item.displayName,
            style: AppTypography.sectionHead.copyWith(fontSize: 16),
          ),
          const SizedBox(height: 3),
          Text(
            line ?? '영양정보 없음',
            style: AppTypography.caption.copyWith(
              fontSize: 9.5,
              color: line == null ? AppColors.primary : AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _label(String text) =>
      Text(text, style: AppTypography.sectionHead.copyWith(fontSize: 14));

  Widget _buildNameField() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _label('음식이름'),
        const SizedBox(height: 8),
        _InputBox(
          child: TextField(
            controller: _name,
            onChanged: (_) => setState(() {}),
            textAlignVertical: TextAlignVertical.center,
            style: AppTypography.body.copyWith(fontSize: 12.5),
            decoration: InputDecoration(
              border: InputBorder.none,
              isDense: true,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 12,
                vertical: 11,
              ),
              suffixIcon: GestureDetector(
                onTap: () => setState(_name.clear),
                child: const Icon(
                  Icons.close,
                  size: 18,
                  color: AppColors.textSecondary,
                  semanticLabel: '이름 지우기',
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildAmountRow() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _label('수량'),
        const SizedBox(height: 8),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Container(
              decoration: BoxDecoration(
                border: Border.all(color: AppColors.border),
                borderRadius: BorderRadius.circular(100),
              ),
              child: Row(
                children: [
                  _roundButton(
                    Icons.remove,
                    () => _step(-mealAmountStep(_unit)),
                  ),
                  SizedBox(
                    width: 76,
                    child: Text(
                      _formatNumber(_amount),
                      textAlign: TextAlign.center,
                      style: AppTypography.sectionHead.copyWith(fontSize: 18),
                    ),
                  ),
                  _roundButton(Icons.add, () => _step(mealAmountStep(_unit))),
                ],
              ),
            ),
            _buildUnitDropdown(),
          ],
        ),
        const SizedBox(height: AppSpacing.md),
      ],
    );
  }

  Widget _roundButton(IconData icon, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 47,
        height: 46,
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          border: Border.all(color: AppColors.border),
          shape: BoxShape.circle,
        ),
        child: Icon(icon, size: 20, color: AppColors.textSecondary),
      ),
    );
  }

  Widget _buildUnitDropdown() {
    return Container(
      width: 83,
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.borderStrong),
        borderRadius: BorderRadius.circular(5),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: _unit,
          isExpanded: true,
          icon: const Icon(
            Icons.keyboard_arrow_down,
            size: 20,
            color: AppColors.textPrimary,
          ),
          style: AppTypography.body.copyWith(
            fontSize: 12.5,
            fontWeight: FontWeight.w700,
            color: AppColors.textBody,
          ),
          items: [
            for (final u in _units) DropdownMenuItem(value: u, child: Text(u)),
          ],
          onChanged: (u) {
            if (u != null) setState(() => _unit = u);
          },
        ),
      ),
    );
  }

  Widget _buildNutritionSetting() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _label('영양정보 설정'),
        const SizedBox(height: 8),
        Text(
          '아래에서 해당하는 음식을 검색하거나,\n직접 입력해주세요.',
          style: AppTypography.caption.copyWith(
            fontSize: 10.5,
            color: AppColors.textTertiary,
          ),
        ),
        const SizedBox(height: 8),
        Row(
          children: [
            Expanded(child: _modeButton('영양 정보 고르기', _NutritionMode.pick)),
            const SizedBox(width: 4),
            Expanded(child: _modeButton('직접 입력하기', _NutritionMode.manual)),
          ],
        ),
        const SizedBox(height: AppSpacing.md),
        if (_mode == _NutritionMode.pick)
          _buildPickBody()
        else
          _buildManualBody(),
      ],
    );
  }

  Widget _modeButton(String text, _NutritionMode mode) {
    final selected = _mode == mode;
    return GestureDetector(
      onTap: () => setState(() => _mode = mode),
      child: Container(
        height: 40,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: selected ? AppColors.primaryStrongBg : AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Text(
          text,
          style: AppTypography.body.copyWith(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            color: selected ? AppColors.primaryStrong : AppColors.textSecondary,
          ),
        ),
      ),
    );
  }

  /// 영양 정보 고르기: 검색창 + 돋보기. 돋보기(또는 검색 키)를 누르면
  /// 공공 DB 후보 시트가 뜨고, 고른 음식이 아래에 보인다.
  Widget _buildPickBody() {
    final c = _candidate;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _InputBox(
          child: TextField(
            controller: _search,
            textInputAction: TextInputAction.search,
            onSubmitted: (_) => _openCandidates(),
            textAlignVertical: TextAlignVertical.center,
            style: AppTypography.body.copyWith(fontSize: 12.5),
            decoration: InputDecoration(
              hintText: '음식 이름으로 검색',
              hintStyle: AppTypography.body.copyWith(
                fontSize: 12.5,
                color: AppColors.textTertiary,
              ),
              border: InputBorder.none,
              isDense: true,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 12,
                vertical: 11,
              ),
              suffixIcon: IconButton(
                icon: const Icon(
                  Icons.search,
                  size: 20,
                  color: AppColors.textSecondary,
                ),
                tooltip: '검색',
                onPressed: _openCandidates,
              ),
            ),
          ),
        ),
        if (c != null) ...[
          const SizedBox(height: AppSpacing.md),
          _buildPickedCard(c),
        ],
      ],
    );
  }

  /// 고른 음식의 영양 6가지(읽기만)를 회색 카드 하나에 담는다. 오른쪽 위 ✕ 는
  /// 선택 취소.
  Widget _buildPickedCard(FoodCandidate c) {
    final n = _scaled(c);
    Widget row(String label, double? v, String unit) => _rowShell(
      label,
      unit,
      Container(
        height: 28,
        alignment: Alignment.centerRight,
        child: Text(
          v == null ? '-' : _formatNumber(v),
          style: AppTypography.body.copyWith(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            color: AppColors.textBody,
          ),
        ),
      ),
    );
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 6),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Stack(
        children: [
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                // ✕ 와 겹치지 않게 오른쪽을 비운다.
                padding: const EdgeInsets.only(right: 28),
                child: _label('영양정보'),
              ),
              const SizedBox(height: 2),
              Padding(
                padding: const EdgeInsets.only(right: 28),
                child: Text(
                  c.displayName,
                  style: AppTypography.caption.copyWith(
                    fontSize: 9.5,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
              ?_gramsNotice(c),
              const SizedBox(height: AppSpacing.md),
              row('칼로리 (kcal)', n.kcal, 'kcal'),
              row('단백질 (g)', n.proteinG, 'g'),
              row('지방 (g)', n.fatG, 'g'),
              row('탄수화물 (g)', n.carbG, 'g'),
              row('식이섬유 (g)', n.fiberG, 'g'),
              row('나트륨 (mg)', n.sodiumMg, 'mg'),
            ],
          ),
          Positioned(
            top: 0,
            right: 0,
            child: GestureDetector(
              onTap: () => setState(() => _candidate = null),
              child: const Icon(
                Icons.close,
                size: 18,
                color: AppColors.textSecondary,
                semanticLabel: '선택 취소',
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 영양을 g 으로만 계산한다는 안내. 단위를 개·인분으로 되돌렸으면 경고한다.
  Widget? _gramsNotice(FoodCandidate c) {
    final String text;
    if (!_unitIsGrams) {
      text = '영양 정보는 g 으로만 계산해요. 양을 g 으로 바꿔 주세요.';
    } else if (_switchedToGrams) {
      text = '양을 기준량 ${_formatNumber(c.servingSizeG!)}g 으로 바꿨어요. 먹은 만큼 고쳐 주세요.';
    } else {
      return null;
    }
    return Padding(
      padding: const EdgeInsets.only(top: AppSpacing.xs, right: 28),
      child: Text(
        text,
        style: AppTypography.caption.copyWith(
          fontSize: 10,
          color: _unitIsGrams ? AppColors.textSecondary : AppColors.primary,
        ),
      ),
    );
  }

  /// 직접 입력하기: 영양 6가지를 먹은 양 기준 총량으로 적는다.
  Widget _buildManualBody() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _label('영양정보'),
        const SizedBox(height: 4),
        Text(
          '먹은 양 전체의 값을 적어 주세요. 모르는 칸은 비워 둬도 돼요.',
          style: AppTypography.caption.copyWith(
            fontSize: 9.5,
            color: AppColors.textTertiary,
          ),
        ),
        const SizedBox(height: AppSpacing.md),
        _manualRow('칼로리 (kcal)', _kcal, 'kcal'),
        _manualRow('단백질 (g)', _protein, 'g'),
        _manualRow('지방 (g)', _fat, 'g'),
        _manualRow('탄수화물 (g)', _carb, 'g'),
        _manualRow('식이섬유 (g)', _fiber, 'g'),
        _manualRow('나트륨 (mg)', _sodium, 'mg'),
      ],
    );
  }

  /// 라벨(좌) · 값 상자 · 단위(우) 한 줄. 직접 입력과 고른 음식이 함께 쓴다.
  Widget _rowShell(String label, String unit, Widget box) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(left: 12),
              child: Text(
                label,
                style: AppTypography.body.copyWith(
                  fontSize: 13,
                  color: AppColors.textBody,
                ),
              ),
            ),
          ),
          SizedBox(width: 92, child: box),
          SizedBox(
            width: 40,
            child: Padding(
              padding: const EdgeInsets.only(left: 8),
              child: Text(
                unit,
                style: AppTypography.body.copyWith(
                  fontSize: 13,
                  color: AppColors.textBody,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _manualRow(String label, TextEditingController c, String unit) {
    final text = c.text.trim();
    final invalid =
        text.isNotEmpty &&
        (double.tryParse(text) == null || double.parse(text) < 0);
    return _rowShell(
      label,
      unit,
      Container(
        height: 28,
        decoration: BoxDecoration(
          color: AppColors.surface,
          border: Border.all(
            color: invalid ? AppColors.primary : AppColors.border,
          ),
          borderRadius: BorderRadius.circular(5),
        ),
        child: TextField(
          controller: c,
          onChanged: (_) => setState(() {}),
          textAlign: TextAlign.end,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          style: AppTypography.body.copyWith(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            color: AppColors.textBody,
          ),
          decoration: const InputDecoration(
            border: InputBorder.none,
            isDense: true,
            contentPadding: EdgeInsets.symmetric(horizontal: 8, vertical: 6),
          ),
        ),
      ),
    );
  }

  Widget _buildActions() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal,
        AppSpacing.lg,
      ),
      child: Row(
        children: [
          Expanded(
            child: GestureDetector(
              onTap: () => Navigator.of(context).pop(),
              child: Container(
                height: 48,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: AppColors.surface,
                  border: Border.all(color: AppColors.borderStrong),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Text(
                  '취소',
                  style: AppTypography.body.copyWith(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w700,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: GestureDetector(
              onTap: _canSave ? _save : null,
              child: Container(
                height: 48,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: _canSave ? AppColors.primary : AppColors.inactive,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Text(
                  '저장',
                  style: AppTypography.buttonLabel.copyWith(fontSize: 13.5),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(vertical: 14),
    child: Divider(height: 1, thickness: 1, color: AppColors.border),
  );
}

/// 흰 바탕 + 연한 테두리 입력 상자(Figma 38 높이, 모서리 5).
class _InputBox extends StatelessWidget {
  const _InputBox({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) => Container(
    constraints: const BoxConstraints(minHeight: 38),
    alignment: Alignment.centerLeft,
    decoration: BoxDecoration(
      color: AppColors.surface,
      border: Border.all(color: AppColors.border),
      borderRadius: BorderRadius.circular(5),
    ),
    child: child,
  );
}
