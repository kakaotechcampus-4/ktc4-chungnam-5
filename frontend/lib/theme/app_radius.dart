import 'package:flutter/widgets.dart';

/// 디자인 가이드(`docs/design-system.md` §4 형태)의 Dart 구현.
///
/// Figma 실측값 9 / 11 / 13 은 SVG 임포트 스케일 잔재로 판정되어
/// 문서의 8 / 12 / 14 / 16 램프로 스냅했다.
class AppRadius {
  AppRadius._();

  /// 칩, 작은 태그.
  static const double sm = 8;

  /// 입력 필드, 버튼.
  static const double md = 12;

  /// 카드, 선택 항목.
  static const double lg = 14;

  /// 큰 컨테이너, 바텀시트 상단.
  static const double xl = 16;

  /// 세그먼티드 컨트롤, 필터.
  static const double pill = 999;

  static const BorderRadius smRadius = BorderRadius.all(Radius.circular(sm));
  static const BorderRadius mdRadius = BorderRadius.all(Radius.circular(md));
  static const BorderRadius lgRadius = BorderRadius.all(Radius.circular(lg));
  static const BorderRadius xlRadius = BorderRadius.all(Radius.circular(xl));
  static const BorderRadius pillRadius = BorderRadius.all(
    Radius.circular(pill),
  );

  /// 바텀시트: 상단만 둥글게.
  static const BorderRadius sheetRadius = BorderRadius.vertical(
    top: Radius.circular(xl),
  );

  // 용도별 별칭 (기존 코드 호환).
  static const BorderRadius chipBorderRadius = smRadius;
  static const BorderRadius thumbnailBorderRadius = mdRadius;
  static const BorderRadius cardBorderRadius = lgRadius;
}
