import 'package:flutter/material.dart';

import '../theme/app_colors.dart';

/// 하단 탭 · 피드백 분기 팝업에서 쓰는 직접 그린 아이콘.
/// Material 아이콘에 같은 모양이 없어 24x24 기준 좌표로 그린다.
/// 크기와 색은 주변 [IconTheme] 을 따른다(탭바가 선택/비선택 색을 넣어 준다).

Widget _icon(BuildContext context, CustomPainter Function(Color) build) {
  final theme = IconTheme.of(context);
  final size = theme.size ?? 24;
  return SizedBox.square(
    dimension: size,
    child: CustomPaint(painter: build(theme.color ?? AppColors.textPrimary)),
  );
}

Paint _stroke(Color color, {double width = 1.8}) => Paint()
  ..color = color
  ..style = PaintingStyle.stroke
  ..strokeWidth = width
  ..strokeJoin = StrokeJoin.round
  ..strokeCap = StrokeCap.round;

/// 피드백 탭: 모서리가 접힌 문서.
class FeedbackTabIcon extends StatelessWidget {
  const FeedbackTabIcon({super.key});

  @override
  Widget build(BuildContext context) => _icon(context, _FeedbackPainter.new);
}

class _FeedbackPainter extends CustomPainter {
  const _FeedbackPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 24);
    final paint = _stroke(color);
    canvas.drawPath(
      Path()
        ..moveTo(5.5, 3)
        ..lineTo(13, 3)
        ..lineTo(19, 8)
        ..lineTo(19, 21)
        ..lineTo(5.5, 21)
        ..close(),
      paint,
    );
    canvas.drawPath(
      Path()
        ..moveTo(13, 3)
        ..lineTo(13, 8)
        ..lineTo(19, 8),
      paint,
    );
    canvas.drawLine(const Offset(8.6, 12), const Offset(16, 12), paint);
    canvas.drawLine(const Offset(8.6, 14.8), const Offset(12.8, 14.8), paint);
  }

  @override
  bool shouldRepaint(_FeedbackPainter old) => old.color != color;
}

/// AI 탭: 눈 두 개 달린 둥근 사각형 + 오른쪽 위 반짝이.
class AiTabIcon extends StatelessWidget {
  const AiTabIcon({super.key});

  @override
  Widget build(BuildContext context) => _icon(context, _AiPainter.new);
}

class _AiPainter extends CustomPainter {
  const _AiPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 24);
    final paint = _stroke(color);
    const r = Radius.circular(3.2);

    // 몸통: 오른쪽 위 모서리는 반짝이 자리라 열어 둔다.
    canvas.drawPath(
      Path()
        ..moveTo(14, 7)
        ..lineTo(6.2, 7)
        ..arcToPoint(const Offset(3, 10.2), radius: r, clockwise: false)
        ..lineTo(3, 17.8)
        ..arcToPoint(const Offset(6.2, 21), radius: r, clockwise: false)
        ..lineTo(17.3, 21)
        ..arcToPoint(const Offset(20.5, 17.8), radius: r, clockwise: false)
        ..lineTo(20.5, 12),
      paint,
    );

    canvas.drawLine(const Offset(9, 13), const Offset(9, 15.5), paint);
    canvas.drawLine(const Offset(15, 13), const Offset(15, 15.5), paint);

    const c = Offset(19.2, 5.2);
    const radius = 3.6;
    const k = 0.9;
    canvas.drawPath(
      Path()
        ..moveTo(c.dx, c.dy - radius)
        ..quadraticBezierTo(c.dx + k, c.dy - k, c.dx + radius, c.dy)
        ..quadraticBezierTo(c.dx + k, c.dy + k, c.dx, c.dy + radius)
        ..quadraticBezierTo(c.dx - k, c.dy + k, c.dx - radius, c.dy)
        ..quadraticBezierTo(c.dx - k, c.dy - k, c.dx, c.dy - radius)
        ..close(),
      _stroke(color, width: 1.4),
    );
  }

  @override
  bool shouldRepaint(_AiPainter old) => old.color != color;
}

/// 단기 피드백 카드: 채워진 달력(오늘 표시).
class CalendarDayIcon extends StatelessWidget {
  const CalendarDayIcon({super.key});

  @override
  Widget build(BuildContext context) => _icon(context, _CalendarPainter.new);
}

class _CalendarPainter extends CustomPainter {
  const _CalendarPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 24);
    final fill = Paint()..color = color;

    RRect rrect(double l, double t, double r, double b, double radius) =>
        RRect.fromLTRBR(l, t, r, b, Radius.circular(radius));

    for (final x in const [7.5, 14.5]) {
      canvas.drawRRect(rrect(x, 2, x + 2, 6.5, 1), fill);
    }
    canvas.drawRRect(rrect(3, 5, 21, 9.3, 1.8), fill);
    final body = Path()
      ..fillType = PathFillType.evenOdd
      ..addRRect(rrect(3, 10.3, 21, 21.5, 1.8))
      ..addRRect(rrect(6, 13, 11, 18, 1));
    canvas.drawPath(body, fill);
  }

  @override
  bool shouldRepaint(_CalendarPainter old) => old.color != color;
}

/// 장기 피드백 카드: 축 + 오르는 꺾은선.
class GraphUpIcon extends StatelessWidget {
  const GraphUpIcon({super.key});

  @override
  Widget build(BuildContext context) => _icon(context, _GraphPainter.new);
}

class _GraphPainter extends CustomPainter {
  const _GraphPainter(this.color);

  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 24);
    final paint = _stroke(color, width: 1.6)..strokeCap = StrokeCap.square;
    canvas.drawPath(
      Path()
        ..moveTo(3, 3)
        ..lineTo(3, 21)
        ..lineTo(22, 21),
      paint,
    );
    canvas.drawPath(
      Path()
        ..moveTo(7, 17)
        ..lineTo(11.5, 11.5)
        ..lineTo(15, 14.5)
        ..lineTo(21, 7.5),
      _stroke(color, width: 1.6),
    );
  }

  @override
  bool shouldRepaint(_GraphPainter old) => old.color != color;
}
