import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 음식 이름 · 양 · 단위를 적어 목록에 쌓는 입력 틀(Figma "음식 추가" 패널 + "추가한 음식").
///
/// 서버로 보내기 전까지는 화면 안에서만 들고 있는 값이라 영양 정보는 다루지 않는다.
/// 영양 정보는 서버가 이름으로 찾아 붙이고, 못 찾으면 음식 확인 화면에서 고친다.

/// 목록에 쌓은 음식 한 줄.
class FoodEntry {
  const FoodEntry({
    required this.name,
    required this.amount,
    required this.unit,
  });

  final String name;
  final double amount;
  final String unit;
}

/// 고를 수 있는 단위.
const List<String> foodEntryUnits = ['g', '개', '인분'];

/// 양을 화면에 적을 때 정수면 소수점을 뗀다(`1.0` → `1`).
String formatFoodAmount(double v) =>
    v == v.roundToDouble() ? '${v.toInt()}' : v.toString();

/// 입력 폼의 값과 쌓인 목록을 들고 있다. 화면(시트)이 만들고 없앤다.
class FoodEntryController extends ChangeNotifier {
  FoodEntryController() {
    name.addListener(_clearError);
    amount.addListener(_clearError);
  }

  final TextEditingController name = TextEditingController();
  final TextEditingController amount = TextEditingController();
  final FocusNode nameFocus = FocusNode();

  String? _unit;
  final List<FoodEntry> _entries = [];
  String? _error;

  /// 고른 단위. 아직 안 골랐으면 null.
  String? get unit => _unit;

  List<FoodEntry> get entries => List.unmodifiable(_entries);

  /// 마지막으로 막힌 이유. 입력을 고치면 사라진다.
  String? get error => _error;

  /// 이름·양 칸이 둘 다 비어 있는지(단위는 보지 않는다).
  bool get formIsEmpty =>
      name.text.trim().isEmpty && amount.text.trim().isEmpty;

  void setUnit(String? value) {
    _unit = value;
    _error = null;
    notifyListeners();
  }

  void _clearError() {
    if (_error == null) return;
    _error = null;
    notifyListeners();
  }

  void _fail(String message) {
    _error = message;
    notifyListeners();
  }

  /// "목록에 추가" — 폼이 올바르면 목록에 쌓고 이름·양 칸을 비운다.
  /// 단위는 이어서 같은 단위로 적는 일이 많아 그대로 둔다.
  bool addFromForm() {
    final text = name.text.trim();
    final value = double.tryParse(amount.text.trim());
    final unit = _unit;
    if (text.isEmpty) {
      _fail('음식 이름을 적어 주세요');
      return false;
    }
    if (value == null || value <= 0) {
      _fail('양을 0보다 큰 숫자로 적어 주세요');
      return false;
    }
    if (unit == null) {
      _fail('단위를 골라 주세요');
      return false;
    }
    _entries.add(FoodEntry(name: text, amount: value, unit: unit));
    name.clear();
    amount.clear();
    _error = null;
    nameFocus.requestFocus();
    notifyListeners();
    return true;
  }

  /// "추가하기" — 쌓은 목록에, 폼에 적어 둔 값이 있으면 그것까지 더해 돌려준다.
  /// 폼이 덜 채워졌거나 보낼 음식이 하나도 없으면 [error] 를 채우고 null.
  List<FoodEntry>? collect() {
    if (!formIsEmpty && !addFromForm()) return null;
    if (_entries.isEmpty) {
      _fail('추가할 음식을 입력해 주세요');
      return null;
    }
    return entries;
  }

  void removeAt(int index) {
    _entries.removeAt(index);
    notifyListeners();
  }

  void clear() {
    _entries.clear();
    notifyListeners();
  }

  @override
  void dispose() {
    name.dispose();
    amount.dispose();
    nameFocus.dispose();
    super.dispose();
  }
}

final TextStyle _fieldText = AppTypography.body.copyWith(
  fontSize: 12.5,
  height: 1.25,
  color: AppColors.textPrimary,
);

/// 입력 패널 + (쌓인 게 있으면) "추가한 음식" 목록.
class FoodEntryEditor extends StatelessWidget {
  const FoodEntryEditor({super.key, required this.controller});

  final FoodEntryController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final entries = controller.entries;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _AddPanel(controller: controller),
            if (entries.isNotEmpty) ...[
              const SizedBox(height: 7),
              _EntryList(controller: controller, entries: entries),
            ],
          ],
        );
      },
    );
  }
}

class _AddPanel extends StatelessWidget {
  const _AddPanel({required this.controller});

  final FoodEntryController controller;

  @override
  Widget build(BuildContext context) {
    final error = controller.error;
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.primaryTint.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ClipOval(
                child: Image.asset(
                  'assets/images/meal_input_food_add.png',
                  width: 39,
                  height: 39,
                  fit: BoxFit.cover,
                ),
              ),
              const SizedBox(width: 10),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '음식 추가',
                    style: AppTypography.cardTitle.copyWith(fontSize: 14),
                  ),
                  const SizedBox(height: 5),
                  Text(
                    '먹은 음식을 추가해 주세요.',
                    style: AppTypography.caption.copyWith(
                      fontSize: 10.5,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 15),
          const _FieldLabel('음식 이름'),
          const SizedBox(height: 5),
          _ClearableField(
            controller: controller.name,
            focusNode: controller.nameFocus,
            hint: '예) 현미밥, 닭가슴살, 김치',
            height: 35,
            textInputAction: TextInputAction.next,
          ),
          const SizedBox(height: 15),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const _FieldLabel('양'),
                    const SizedBox(height: 5),
                    _ClearableField(
                      controller: controller.amount,
                      height: 40,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      inputFormatters: [
                        FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 25),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const _FieldLabel('단위 (g · 개 · 인분)'),
                    const SizedBox(height: 5),
                    _UnitDropdown(
                      value: controller.unit,
                      onChanged: controller.setUnit,
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 15),
          _AddToListButton(onTap: controller.addFromForm),
          if (error != null) ...[
            const SizedBox(height: AppSpacing.sm),
            Text(
              error,
              style: AppTypography.caption.copyWith(
                fontSize: 11,
                color: AppColors.primary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _EntryList extends StatelessWidget {
  const _EntryList({required this.controller, required this.entries});

  final FoodEntryController controller;
  final List<FoodEntry> entries;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 30,
          child: Row(
            children: [
              Text('추가한 음식', style: AppTypography.cardTitle),
              const SizedBox(width: 6),
              _CountBadge(count: entries.length),
              const Spacer(),
              _ClearAllButton(onTap: controller.clear),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.lg),
        for (var i = 0; i < entries.length; i++) ...[
          if (i > 0) const SizedBox(height: 6),
          _EntryCard(
            entry: entries[i],
            onRemove: () => controller.removeAt(i),
          ),
        ],
      ],
    );
  }
}

/// 입력창 테두리 상자(흰 바탕 + 회색 테두리).
BoxDecoration _fieldBox() => BoxDecoration(
  color: AppColors.surface,
  border: Border.all(color: AppColors.border),
  borderRadius: BorderRadius.circular(10),
);

/// 입력창 위의 라벨("음식 이름" · "양" · "단위").
class _FieldLabel extends StatelessWidget {
  const _FieldLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppTypography.body.copyWith(
        fontSize: 13,
        height: 1.25,
        color: AppColors.textPrimary,
      ),
    );
  }
}

/// 글자가 있으면 오른쪽에 지우기(✕) 버튼이 뜨는 입력창.
class _ClearableField extends StatelessWidget {
  const _ClearableField({
    required this.controller,
    required this.height,
    this.focusNode,
    this.hint,
    this.keyboardType,
    this.inputFormatters,
    this.textInputAction,
  });

  final TextEditingController controller;
  final double height;
  final FocusNode? focusNode;
  final String? hint;
  final TextInputType? keyboardType;
  final List<TextInputFormatter>? inputFormatters;
  final TextInputAction? textInputAction;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      padding: const EdgeInsets.only(left: 7, right: 11),
      decoration: _fieldBox(),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              keyboardType: keyboardType,
              inputFormatters: inputFormatters,
              textInputAction: textInputAction,
              style: _fieldText,
              decoration: InputDecoration(
                hintText: hint,
                hintStyle: _fieldText.copyWith(color: AppColors.textTertiary),
                filled: false,
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                isDense: true,
                contentPadding: EdgeInsets.zero,
              ),
            ),
          ),
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: controller,
            builder: (context, value, _) {
              if (value.text.isEmpty) return const SizedBox.shrink();
              return GestureDetector(
                onTap: controller.clear,
                child: const Icon(
                  Icons.cancel,
                  size: 20,
                  color: AppColors.textTertiary,
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}

/// 단위(g · 개 · 인분) 고르기. 아직 안 골랐으면 "단위 선택"이 보인다.
class _UnitDropdown extends StatelessWidget {
  const _UnitDropdown({required this.value, required this.onChanged});

  final String? value;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 40,
      padding: const EdgeInsets.only(left: 7, right: 7),
      decoration: _fieldBox(),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: value,
          isExpanded: true,
          hint: Text(
            '단위 선택',
            style: _fieldText.copyWith(color: AppColors.textTertiary),
          ),
          icon: const Icon(
            Icons.keyboard_arrow_down,
            size: 20,
            color: AppColors.textTertiary,
          ),
          style: _fieldText,
          dropdownColor: AppColors.surface,
          borderRadius: BorderRadius.circular(10),
          items: [
            for (final unit in foodEntryUnits)
              DropdownMenuItem(value: unit, child: Text(unit)),
          ],
          onChanged: onChanged,
        ),
      ),
    );
  }
}

/// "목록에 추가" 버튼(빨강2 윤곽 + 연한 배경).
class _AddToListButton extends StatelessWidget {
  const _AddToListButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final shape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(10),
      side: const BorderSide(color: AppColors.primaryStrong),
    );
    return Material(
      color: AppColors.primaryStrongBg,
      shape: shape,
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: SizedBox(
          height: 37,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.add, size: 16, color: AppColors.primaryStrong),
              const SizedBox(width: 5),
              Text(
                '목록에 추가',
                style: AppTypography.buttonLabel.copyWith(
                  color: AppColors.primaryStrong,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// "추가한 음식" 옆의 개수 배지.
class _CountBadge extends StatelessWidget {
  const _CountBadge({required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 14,
      height: 14,
      alignment: Alignment.center,
      decoration: const BoxDecoration(
        color: AppColors.primaryStrongBg,
        shape: BoxShape.circle,
      ),
      child: Text(
        '$count',
        style: AppTypography.emphasis.copyWith(
          fontSize: 9,
          height: 1,
          color: AppColors.primaryStrong,
        ),
      ),
    );
  }
}

/// "전체 삭제" 버튼.
class _ClearAllButton extends StatelessWidget {
  const _ClearAllButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final shape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(5),
      side: const BorderSide(color: AppColors.border),
    );
    return Material(
      color: AppColors.surface,
      shape: shape,
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: SizedBox(
          width: 93,
          height: 30,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(
                Icons.delete_outline,
                size: 16,
                color: AppColors.primary,
              ),
              const SizedBox(width: 8),
              Text('전체 삭제', style: _fieldText),
            ],
          ),
        ),
      ),
    );
  }
}

/// 목록의 음식 한 줄 — 이름 옆에 "양+단위"를 작게 붙이고, 오른쪽 ✕ 로 지운다.
class _EntryCard extends StatelessWidget {
  const _EntryCard({required this.entry, required this.onRemove});

  final FoodEntry entry;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: AppRadius.lgRadius,
      ),
      child: Row(
        children: [
          Expanded(
            child: Row(
              children: [
                Flexible(
                  child: Text(
                    entry.name,
                    style: AppTypography.cardTitle,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 3),
                Text(
                  '${formatFoodAmount(entry.amount)}${entry.unit}',
                  style: AppTypography.caption.copyWith(fontSize: 9.5),
                ),
              ],
            ),
          ),
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: onRemove,
            child: const Padding(
              padding: EdgeInsets.symmetric(horizontal: 4),
              child: Icon(Icons.close, size: 14, color: AppColors.textTertiary),
            ),
          ),
        ],
      ),
    );
  }
}
