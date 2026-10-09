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
      // fromSeed 는 빨강 하나에서 팔레트에 없는 보조색 · 컨테이너색을 자동으로 만든다.
      // 색을 직접 지정하지 않은 기본 위젯(스낵바 · 날짜 선택기 · 리스트 아이콘 등)이
      // 팔레트 밖 색을 쓰지 않도록 모든 역할을 팔레트 색으로 채운다.
      colorScheme: const ColorScheme(
        brightness: Brightness.light,
        primary: AppColors.primary,
        onPrimary: AppColors.textInverse,
        primaryContainer: AppColors.primaryTint,
        onPrimaryContainer: AppColors.primaryStrong,
        secondary: AppColors.primaryStrong,
        onSecondary: AppColors.textInverse,
        secondaryContainer: AppColors.primaryTint,
        onSecondaryContainer: AppColors.primaryStrong,
        tertiary: AppColors.quality,
        onTertiary: AppColors.textInverse,
        tertiaryContainer: AppColors.qualityBg,
        onTertiaryContainer: AppColors.quality,
        error: AppColors.primary,
        onError: AppColors.textInverse,
        errorContainer: AppColors.primaryTint,
        onErrorContainer: AppColors.primaryStrong,
        surface: AppColors.surface,
        onSurface: AppColors.textPrimary,
        onSurfaceVariant: AppColors.textSecondary,
        surfaceDim: AppColors.surfaceMuted,
        surfaceBright: AppColors.surface,
        surfaceContainerLowest: AppColors.surface,
        surfaceContainerLow: AppColors.surface,
        surfaceContainer: AppColors.surface,
        surfaceContainerHigh: AppColors.surface,
        surfaceContainerHighest: AppColors.surfaceMuted,
        outline: AppColors.border,
        outlineVariant: AppColors.borderStrong,
        shadow: AppColors.textPrimary,
        scrim: AppColors.textPrimary,
        inverseSurface: AppColors.textPrimary,
        onInverseSurface: AppColors.textInverse,
        inversePrimary: AppColors.primaryTint,
      ),
      progressIndicatorTheme: const ProgressIndicatorThemeData(
        color: AppColors.primary,
        linearTrackColor: AppColors.surfaceMuted,
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
