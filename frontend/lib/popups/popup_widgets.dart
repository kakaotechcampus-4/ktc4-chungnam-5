import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 팝업 공용 조각. Figma 팝업(2-a 컨디션 기록 · 10 사후 포만감 체크인)이
/// 같은 틀을 쓴다: 가운데 카드 + 제목/✕ + 섹션들 + 나중에/기록 저장.
///
/// NOTE: 디자인 가이드 §5 는 팝업을 바텀시트로 적었지만 Figma 는 가운데
/// 다이얼로그다. §0 메타 규칙대로 Figma 를 따랐다. 모서리는 Figma 20 대신
/// 토큰 램프의 16, 그림자 대신 딤(§5 "딤이 주 depth 장치")을 쓴다.

/// 팝업 틀. 내용이 길면 스크롤된다.
class PopupDialog extends StatelessWidget {
  const PopupDialog({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: AppColors.background,
      insetPadding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.screenHorizontal,
        vertical: AppSpacing.xl,
      ),
      shape: const RoundedRectangleBorder(
        borderRadius: AppRadius.xlRadius,
        side: BorderSide(color: AppColors.border),
      ),
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: children,
        ),
      ),
    );
  }
}

/// 제목 + ✕ 닫기. [subtitle] 이 있으면 제목 아래에 붙는다.
class PopupHeader extends StatelessWidget {
  const PopupHeader({
    super.key,
    required this.title,
    required this.onClose,
    this.subtitle,
  });

  final String title;
  final VoidCallback onClose;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(child: Text(title, style: AppTypography.sectionHead)),
            Material(
              color: AppColors.surfaceSage,
              shape: const CircleBorder(),
              child: InkWell(
                onTap: onClose,
                customBorder: const CircleBorder(),
                child: const SizedBox.square(
                  dimension: 28,
                  child: Icon(
                    Icons.close,
                    size: AppLayout.tabIconSize,
                    color: AppColors.textSecondary,
                    semanticLabel: '닫기',
                  ),
                ),
              ),
            ),
          ],
        ),
        if (subtitle != null) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(
            subtitle!,
            style: AppTypography.caption.copyWith(
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ],
    );
  }
}

/// 섹션 제목(좌) + 보조 설명(우). [trailing] 을 주면 설명 대신 붙는다.
class PopupSectionLabel extends StatelessWidget {
  const PopupSectionLabel({
    super.key,
    required this.title,
    this.hint,
    this.trailing,
  });

  final String title;
  final String? hint;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(child: Text(title, style: AppTypography.emphasis)),
        if (trailing != null)
          trailing!
        else if (hint != null)
          Text(
            hint!,
            style: AppTypography.caption.copyWith(
              color: AppColors.textTertiary,
            ),
          ),
      ],
    );
  }
}

/// 가로로 나눠 하나만 고르는 칸(식욕 5단계 · 다시 배고파진 시점).
/// 선택 시 포만감 계열 — Figma 그대로.
class PopupChoiceTile extends StatelessWidget {
  const PopupChoiceTile({
    super.key,
    required this.label,
    required this.selected,
    required this.onTap,
    this.height = 38,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final double height;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadius.mdRadius,
      child: Container(
        height: height,
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.xs),
        decoration: BoxDecoration(
          color: selected ? AppColors.satietyBg : AppColors.surface,
          borderRadius: AppRadius.mdRadius,
          border: Border.all(
            color: selected ? AppColors.primary : AppColors.border,
            width: selected ? 1.5 : 1,
          ),
        ),
        child: FittedBox(
          fit: BoxFit.scaleDown,
          child: Text(
            label,
            maxLines: 1,
            style: selected
                ? AppTypography.caption.copyWith(
                    fontWeight: FontWeight.w700,
                    color: AppColors.satiety,
                  )
                : AppTypography.caption.copyWith(
                    color: AppColors.textSecondary,
                  ),
          ),
        ),
      ),
    );
  }
}

/// 하단 액션: 나중에(보조, 좌) / 기록 저장(주, 우). §5 "보조 좌 / 주 우".
class PopupActions extends StatelessWidget {
  const PopupActions({
    super.key,
    required this.onLater,
    required this.onSave,
    required this.saving,
  });

  final VoidCallback onLater;

  /// null 이면 저장 버튼이 비활성화된다.
  final VoidCallback? onSave;
  final bool saving;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        SizedBox(
          width: 96,
          child: OutlinedButton(
            onPressed: saving ? null : onLater,
            style: OutlinedButton.styleFrom(
              foregroundColor: AppColors.textSecondary,
              backgroundColor: AppColors.surface,
              shape: const RoundedRectangleBorder(
                borderRadius: AppRadius.lgRadius,
              ),
            ),
            child: const Text('나중에'),
          ),
        ),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: FilledButton(
            onPressed: saving ? null : onSave,
            style: FilledButton.styleFrom(
              shape: const RoundedRectangleBorder(
                borderRadius: AppRadius.lgRadius,
              ),
            ),
            child: Text(saving ? '저장 중' : '기록 저장'),
          ),
        ),
      ],
    );
  }
}

/// 팝업 안 저장 실패 문구.
class PopupErrorText extends StatelessWidget {
  const PopupErrorText({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Text(
      message,
      style: AppTypography.caption.copyWith(color: AppColors.primary),
    );
  }
}
