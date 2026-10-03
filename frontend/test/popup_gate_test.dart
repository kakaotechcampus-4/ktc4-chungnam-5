import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/popups/popup_gate.dart';
import 'package:shared_preferences/shared_preferences.dart';

final _dailyTitle = find.text('오늘 컨디션 기록');
final _satietyTitle = find.text('지금 얼마나 부르세요?');

/// 게이트만 올린 빈 화면을 띄우고, 팝업을 띄울 context 를 돌려준다.
Future<BuildContext> _pumpHost(WidgetTester tester) async {
  tester.view.physicalSize = const Size(800, 1200);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  SharedPreferences.setMockInitialValues({});

  late BuildContext hostContext;
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (context) {
          hostContext = context;
          return const Scaffold();
        },
      ),
    ),
  );
  return hostContext;
}

/// 팝업이 뜨고 더미 API(Future.delayed)가 끝날 때까지 시간을 흘린다.
Future<void> _settle(WidgetTester tester) async {
  await tester.pumpAndSettle();
  await tester.pump(const Duration(seconds: 1));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('requests in the same frame show by priority, one at a time', (
    WidgetTester tester,
  ) async {
    final context = await _pumpHost(tester);
    final gate = PopupGate();

    // 앱 시작 때 하루 팝업이 먼저 요청돼도, 같은 프레임의 포만감(알림 진입)이
    // 먼저 뜬다.
    gate.showDailyPopupsIfDue(context);
    final satiety = gate.showSatietyCheckin(context, mealId: 'meal_1');
    await _settle(tester);
    expect(_satietyTitle, findsOneWidget);
    expect(_dailyTitle, findsNothing);

    await tester.tap(find.text('나중에'));
    await _settle(tester);
    expect(await satiety, isFalse);
    expect(_satietyTitle, findsNothing);
    expect(_dailyTitle, findsOneWidget);
  });

  testWidgets('a popup already showing is not replaced', (
    WidgetTester tester,
  ) async {
    final context = await _pumpHost(tester);
    final gate = PopupGate();

    gate.showDailyPopupsIfDue(context);
    await _settle(tester);
    expect(_dailyTitle, findsOneWidget);

    gate.showSatietyCheckin(context, mealId: 'meal_1');
    await _settle(tester);
    expect(_dailyTitle, findsOneWidget);
    expect(_satietyTitle, findsNothing);

    await tester.tap(find.text('나중에'));
    await _settle(tester);
    expect(_satietyTitle, findsOneWidget);
  });

  testWidgets('the same request does not stack', (WidgetTester tester) async {
    final context = await _pumpHost(tester);
    final gate = PopupGate();

    final first = gate.showSatietyCheckin(context, mealId: 'meal_1');
    final second = gate.showSatietyCheckin(context, mealId: 'meal_1');
    gate.showDailyPopupsIfDue(context);
    await _settle(tester);
    gate.showDailyPopupsIfDue(context); // 떠 있는 동안 복귀 이벤트
    await _settle(tester);
    expect(identical(first, second), isTrue);

    await tester.tap(find.text('나중에'));
    await _settle(tester);
    expect(_satietyTitle, findsNothing);
    expect(_dailyTitle, findsOneWidget);

    await tester.tap(find.text('나중에'));
    await _settle(tester);
    expect(_dailyTitle, findsNothing);
    expect(gate.isShowing, isFalse);
  });

  testWidgets('daily popup waits while another screen is on top', (
    WidgetTester tester,
  ) async {
    final context = await _pumpHost(tester);
    final gate = PopupGate();

    // 식사 기록 흐름처럼 다른 화면이 위에 떠 있다.
    Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => const Scaffold()));
    await tester.pumpAndSettle();
    gate.showDailyPopupsIfDue(context);
    await _settle(tester);
    expect(_dailyTitle, findsNothing);

    // 돌아온 뒤 다음 요청(복귀 이벤트)에는 뜬다.
    Navigator.of(context).pop();
    await tester.pumpAndSettle();
    gate.showDailyPopupsIfDue(context);
    await _settle(tester);
    expect(_dailyTitle, findsOneWidget);
  });

  testWidgets('daily popup is skipped once completed today', (
    WidgetTester tester,
  ) async {
    final context = await _pumpHost(tester);
    final gate = PopupGate();
    await gate.markCompletedToday(AppPopup.dailyCondition);

    gate.showDailyPopupsIfDue(context);
    await _settle(tester);
    expect(_dailyTitle, findsNothing);
    expect(gate.isShowing, isFalse);
  });
}
