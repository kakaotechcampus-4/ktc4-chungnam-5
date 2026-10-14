import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'food_entry_editor.dart';

/// 음식 확인 화면의 "＋ 음식 추가" 가 여는 바텀시트(Figma 화면 5 · 음식 추가 a-2 · a-3).
///
/// 이름 · 양 · 단위를 적어 목록에 쌓고 "추가하기" 로 한 번에 돌려준다. 서버에는
/// 보내지 않는다 — 호출한 화면이 보낸다. 닫거나 바깥을 누르면 null.
Future<List<FoodEntry>?> showMealAddSheet(BuildContext context) {
  return showModalBottomSheet<List<FoodEntry>>(
    context: context,
    isScrollControlled: true,
    backgroundColor: AppColors.surface,
    shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
    builder: (_) => const _MealAddSheet(),
  );
}

class _MealAddSheet extends StatefulWidget {
  const _MealAddSheet();

  @override
  State<_MealAddSheet> createState() => _MealAddSheetState();
}

class _MealAddSheetState extends State<_MealAddSheet> {
  final FoodEntryController _controller = FoodEntryController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final entries = _controller.collect();
    if (entries != null) Navigator.of(context).pop(entries);
  }

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: media.size.height * 0.9),
        child: Padding(
          padding: EdgeInsets.only(bottom: media.viewInsets.bottom),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              _buildHeader(),
              const Divider(height: 1, thickness: 1, color: AppColors.border),
              Flexible(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(
                    AppSpacing.screenHorizontal,
                    14,
                    AppSpacing.screenHorizontal,
                    0,
                  ),
                  child: FoodEntryEditor(controller: _controller),
                ),
              ),
              _buildSubmitButton(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return SizedBox(
      width: double.infinity,
      height: 60,
      child: Stack(
        alignment: Alignment.center,
        children: [
          Text(
            '음식 추가',
            style: AppTypography.sectionHead.copyWith(fontSize: 16),
          ),
          Positioned(
            right: AppSpacing.md,
            child: IconButton(
              onPressed: () => Navigator.of(context).pop(),
              icon: const Icon(
                Icons.close,
                size: 20,
                color: AppColors.textSecondary,
              ),
              tooltip: '닫기',
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSubmitButton() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        14,
        AppSpacing.screenHorizontal,
        AppSpacing.lg,
      ),
      child: SizedBox(
        width: double.infinity,
        height: AppLayout.primaryButtonHeight,
        child: ElevatedButton(
          onPressed: _submit,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            elevation: 0,
            shape: const RoundedRectangleBorder(
              borderRadius: AppRadius.xlRadius,
            ),
          ),
          child: Text('추가하기', style: AppTypography.buttonLabel),
        ),
      ),
    );
  }
}
