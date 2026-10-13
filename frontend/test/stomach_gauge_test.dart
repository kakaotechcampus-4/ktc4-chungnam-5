import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/screens/home_screen.dart' show StomachGauge;

Widget _host(int? satiety, {Key? key}) => MaterialApp(
  home: Scaffold(
    body: Center(child: StomachGauge(key: key, satiety: satiety)),
  ),
);

/// 지금 그려지는 수위(0~1). 페인터는 비공개 클래스라 공개 필드만 읽는다.
double _fill(WidgetTester tester) {
  final paint = tester
      .widgetList<CustomPaint>(find.byType(CustomPaint))
      .firstWhere((w) => w.painter.runtimeType.toString() == '_StomachPainter');
  return (paint.painter as dynamic).fillRatio as double;
}

void main() {
  const key = ValueKey('gauge');

  testWidgets('the first value is shown as it is, not filled up from empty', (
    tester,
  ) async {
    await tester.pumpWidget(_host(null, key: key));
    expect(_fill(tester), 0);

    await tester.pumpWidget(_host(60, key: key));
    expect(_fill(tester), closeTo(0.6, 1e-9));
  });

  testWidgets('a higher value rises from the previous one', (tester) async {
    await tester.pumpWidget(_host(30, key: key));
    await tester.pumpWidget(_host(80, key: key));

    // 움직이는 도중에는 이전 값과 새 값 사이에 있다.
    await tester.pump(const Duration(milliseconds: 300));
    final mid = _fill(tester);
    expect(mid, greaterThan(0.3));
    expect(mid, lessThan(0.8));

    await tester.pump(const Duration(seconds: 3));
    expect(_fill(tester), closeTo(0.8, 1e-9));
    expect(tester.takeException(), isNull);
  });

  testWidgets('a lower value goes down from the previous one', (tester) async {
    await tester.pumpWidget(_host(80, key: key));
    await tester.pumpWidget(_host(30, key: key));

    await tester.pump(const Duration(milliseconds: 300));
    final mid = _fill(tester);
    expect(mid, lessThan(0.8));
    expect(mid, greaterThan(0.3));

    await tester.pump(const Duration(seconds: 3));
    expect(_fill(tester), closeTo(0.3, 1e-9));
  });

  testWidgets('an unknown value while reloading keeps the current level', (
    tester,
  ) async {
    await tester.pumpWidget(_host(55, key: key));
    await tester.pumpWidget(_host(null, key: key));
    await tester.pump(const Duration(seconds: 1));
    expect(_fill(tester), closeTo(0.55, 1e-9));

    // 다시 값이 오면 그 55 에서부터 움직인다.
    await tester.pumpWidget(_host(75, key: key));
    await tester.pump(const Duration(milliseconds: 200));
    expect(_fill(tester), greaterThan(0.55));
    expect(_fill(tester), lessThan(0.75));
  });

  testWidgets('reduce motion shows the value without any animation', (
    tester,
  ) async {
    tester.platformDispatcher.accessibilityFeaturesTestValue =
        const FakeAccessibilityFeatures(disableAnimations: true);
    addTearDown(tester.platformDispatcher.clearAccessibilityFeaturesTestValue);

    await tester.pumpWidget(_host(40, key: key));
    await tester.pumpWidget(_host(80, key: key));
    expect(_fill(tester), closeTo(0.8, 1e-9));
    await tester.pumpAndSettle();
    expect(tester.hasRunningAnimations, isFalse);
  });

  testWidgets('animation tickers stop once the gauge leaves the screen', (
    tester,
  ) async {
    await tester.pumpWidget(_host(50, key: key));
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpWidget(const MaterialApp(home: SizedBox.shrink()));
    await tester.pumpAndSettle();
    expect(tester.hasRunningAnimations, isFalse);
  });
}
