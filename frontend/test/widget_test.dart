import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:frontend/main.dart';
import 'package:frontend/screens/home_screen.dart' show StomachGauge;
import 'package:frontend/state/user_session.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 기본 테스트 화면(800×600)은 팝업 아래쪽 버튼이 잘려 세로를 늘린다.
/// 폭은 줄이지 않는다 — 테스트 글꼴(Ahem)은 실제 글꼴보다 넓어서 375 폭에서는
/// 기존 화면의 Row 가 넘친다.
void _useDesignSize(WidgetTester tester) {
  tester.view.physicalSize = const Size(800, 1200);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

/// 백그라운드로 갔다가 돌아오는 것을 흉내 낸다.
Future<void> _backgroundAndResume(WidgetTester tester) async {
  for (final state in const [
    AppLifecycleState.inactive,
    AppLifecycleState.hidden,
    AppLifecycleState.paused,
    AppLifecycleState.hidden,
    AppLifecycleState.inactive,
    AppLifecycleState.resumed,
  ]) {
    tester.binding.handleAppLifecycleStateChanged(state);
  }
  await tester.pumpAndSettle();
  // 팝업 프리필 더미 API(Future.delayed)가 끝나도록 시간을 더 흘린다.
  // pumpAndSettle 은 그릴 프레임이 없으면 타이머를 기다리지 않는다.
  await tester.pump(const Duration(seconds: 1));
}

final _popupTitle = find.text('오늘 컨디션 기록');

/// 앱을 띄운다. [withProfile] 이면 프로필을 이미 저장한 사용자로 시작한다
/// (온보딩을 건너뛰고 탭 화면으로).
Future<void> _pumpApp(WidgetTester tester, {bool withProfile = true}) async {
  SharedPreferences.setMockInitialValues(
    withProfile ? {'session.userId': 'test-user'} : {},
  );
  final session = await UserSession.load();
  await tester.pumpWidget(MyApp(session: session));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('bottom nav switches between the four tabs', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);

    // 앱 진입 팝업을 먼저 닫는다.
    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();

    expect(find.text('홈'), findsWidgets);

    await tester.tap(find.text('기록'));
    await tester.pumpAndSettle();

    expect(find.text('기록'), findsWidgets);
  });

  testWidgets('condition popup shows on launch and again after "later"', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    expect(_popupTitle, findsOneWidget);

    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();
    expect(_popupTitle, findsNothing);

    await _backgroundAndResume(tester);
    expect(_popupTitle, findsOneWidget);
  });

  testWidgets('condition popup does not show again after saving', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);

    final save = find.widgetWithText(FilledButton, '기록 저장');
    expect(tester.widget<FilledButton>(save).onPressed, isNull);

    await tester.tap(find.text('보통').first); // 식욕
    await tester.tap(find.text('없음').last); // GI 증상
    await tester.pump();
    expect(tester.widget<FilledButton>(save).onPressed, isNotNull);

    await tester.tap(save);
    await tester.pumpAndSettle();
    expect(_popupTitle, findsNothing);

    await _backgroundAndResume(tester);
    expect(_popupTitle, findsNothing);
  });

  testWidgets('symptom needs a severity before saving', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);

    final save = find.widgetWithText(FilledButton, '기록 저장');
    await tester.tap(find.text('보통').first); // 식욕
    await tester.tap(find.text('메스꺼움'));
    await tester.pump();
    expect(find.text('증상 강도'), findsOneWidget);
    expect(tester.widget<FilledButton>(save).onPressed, isNull);

    await tester.tap(find.text('심함'));
    await tester.pump();
    expect(tester.widget<FilledButton>(save).onPressed, isNotNull);
  });

  testWidgets('tapping the stomach gauge opens the satiety check-in', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();

    await tester.tap(find.byType(StomachGauge));
    await tester.pumpAndSettle();
    expect(find.text('지금 얼마나 부르세요?'), findsOneWidget);
    expect(find.textContaining('3시간 경과'), findsOneWidget);

    final save = find.widgetWithText(FilledButton, '기록 저장');
    expect(tester.widget<FilledButton>(save).onPressed, isNull);

    // 직접 입력은 0~100 으로 잘린다.
    await tester.enterText(find.byType(TextField), '120');
    await tester.pump();
    expect(find.text('100'), findsOneWidget);

    await tester.tap(find.text('1시간 전'));
    await tester.pump();
    expect(tester.widget<FilledButton>(save).onPressed, isNotNull);

    await tester.tap(save);
    await tester.pumpAndSettle();
    expect(find.text('지금 얼마나 부르세요?'), findsNothing);
  });

  testWidgets('first launch starts with profile input, then shows tabs', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester, withProfile: false);
    expect(find.text('프로필 입력'), findsOneWidget);

    // 빈 값으로는 넘어가지 않는다.
    await tester.tap(find.text('시작하기'));
    await tester.pumpAndSettle();
    expect(find.text('닉네임을 입력해 주세요'), findsOneWidget);

    final fields = find.byType(TextFormField);
    await tester.enterText(fields.at(0), '영우');
    await tester.enterText(fields.at(1), '175');
    await tester.enterText(fields.at(2), '78.4');
    await tester.enterText(fields.at(3), '700');
    await tester.tap(find.text('시작하기'));
    await tester.pumpAndSettle();

    // 탭 화면으로 넘어가면 오늘의 컨디션 팝업이 뜬다.
    expect(find.text('프로필 입력'), findsNothing);
    expect(_popupTitle, findsOneWidget);

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('session.userId'), isNotNull);
  });

  testWidgets('my tab shows member info', (WidgetTester tester) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();

    await tester.tap(find.text('마이'));
    await tester.pumpAndSettle();
    expect(find.text('영우 님'), findsOneWidget);
    expect(find.text('175cm'), findsOneWidget);
    expect(find.text('700kcal'), findsOneWidget);
  });

  testWidgets('condition popup stays closed after an app restart', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('보통').first);
    await tester.tap(find.text('없음').last);
    await tester.pump();
    await tester.tap(find.widgetWithText(FilledButton, '기록 저장'));
    await tester.pumpAndSettle();

    // 같은 기기 저장소로 앱을 새로 띄운다.
    await tester.pumpWidget(const SizedBox());
    final session = await UserSession.load();
    await tester.pumpWidget(MyApp(session: session));
    await tester.pumpAndSettle();
    expect(_popupTitle, findsNothing);
  });
}
