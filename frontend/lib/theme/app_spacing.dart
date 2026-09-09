/// Figma `hOxrHBitBpjwIBBg2GO49y` 와이어프레임(예: node 96:6, 96:9, 96:16)에서
/// 실측한 여백 값 기준 공통 스페이싱 스케일.
class AppSpacing {
  AppSpacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;

  /// 화면 좌우 기본 여백 (375 너비 화면에서 콘텐츠 폭 327 기준: (375-327)/2)
  static const double screenHorizontal = xl;
}
