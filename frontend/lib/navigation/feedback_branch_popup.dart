import 'package:flutter/material.dart';

import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_typography.dart';
import 'nav_icons.dart';

/// 피드백 탭을 누르면 탭바 위에 뜨는 분기 팝업(Figma 2-b).
/// 단기 피드백(→ 기록 달력) · 장기 피드백 두 말풍선 중 하나를 고르면 그 값을 돌려주고,
/// 바깥을 누르면 null 이다.
///
/// [anchorCenterX] 는 피드백 탭 아이콘의 가로 중심, [bottomOffset] 은 화면 아래에서
/// 탭바 윗선까지의 거리다. 두 말풍선의 꼬리가 이 가운데 지점으로 모인다.
Future<FeedbackKind?> showFeedbackBranchPopup(
  BuildContext context, {
  required double anchorCenterX,
  required double bottomOffset,
}) {
  return showGeneralDialog<FeedbackKind>(
    context: context,
    barrierDismissible: true,
    barrierLabel: '닫기',
    barrierColor: AppColors.overlay,
    transitionDuration: const Duration(milliseconds: 320),
    pageBuilder: (dialogContext, animation, _) => _FeedbackBranchPopup(
      animation: animation,
      anchorCenterX: anchorCenterX,
      bottomOffset: bottomOffset,
    ),
    // 딤 배경의 페이드는 route 가 맡고, 말풍선 애니메이션은 팝업 안에서 한다.
    transitionBuilder: (_, _, _, child) => child,
  );
}

class _FeedbackBranchPopup extends StatefulWidget {
  const _FeedbackBranchPopup({
    required this.animation,
    required this.anchorCenterX,
    required this.bottomOffset,
  });

  final Animation<double> animation;
  final double anchorCenterX;
  final double bottomOffset;

  static const double _cardSize = 73;
  static const double _cardGap = 4;
  static const double _tailWidth = 23;
  static const double _tailHeight = 11;
  static const double _bottomGap = 2;
  static const double _totalWidth = _cardSize * 2 + _cardGap;

  @override
  State<_FeedbackBranchPopup> createState() => _FeedbackBranchPopupState();
}

class _FeedbackBranchPopupState extends State<_FeedbackBranchPopup> {
  // 말풍선이 꼬리 끝(탭 가운데)에서 커지며 살짝 튀어나왔다 자리 잡는다("뾱").
  // 왼쪽이 먼저, 오른쪽이 조금 늦게 따라 나온다. 효과를 빼려면 [_pop] 을 child 만 돌려주게 하면 된다.
  late final CurvedAnimation _leftPop = _curve(0, 0.8);
  late final CurvedAnimation _rightPop = _curve(0.2, 1);

  CurvedAnimation _curve(double begin, double end) => CurvedAnimation(
    parent: widget.animation,
    curve: Interval(begin, end, curve: Curves.easeOutBack),
    reverseCurve: Curves.easeIn,
  );

  @override
  void dispose() {
    _leftPop.dispose();
    _rightPop.dispose();
    super.dispose();
  }

  Widget _pop(Animation<double> animation, Alignment origin, Widget child) =>
      ScaleTransition(scale: animation, alignment: origin, child: child);

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.sizeOf(context).width;
    final left = (widget.anchorCenterX - _FeedbackBranchPopup._totalWidth / 2)
        .clamp(8.0, screenWidth - _FeedbackBranchPopup._totalWidth - 8);

    return Material(
      type: MaterialType.transparency,
      child: Stack(
        children: [
          Positioned(
            left: left,
            bottom: widget.bottomOffset + _FeedbackBranchPopup._bottomGap,
            child: Row(
              children: [
                _pop(
                  _leftPop,
                  Alignment.bottomRight,
                  _BranchBubble(
                    icon: const CalendarDayIcon(),
                    label: '단기 피드백',
                    tailOnRight: true,
                    onTap: () => Navigator.of(context).pop(FeedbackKind.short),
                  ),
                ),
                const SizedBox(width: _FeedbackBranchPopup._cardGap),
                _pop(
                  _rightPop,
                  Alignment.bottomLeft,
                  _BranchBubble(
                    icon: const GraphUpIcon(),
                    label: '장기 피드백',
                    tailOnRight: false,
                    onTap: () => Navigator.of(context).pop(FeedbackKind.long),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 말풍선 한 칸. 꼬리는 안쪽 아래 모서리에서 가운데 틈 쪽으로 내려간다.
class _BranchBubble extends StatelessWidget {
  const _BranchBubble({
    required this.icon,
    required this.label,
    required this.tailOnRight,
    required this.onTap,
  });

  final Widget icon;
  final String label;
  final bool tailOnRight;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    const cardSize = _FeedbackBranchPopup._cardSize;
    return SizedBox(
      width: cardSize,
      height: cardSize + _FeedbackBranchPopup._tailHeight,
      child: Stack(
        children: [
          Positioned.fill(
            child: CustomPaint(
              painter: _BubblePainter(
                tailOnRight: tailOnRight,
                cardHeight: cardSize,
                tailWidth: _FeedbackBranchPopup._tailWidth,
                fill: AppColors.surface,
                border: AppColors.border,
              ),
            ),
          ),
          Positioned(
            left: 0,
            top: 0,
            width: cardSize,
            height: cardSize,
            child: InkWell(
              onTap: onTap,
              borderRadius: AppRadius.lgRadius,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  IconTheme(
                    data: const IconThemeData(
                      size: 18,
                      color: AppColors.textPrimary,
                    ),
                    child: icon,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    label,
                    style: AppTypography.caption.copyWith(
                      fontWeight: FontWeight.w700,
                      color: AppColors.textPrimary,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _BubblePainter extends CustomPainter {
  const _BubblePainter({
    required this.tailOnRight,
    required this.cardHeight,
    required this.tailWidth,
    required this.fill,
    required this.border,
  });

  final bool tailOnRight;
  final double cardHeight;
  final double tailWidth;
  final Color fill;
  final Color border;

  @override
  void paint(Canvas canvas, Size size) {
    const r = AppRadius.lg;
    const radius = Radius.circular(r);
    final w = size.width;
    final h = cardHeight;
    final tip = size.height;

    final path = Path()..moveTo(r, 0);
    if (tailOnRight) {
      path
        ..lineTo(w - r, 0)
        ..arcToPoint(Offset(w, r), radius: radius)
        ..lineTo(w, tip)
        ..lineTo(w - tailWidth, h)
        ..lineTo(r, h)
        ..arcToPoint(Offset(0, h - r), radius: radius)
        ..lineTo(0, r)
        ..arcToPoint(const Offset(r, 0), radius: radius);
    } else {
      path
        ..lineTo(w - r, 0)
        ..arcToPoint(Offset(w, r), radius: radius)
        ..lineTo(w, h - r)
        ..arcToPoint(Offset(w - r, h), radius: radius)
        ..lineTo(tailWidth, h)
        ..lineTo(0, tip)
        ..lineTo(0, r)
        ..arcToPoint(const Offset(r, 0), radius: radius);
    }
    path.close();

    canvas.drawPath(path, Paint()..color = fill);
    canvas.drawPath(
      path,
      Paint()
        ..color = border
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..strokeJoin = StrokeJoin.round,
    );
  }

  @override
  bool shouldRepaint(_BubblePainter old) =>
      old.tailOnRight != tailOnRight ||
      old.cardHeight != cardHeight ||
      old.tailWidth != tailWidth ||
      old.fill != fill ||
      old.border != border;
}
