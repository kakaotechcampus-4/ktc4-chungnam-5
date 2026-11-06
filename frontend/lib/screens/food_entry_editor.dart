import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 음식 이름 · 양(g)을 적는 입력 틀(Figma "음식 추가" 패널).
///
/// 서버로 보내기 전까지는 화면 안에서만 들고 있는 값이라 영양 정보는 다루지 않는다.
/// 영양 정보는 서버가 이름으로 찾아 붙이고, 못 찾으면 음식 확인 화면에서 고친다.

/// 입력한 음식 한 줄.
class FoodEntry {
  const FoodEntry({
    required this.name,
    required this.amount,
    this.unit = foodEntryUnit,
  });

  final String name;
  final double amount;
  final String unit;
}

/// 고를 수 있는 단위는 g 하나다. BE 는 개·인분도 받지만 g 으로 바꿀 근거가
/// DB 에 없어 영양을 계산하지 못한다 — 확장은 MVP 이후로 미룬다.
const String foodEntryUnit = 'g';

/// 양을 화면에 적을 때 정수면 소수점을 뗀다(`1.0` → `1`).
String formatFoodAmount(double v) =>
    v == v.roundToDouble() ? '${v.toInt()}' : v.toString();

/// 입력 폼의 값을 들고 있다. 화면(시트)이 만들고 없앤다.
class FoodEntryController extends ChangeNotifier {
  FoodEntryController() {
    name.addListener(_clearError);
    amount.addListener(_clearError);
  }

  final TextEditingController name = TextEditingController();
  final TextEditingController amount = TextEditingController();
  final FocusNode nameFocus = FocusNode();

  String? _error;

  /// 마지막으로 막힌 이유. 입력을 고치면 사라진다.
  String? get error => _error;

  void _clearError() {
    if (_error == null) return;
    _error = null;
    notifyListeners();
  }

  void _fail(String message) {
    _error = message;
    notifyListeners();
  }

  /// "추가하기" — 폼이 올바르면 음식 한 줄을 돌려준다.
  /// 비었거나 덜 채워졌으면 [error] 를 채우고 null.
  FoodEntry? collect() {
    final text = name.text.trim();
    final amountText = amount.text.trim();
    if (text.isEmpty && amountText.isEmpty) {
      _fail('추가할 음식을 입력해 주세요');
      return null;
    }
    if (text.isEmpty) {
      _fail('음식 이름을 적어 주세요');
      return null;
    }
    final value = double.tryParse(amountText);
    if (value == null || value <= 0) {
      _fail('양을 0보다 큰 숫자로 적어 주세요');
      return null;
    }
    return FoodEntry(name: text, amount: value);
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

/// 음식 이름 · 양 입력 패널.
class FoodEntryEditor extends StatelessWidget {
  const FoodEntryEditor({super.key, required this.controller});

  final FoodEntryController controller;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) => _AddPanel(controller: controller),
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
          const _FieldLabel('양 (g)'),
          const SizedBox(height: 5),
          _ClearableField(
            controller: controller.amount,
            height: 40,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
            ],
          ),
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

/// 입력창 테두리 상자(흰 바탕 + 회색 테두리).
BoxDecoration _fieldBox() => BoxDecoration(
  color: AppColors.surface,
  border: Border.all(color: AppColors.border),
  borderRadius: BorderRadius.circular(10),
);

/// 입력창 위의 라벨("음식 이름" · "양 (g)").
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
