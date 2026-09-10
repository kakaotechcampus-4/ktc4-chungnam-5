import 'package:flutter/material.dart';

import 'app_colors.dart';
import 'app_radius.dart';
import 'app_spacing.dart';
import 'app_typography.dart';

class AppTheme {
  AppTheme._();

  static ThemeData get light {
    return ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: AppColors.background,
      fontFamily: AppTypography.fontFamily,
      fontFamilyFallback: AppTypography.fontFamilyFallback,
      textTheme: AppTypography.textTheme,
      colorScheme: ColorScheme.fromSeed(
        seedColor: AppColors.primary,
        primary: AppColors.primary,
        onPrimary: AppColors.textInverse,
        surface: AppColors.surface,
        onSurface: AppColors.textPrimary,
        outline: AppColors.border,
        outlineVariant: AppColors.borderStrong,
        brightness: Brightness.light,
      ),
      dividerColor: AppColors.border,
      dividerTheme: const DividerThemeData(
        color: AppColors.border,
        thickness: 1,
        space: 1,
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: AppColors.background,
        foregroundColor: AppColors.textPrimary,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        titleTextStyle: AppTypography.screenTitle,
      ),
      // 그림자로 depth 를 만들지 않는다. 카드는 테두리와 배경 대비로 띄운다.
      cardTheme: CardThemeData(
        color: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: const RoundedRectangleBorder(
          borderRadius: AppRadius.lgRadius,
          side: BorderSide(color: AppColors.border),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: AppColors.primary,
          foregroundColor: AppColors.textInverse,
          disabledBackgroundColor: AppColors.inactive,
          minimumSize: const Size.fromHeight(AppLayout.primaryButtonHeight),
          elevation: 0,
          textStyle: AppTypography.buttonLabel,
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.mdRadius),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.textPrimary,
          backgroundColor: Colors.transparent,
          minimumSize: const Size.fromHeight(AppLayout.primaryButtonHeight),
          side: const BorderSide(color: AppColors.borderStrong),
          textStyle: AppTypography.buttonLabel.copyWith(
            color: AppColors.textPrimary,
          ),
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.mdRadius),
        ),
      ),
      bottomSheetTheme: const BottomSheetThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        modalBarrierColor: AppColors.overlay,
        shape: RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
      ),
      dialogTheme: const DialogThemeData(
        backgroundColor: AppColors.surface,
        surfaceTintColor: Colors.transparent,
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: AppRadius.xlRadius),
      ),
      bottomNavigationBarTheme: BottomNavigationBarThemeData(
        backgroundColor: AppColors.surface,
        selectedItemColor: AppColors.primary,
        unselectedItemColor: AppColors.inactive,
        type: BottomNavigationBarType.fixed,
        showUnselectedLabels: true,
        // 탭바 상단 구분은 RootShell 의 1px 테두리가 담당한다.
        elevation: 0,
        selectedIconTheme: const IconThemeData(size: AppLayout.tabIconSize),
        unselectedIconTheme: const IconThemeData(size: AppLayout.tabIconSize),
        selectedLabelStyle: AppTypography.caption.copyWith(
          color: AppColors.primary,
        ),
        unselectedLabelStyle: AppTypography.caption.copyWith(
          color: AppColors.inactive,
        ),
      ),
    );
  }
}
