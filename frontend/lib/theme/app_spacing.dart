/// 디자인 가이드(`docs/design-system.md` §1 캔버스와 레이아웃)의 Dart 구현.
///
/// 위젯에서 매직 넘버 대신 항상 이 토큰을 쓴다.
/// 필요한 값이 없으면 문서에 먼저 추가한 뒤 여기에 반영한다.
class AppSpacing {
  AppSpacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;

  /// 화면 좌우 거터 (375 기준 콘텐츠 폭 327).
  static const double screenHorizontal = xl;

  /// 카드 내부 기본 패딩. 좁은 칩·배지는 [md].
  static const double cardPadding = lg;

  /// 카드 사이 간격 (12~16 범위의 기본값).
  static const double cardGap = md;

  /// 화면 타이틀 상단 여백 (상태바 아래).
  static const double titleTop = xl;
}

/// 고정 크기 레이아웃 상수. 여백 스케일과 성격이 달라 분리한다.
class AppLayout {
  AppLayout._();

  /// 기준 화면 (세로 전용).
  static const double designWidth = 375;
  static const double designHeight = 812;

  /// 기준 폭에서의 콘텐츠 폭.
  static const double contentWidth = 327;

  /// 하단 탭바 높이 (safe area 제외).
  static const double bottomBarHeight = 60;

  /// 스크롤 콘텐츠 하단 패딩. 탭바에 가리지 않게 최소값.
  static const double scrollBottomPadding = 80;

  /// 주 버튼 높이.
  static const double primaryButtonHeight = 52;

  /// 탭바 아이콘 크기.
  static const double tabIconSize = 18;
}
