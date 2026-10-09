import 'package:flutter/material.dart';

/// 디자인 가이드(`docs/design-system.md` §2 컬러 토큰)의 Dart 구현.
///
/// Figma "컬러 팔레트"의 색만 쓴다. 팔레트에 없는 색을 새로 만들지 않는다
/// (딤 [overlay] 만 예외 — 메인 글씨색을 반투명하게 깐 것).
/// 값을 바꾸려면 문서와 이 파일을 함께 수정한다.
/// 팔레트 밖의 생 hex 를 위젯에서 직접 쓰지 않는다.
///
/// 색 사용 규칙
/// - 빨강1 [primary]: 모든 버튼(하단 버튼 · 선택 상태 버튼), 선택한 네비게이션 바,
///   채팅 색, 영양 정보가 없을 때 테두리, 초과했을 때 게이지 바,
///   달력 점 · 선택한 달력 날짜(글씨 · 테두리, 배경은 [primaryTint]).
/// - 빨강2 [primaryStrong]: 정보를 알려주는 글, 포만감(S).
/// - 흰색 [surface]: 카드 · 팝업 · 네비게이션 바 배경 등.
/// - 네비게이션 바 회색 [inactive]: 선택 안 한 탭 아이콘 · 라벨.
/// - Q·Q·S: 양 = 노랑, 질 = 초록, 포만도 = 빨강2.
/// - 영양 상태: 부족 = 노랑, 적정 = 초록, 초과 = 빨강1.
/// - 투약 변화: 증량 = 초록, 감소 = 빨강2.
class AppColors {
  AppColors._();

  // ── 배경 · 표면 ──────────────────────────────────────────────
  /// 배경색 #FDFCF7. 화면 배경 (따뜻한 오프화이트).
  static const Color background = Color(0xFFFDFCF7);

  /// 흰색 #FFFFFF. 카드, 팝업, 네비게이션 바 배경 등 여러 곳에서 가끔 쓴다.
  static const Color surface = Colors.white;

  /// 위(胃) 색 #FA9C95 (단색). 홈 위 게이지가 채워지는 색.
  static const Color stomach = Color(0xFFFA9C95);

  /// 홈 화면이 아직 그라디언트로 그려서 남겨 둔 임시 별칭. 두 값이 같아 단색으로 보인다.
  /// 홈 화면을 [stomach] 로 바꾸면 지운다.
  static const Color gaugeFrom = stomach;
  static const Color gaugeTo = stomach;

  /// 회색 배경 #EDF1F0. 트랙, 비활성 세그먼트.
  static const Color surfaceMuted = Color(0xFFEDF1F0);

  /// 회색 배경 #EDF1F0. 보조 블록, 그래프 바.
  static const Color surfaceSage = Color(0xFFEDF1F0);

  /// 팝업 · 바텀시트 딤. 메인 글씨 #3A2E22 를 반투명하게 깐다.
  static const Color overlay = Color(0x7A3A2E22);

  // ── 텍스트 ──────────────────────────────────────────────────
  /// 메인 글씨 #3A2E22. 제목 · 강조 글씨.
  static const Color textPrimary = Color(0xFF3A2E22);

  /// 일반 본문 #5A5A5A. 본문 글씨.
  static const Color textBody = Color(0xFF5A5A5A);

  /// 회색 글씨 #7A7873. 보조 설명.
  static const Color textSecondary = Color(0xFF7A7873);

  /// 네비게이션 바 회색 #A9B4B0. 축 라벨, 메타 정보.
  static const Color textTertiary = Color(0xFFA9B4B0);

  /// 배경색 #FDFCF7. 빨간 배경 위 글씨.
  static const Color textInverse = Color(0xFFFDFCF7);

  // ── 빨간색 ──────────────────────────────────────────────────
  /// 빨간색 #E85C4A. 모든 버튼, 네비게이션 바 선택, 채팅 색,
  /// 영양 정보가 없을 때 테두리, 초과했을 때 게이지 바 색.
  static const Color primary = Color(0xFFE85C4A);

  /// 빨강2 #C0663B. 정보를 알려주는 글, 포만감(S).
  static const Color primaryStrong = Color(0xFFC0663B);

  /// 빨강2 배경 #F7DCCB.
  static const Color primaryStrongBg = Color(0xFFF7DCCB);

  /// 빨강1 배경 #F6E7DE. 선택된 카드 · 선택한 달력 날짜 배경 등.
  static const Color primaryTint = Color(0xFFF6E7DE);

  // ── Q·Q·S 시맨틱 ────────────────────────────────────────────
  // 세 색은 의미가 고정되어 있다. 다른 용도로 재사용하지 않는다.

  /// 노란 글씨 #9C7A2E. 양(Quantity) · 부족.
  static const Color quantity = Color(0xFF9C7A2E);

  /// 노란 글씨 배경 #F3E9D6.
  static const Color quantityBg = Color(0xFFF3E9D6);

  /// 초록 글씨 #5A7A47. 질(Quality) · 적정 · +.
  static const Color quality = Color(0xFF5A7A47);

  /// 초록 글씨 배경 #DEEAD8.
  static const Color qualityBg = Color(0xFFDEEAD8);

  /// 빨강2 #C0663B. 포만감(Satiety).
  static const Color satiety = primaryStrong;

  /// 빨강2 배경 #F7DCCB.
  static const Color satietyBg = primaryStrongBg;

  // ── 선 · 비활성 ─────────────────────────────────────────────
  /// 테두리 회색 #E1E6E4. 기본 구분선, 카드 테두리, 탭바 상단선.
  static const Color border = Color(0xFFE1E6E4);

  /// 네비게이션 바 회색 #A9B4B0. 아웃라인 버튼, 선택 가능한 카드 테두리.
  static const Color borderStrong = Color(0xFFA9B4B0);

  /// 네비게이션 바 회색 #A9B4B0. 비활성 탭 아이콘 · 라벨.
  static const Color inactive = Color(0xFFA9B4B0);
}
