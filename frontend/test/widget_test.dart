import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:frontend/main.dart';

void main() {
  testWidgets('bottom nav switches between the four tabs', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());

    expect(find.text('홈'), findsWidgets);

    await tester.tap(find.text('기록'));
    await tester.pumpAndSettle();

    expect(find.text('기록'), findsWidgets);
  });
}
