import 'package:flutter/material.dart';

import 'app_colors.dart';

/// 디자인 가이드(`docs/design-system.md` §3 타이포그래피)의 Dart 구현.
///
/// Figma 원본은 Inter 지만 한글 UI 이므로 구현은 **Pretendard** 를 쓴다.
/// `assets/fonts/` 에 Regular(400)·Bold(700) 두 굵기를 번들하여
/// (`pubspec.yaml` 의 `fonts:` 항목) 네트워크 없이 즉시 렌더된다.
/// 숫자 강조는 크기가 아니라 굵기와 색으로 만든다.
class AppTypography {
  AppTypography._();

  static const String fontFamily = 'Pretendard';

  /// 폰트 파일이 손상·누락된 극단적 상황을 대비한 시스템 한글 폰트.
  static const List<String> fontFamilyFallback = <String>[
    '-apple-system',
    'system-ui',
    'Apple SD Gothic Neo',
    'Malgun Gothic',
  ];

  static const double _tightHeight = 1.25; // 제목 · 수치
  static const double _bodyHeight = 1.5; // 본문

  static const TextStyle _base = TextStyle(
    fontFamily: fontFamily,
    fontFamilyFallback: fontFamilyFallback,
    color: AppColors.textPrimary,
  );

  /// 게이지 수치 (홈 포만감 %). 46 Bold.
  static final TextStyle gaugeValue = _base.copyWith(
    fontSize: 46,
    fontWeight: FontWeight.w700,
    height: _tightHeight,
    color: AppColors.primary,
  );

  /// 화면 타이틀. 20 Bold.
  static final TextStyle screenTitle = _base.copyWith(
    fontSize: 20,
    fontWeight: FontWeight.w700,
    height: _tightHeight,
  );

  /// 섹션 헤드. 16 Bold.
  static final TextStyle sectionHead = _base.copyWith(
    fontSize: 16,
    fontWeight: FontWeight.w700,
    height: _tightHeight,
  );

  /// 카드 제목. 13 Bold.
  static final TextStyle cardTitle = _base.copyWith(
    fontSize: 13,
    fontWeight: FontWeight.w700,
    height: _tightHeight,
  );

  /// 강조 수치 · 배지. 12 Bold. 색은 문맥에 따라 지정한다.
  static final TextStyle emphasis = _base.copyWith(
    fontSize: 12,
    fontWeight: FontWeight.w700,
    height: _tightHeight,
  );

  /// 본문. 11 Regular.
  static final TextStyle body = _base.copyWith(
    fontSize: 11,
    fontWeight: FontWeight.w400,
    height: _bodyHeight,
  );

  /// 보조 설명. 11 Regular.
  static final TextStyle bodySecondary = body.copyWith(
    color: AppColors.textSecondary,
  );

  /// 캡션 · 탭 라벨 · 축 라벨. 10 Regular.
  static final TextStyle caption = _base.copyWith(
    fontSize: 10,
    fontWeight: FontWeight.w400,
    height: _bodyHeight,
    color: AppColors.textTertiary,
  );

  /// 주 버튼 라벨. 14 Bold.
  static final TextStyle buttonLabel = _base.copyWith(
    fontSize: 14,
    fontWeight: FontWeight.w700,
    height: _tightHeight,
    color: AppColors.textInverse,
  );

  static TextTheme get textTheme => TextTheme(
    displayLarge: gaugeValue,
    titleLarge: screenTitle,
    titleMedium: sectionHead,
    titleSmall: cardTitle,
    labelLarge: buttonLabel,
    labelMedium: emphasis,
    labelSmall: caption,
    bodyLarge: body,
    bodyMedium: body,
    bodySmall: bodySecondary,
  );
}
