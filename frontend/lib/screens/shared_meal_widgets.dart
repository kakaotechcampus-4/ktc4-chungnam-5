import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 홈(`home_screen.dart`)과 기록(`meal_history_screen.dart`)이 **똑같이** 쓰는 조각들.
///
/// 원래는 `lib/widgets/` 로 올라가야 하지만, 폴더 구조를 팀 작업에 맞춰 나중에 정하기로 해서
/// 지금은 화면 폴더 안에 임시로 둔다. 세 번째 화면이 이걸 쓰게 되는 시점이
/// `lib/widgets/` 로 옮길 때다.
///
/// 디자인 기준: Figma `hOxrHBitBpjwIBBg2GO49y` node `60:189`(홈) · `94:6`(기록).
///
/// 아래쪽 상태 위젯([MealCardSkeleton] · [RetryBlock] · [EmptyBlock] · [NoticeBlock])은
/// Figma 에 해당 상태 화면이 없어 규칙을 정해 만든 것이다. 규칙은
/// `docs/design-system.md` §10 에 적어 뒀고, 다른 화면도 그걸 따른다.

/// 배지·칩 글자. 디자인 가이드 §5 가 배지에 대해 허용한 "10 Bold".
final TextStyle _badgeText = AppTypography.emphasis.copyWith(fontSize: 10);

/// 화면(또는 화면 안 한 블록)의 데이터 적재 상태.
///
/// 기록 화면처럼 API 가 둘 이상이면 블록마다 따로 들고 있는다.
/// 한쪽이 실패해도 다른 쪽을 같이 죽이지 않기 위해서다.
enum LoadState { loading, ready, failed }

/// 투약 단계 배지 (`유지기` 등). 코랄 틴트 알약.
class StageBadge extends StatelessWidget {
  const StageBadge({super.key, required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: AppSpacing.xs,
      ),
      decoration: const BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: AppRadius.pillRadius,
      ),
      child: Text(label, style: _badgeText.copyWith(color: AppColors.primary)),
    );
  }
}

/// Q·Q·S 배지 한 줄. 세 색은 의미가 고정이라 다른 용도로 쓰지 않는다.
///
/// - 양: 서버가 내려주는 라벨(`부족`·`적정`·`과다`). FE 는 계산하지 않는다.
/// - 질 / 포만도: 0~100 정수
class QqsBadgeRow extends StatelessWidget {
  const QqsBadgeRow({
    super.key,
    required this.quantityLabel,
    required this.quality,
    required this.satiety,
  });

  final String quantityLabel;
  final int quality;
  final int satiety;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        _Badge(
          text: '양 $quantityLabel',
          background: AppColors.quantityBg,
          foreground: AppColors.quantity,
        ),
        const SizedBox(width: AppSpacing.xs),
        _Badge(
          text: '질 $quality점',
          background: AppColors.qualityBg,
          foreground: AppColors.quality,
        ),
        const SizedBox(width: AppSpacing.xs),
        _Badge(
          text: '포만도 $satiety%',
          background: AppColors.satietyBg,
          foreground: AppColors.satiety,
        ),
      ],
    );
  }
}

class _Badge extends StatelessWidget {
  const _Badge({
    required this.text,
    required this.background,
    required this.foreground,
  });

  final String text;
  final Color background;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 3),
      decoration: BoxDecoration(
        color: background,
        borderRadius: AppRadius.smRadius,
      ),
      child: Text(text, style: _badgeText.copyWith(color: foreground)),
    );
  }
}

/// 기록된 한 끼 카드. 홈과 기록이 같은 모양을 쓰고, 기록만 우측 `>` 를 켠다.
class MealCard extends StatelessWidget {
  const MealCard({
    super.key,
    required this.mealTypeLabel,
    required this.time,
    required this.foodNames,
    required this.quantityLabel,
    required this.quality,
    required this.satiety,
    this.onTap,
    this.showChevron = false,
  });

  /// `아침` · `점심` · `저녁` · `간식`.
  final String mealTypeLabel;

  /// `08:20`.
  final String time;

  /// `토스트, 그릭요거트`.
  final String foodNames;

  final String quantityLabel;
  final int quality;
  final int satiety;

  final VoidCallback? onTap;
  final bool showChevron;

  static const double _thumbnailSize = 54;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadius.lgRadius,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // 실제 사진은 S3 URL 로 교체한다. 지금은 자리만 잡는다.
              Container(
                width: _thumbnailSize,
                height: _thumbnailSize,
                decoration: const BoxDecoration(
                  color: AppColors.surfaceSage,
                  borderRadius: AppRadius.mdRadius,
                ),
              ),
              const SizedBox(width: AppSpacing.md),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('$mealTypeLabel · $time', style: AppTypography.caption),
                    const SizedBox(height: 2),
                    Text(
                      foodNames,
                      style: AppTypography.cardTitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: AppSpacing.sm),
                    QqsBadgeRow(
                      quantityLabel: quantityLabel,
                      quality: quality,
                      satiety: satiety,
                    ),
                  ],
                ),
              ),
              if (showChevron) ...[
                const SizedBox(width: AppSpacing.sm),
                const Icon(
                  Icons.chevron_right,
                  size: AppLayout.tabIconSize,
                  color: AppColors.inactive,
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// 아직 기록하지 않은 끼니 자리. 점선 테두리 + 가운데 정렬.
class AddMealCard extends StatelessWidget {
  const AddMealCard({
    super.key,
    required this.label,
    this.subtitle,
    this.onTap,
  });

  /// `＋ 저녁 식사 기록하기`.
  final String label;

  /// Figma 의 `아래 카메라 버튼으로 바로 찍을 수 있어요` 자리.
  /// 중앙 카메라 버튼이 미확정(`docs/design-system.md` §9)이라 지금은 넘기지 않는다.
  final String? subtitle;

  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: const _DashedBorderPainter(),
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadius.lgRadius,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(vertical: AppSpacing.lg),
          alignment: Alignment.center,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(label, style: AppTypography.cardTitle),
              if (subtitle != null) ...[
                const SizedBox(height: AppSpacing.xs),
                Text(subtitle!, style: AppTypography.caption),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// 점선 테두리. 테마의 `Card` 는 실선뿐이라 직접 그린다.
class _DashedBorderPainter extends CustomPainter {
  const _DashedBorderPainter();

  static const double _dash = 4;
  static const double _gap = 4;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.borderStrong
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;

    final path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(
          Offset.zero & size,
          const Radius.circular(AppRadius.lg),
        ),
      );

    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        canvas.drawPath(
          metric.extractPath(distance, distance + _dash),
          paint,
        );
        distance += _dash + _gap;
      }
    }
  }

  @override
  bool shouldRepaint(_DashedBorderPainter oldDelegate) => false;
}

// ── 상태 위젯 ────────────────────────────────────────────────
// Figma 에 없는 상태들이라 규칙을 정해 만들었다(`docs/design-system.md` §10).
// - 새 색 토큰을 만들지 않는다. 스켈레톤은 surfaceMuted, 안내 블록은 primaryTint.
// - 실패·안내는 색이 아니라 블록 형태로 구분한다. 팔레트에 에러색이 없고,
//   코랄을 쓰면 §2 "강조색은 화면당 한 군데" 와 부딪힌다.
// - 애니메이션 없는 정적 스켈레톤. shimmer 는 패키지가 필요하다.
// - 카피는 평서형 존댓말이고 사용자를 탓하지 않는다(§7).

/// 스켈레톤 한 조각.
class SkeletonBox extends StatelessWidget {
  const SkeletonBox({super.key, required this.width, required this.height});

  final double width;
  final double height;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: const BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.smRadius,
      ),
    );
  }
}

/// [MealCard] 로딩 자리.
///
/// 카드 높이·패딩·썸네일 크기를 [MealCard] 와 똑같이 맞춘다.
/// 높이가 다르면 로딩에서 완료로 넘어갈 때 화면이 튄다.
class MealCardSkeleton extends StatelessWidget {
  const MealCardSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.md),
        child: Row(
          children: [
            const SkeletonBox(width: 54, height: 54),
            const SizedBox(width: AppSpacing.md),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: const [
                  SkeletonBox(width: 64, height: 10),
                  SizedBox(height: 6),
                  SkeletonBox(width: 120, height: 12),
                  SizedBox(height: AppSpacing.sm),
                  SkeletonBox(width: 160, height: 18),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 불러오기 실패. 문구 + 다시 시도.
class RetryBlock extends StatelessWidget {
  const RetryBlock({
    super.key,
    this.message = '잠시 후 다시 시도해 주세요',
    required this.onRetry,
  });

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
      decoration: const BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: AppRadius.lgRadius,
      ),
      child: Column(
        children: [
          Text(
            message,
            textAlign: TextAlign.center,
            style: AppTypography.body,
          ),
          const SizedBox(height: AppSpacing.md),
          OutlinedButton(onPressed: onRetry, child: const Text('다시 시도')),
        ],
      ),
    );
  }
}

/// 보여 줄 게 없는 상태. 실패가 아니므로 버튼을 두지 않는다.
class EmptyBlock extends StatelessWidget {
  const EmptyBlock({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.xl),
      alignment: Alignment.center,
      child: Text(message, style: AppTypography.bodySecondary),
    );
  }
}

/// 안내 블록. `safetyStatus: BLOCKED` 의 상담 안내가 주 용도다.
///
/// 의료·처방 판단을 하지 않고 담당 의사 상담만 안내한다(§7).
class NoticeBlock extends StatelessWidget {
  const NoticeBlock({
    super.key,
    this.message = '지금은 피드백을 드리기 어려워요. 담당 의사와 상담해 주세요.',
  });

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
      decoration: const BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: AppRadius.lgRadius,
      ),
      child: Text(
        message,
        textAlign: TextAlign.center,
        style: AppTypography.body,
      ),
    );
  }
}
