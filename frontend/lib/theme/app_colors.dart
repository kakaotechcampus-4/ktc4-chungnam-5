import 'package:flutter/material.dart';

/// 디자인 가이드(`docs/design-system.md` §2 컬러 토큰)의 Dart 구현.
///
/// 값을 바꾸려면 문서와 이 파일을 함께 수정한다.
/// 팔레트 밖의 생 hex 를 위젯에서 직접 쓰지 않는다.
class AppColors {
  AppColors._();

  // ── 배경 · 표면 ──────────────────────────────────────────────
  /// 화면 배경 (따뜻한 오프화이트). 순백은 카드에만 쓴다.
  static const Color background = Color(0xFFFDFCF7);

  /// 카드 표면.
  static const Color surface = Colors.white;

  /// 트랙, 비활성 세그먼트.
  static const Color surfaceMuted = Color(0xFFF3F5F4);

  /// 보조 블록, 그래프 바. (구 `thumbnailPlaceholder`)
  static const Color surfaceSage = Color(0xFFEDF1F0);

  /// 팝업 · 바텀시트 딤.
  static const Color overlay = Color(0x7A0D0A08);

  // ── 텍스트 ──────────────────────────────────────────────────
  /// 본문 · 제목.
  static const Color textPrimary = Color(0xFF3A2E22);

  /// 보조 설명.
  static const Color textSecondary = Color(0xFF8A8073);

  /// 축 라벨, 메타 정보.
  static const Color textTertiary = Color(0xFF9AA6A2);

  /// 코랄 배경 위 텍스트.
  static const Color textInverse = Color(0xFFFDFCF7);

  /// 그래프 계열 이름.
  static const Color textChartLabel = Color(0xFF5A6A66);

  // ── 브랜드 ──────────────────────────────────────────────────
  /// 주 액션, 활성 탭, 강조. 화면당 한 군데만 쓴다.
  static const Color primary = Color(0xFFE85C4A);

  /// pressed 상태.
  static const Color primaryStrong = Color(0xFFC0492A);

  /// 브랜드 배경 블록 (선택된 카드 등).
  static const Color primaryTint = Color(0xFFFBE6E1);

  /// 위(胃) 게이지 그라디언트 시작.
  static const Color gaugeFrom = Color(0xFFF4A99C);

  /// 위(胃) 게이지 그라디언트 끝.
  static const Color gaugeTo = Color(0xFFE85C4A);

  // ── Q·Q·S 시맨틱 ────────────────────────────────────────────
  // 세 색은 의미가 고정되어 있다. 다른 용도로 재사용하지 않는다.

  /// 양(Quantity).
  static const Color quantity = Color(0xFF9C7A2E);
  static const Color quantityBg = Color(0xFFF3E9D6);

  /// 질(Quality).
  static const Color quality = Color(0xFF5A7A47);
  static const Color qualityBg = Color(0xFFDEEAD8);

  /// 포만감(Satiety).
  static const Color satiety = Color(0xFFC0663B);
  static const Color satietyBg = Color(0xFFF7DCCB);

  // ── 선 · 비활성 ─────────────────────────────────────────────
  /// 기본 구분선, 카드 테두리, 탭바 상단선. (구 `cardBorder`)
  static const Color border = Color(0xFFE1E6E4);

  /// 아웃라인 버튼, 선택 가능한 카드.
  static const Color borderStrong = Color(0xFFCBD8D4);

  /// 비활성 탭 아이콘 · 라벨.
  static const Color inactive = Color(0xFFA9B4B0);
}
