import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:frontend/api/api_client.dart';
import 'package:frontend/api/medication_api.dart';
import 'package:frontend/main.dart';
import 'package:frontend/screens/home_screen.dart' show StomachGauge;
import 'package:frontend/screens/medication_info_screen.dart';
import 'package:frontend/state/medication_state.dart';
import 'package:frontend/state/user_session.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'support/fake_api.dart';

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
  await _settle(tester);
}

final _popupTitle = find.text('오늘 컨디션 기록');

/// 앱을 띄운다. [withProfile] 이면 프로필을 이미 저장한 사용자로 시작한다
/// (온보딩을 건너뛰고 탭 화면으로). 서버는 [api](기본: mock 고정 응답)다.
Future<FakeApi> _pumpApp(
  WidgetTester tester, {
  bool withProfile = true,
  FakeApi? api,
}) async {
  SharedPreferences.setMockInitialValues(
    withProfile ? {'session.userId': 'test-user'} : {},
  );
  final fake = api ?? FakeApi();
  final session = await UserSession.load();
  await tester.pumpWidget(MyApp(session: session, dio: fake.dio));
  await _settle(tester);
  return fake;
}

/// 가짜 서버 응답과 화면 안 예시(Future.delayed)가 끝나도록 시간을 흘린다.
/// 앱 시작 때 온보딩 확인(`GET /users/me`)이 끝나야 하루 팝업이 뜨고,
/// 팝업도 프리필을 받는다. pumpAndSettle 은 그릴 프레임이 없으면 타이머를
/// 기다리지 않는다.
Future<void> _settle(WidgetTester tester) async {
  await tester.pumpAndSettle();
  await tester.pump(const Duration(seconds: 1));
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
    // 새 사용자: 투약을 저장하기 전까지 MEDICATION_REQUIRED · STAGE_NOT_SET.
    final api = FakeApi();
    Map<String, Object?>? medication;
    Map<String, Object?> me() => {
      'userId': 'new-user',
      'nickname': '민지',
      'heightCm': 175.0,
      'weightKg': 78.4,
      'baselineIntake': 700.0,
      'onboardingStatus': medication == null ? 'MEDICATION_REQUIRED' : 'READY',
    };
    api
      ..on('POST /users/profile', (_) => (201, fakeOk(me())))
      ..on('GET /users/me', (_) => (200, fakeOk(me())))
      ..on('GET /user-states/latest', (_) => (200, fakeOk(null)))
      ..on(
        'GET /medications/current',
        (_) => medication == null
            ? (409, fakeError('STAGE_NOT_SET'))
            : (200, fakeOk(medication)),
      )
      ..on('POST /medications', (o) {
        final body = o.data as Map<String, dynamic>;
        medication = {
          'medicationId': 'm1',
          'drugName': body['drugName'],
          'doseMg': body['doseMg'],
          'startedAt': body['startedAt'],
          'doseCount': 1,
          'nextDoseDate': body['startedAt'],
          'daysUntilNextDose': 7,
          'stage': 'INITIAL',
          'stageReason': '처음 용량에 몸이 적응하는 구간',
        };
        return (200, fakeOk(medication));
      });
    await _pumpApp(tester, withProfile: false, api: api);
    expect(find.text('프로필 입력'), findsOneWidget);

    // 빈 값으로는 넘어가지 않는다.
    await tester.tap(find.text('시작하기'));
    await tester.pumpAndSettle();
    expect(find.text('닉네임을 입력해 주세요'), findsOneWidget);

    final fields = find.byType(TextFormField);
    await tester.enterText(fields.at(0), '민지');
    await tester.enterText(fields.at(1), '175');
    await tester.enterText(fields.at(2), '78.4');
    await tester.enterText(fields.at(3), '700');
    await tester.tap(find.text('시작하기'));
    await _settle(tester);

    // 프로필 다음은 투약 정보 입력이다. 새 사용자라 잠겨 있지 않다.
    expect(find.text('프로필 입력'), findsNothing);
    expect(find.text('투약 정보를 알려주세요'), findsOneWidget);
    expect(find.textContaining('등록 후에는 바꿀 수 없어요'), findsNothing);
    expect(find.text('1회차'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, '저장'));
    await _settle(tester);

    // 홈으로 돌아오면 오늘의 컨디션 팝업이 뜨고, 입력한 체중이 채워져 있다.
    expect(_popupTitle, findsOneWidget);
    expect(find.textContaining('78.4'), findsWidgets);
    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();

    // 홈 투약 카드에 방금 저장한 투약이 보이고, 마이 탭엔 입력한 이름이 보인다.
    expect(find.text('위고비 1.0mg'), findsOneWidget);
    expect(find.text('도입기'), findsOneWidget);
    await tester.tap(find.text('마이'));
    await tester.pumpAndSettle();
    expect(find.text('민지 님'), findsOneWidget);

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

  testWidgets('my tab edits the profile and shows the new values', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    final api = FakeApi();
    final profile = <String, Object?>{
      'userId': 'test-user',
      'nickname': '영우',
      'heightCm': 175.0,
      'weightKg': 78.4,
      'baselineIntake': 700.0,
      'onboardingStatus': 'READY',
    };
    api
      ..on('GET /users/me', (_) => (200, fakeOk(profile)))
      ..on('PATCH /users/me', (o) {
        profile.addAll(o.data as Map<String, dynamic>);
        return (200, fakeOk(profile));
      });
    await _pumpApp(tester, api: api);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();
    await tester.tap(find.text('마이'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('수정'));
    await tester.pumpAndSettle();
    expect(find.text('프로필 수정'), findsOneWidget);
    // 현재 값이 채워져 있다.
    expect(find.widgetWithText(TextFormField, '영우'), findsOneWidget);
    expect(find.widgetWithText(TextFormField, '175'), findsOneWidget);

    await tester.enterText(find.byType(TextFormField).at(0), '종호');
    await tester.tap(find.text('저장'));
    await _settle(tester);

    expect(find.text('프로필 수정'), findsNothing);
    expect(find.text('종호 님'), findsOneWidget);
    // 바뀐 칸만 보낸다.
    final patch = api.requests.singleWhere((r) => r.method == 'PATCH');
    expect(patch.data, {'nickname': '종호'});
  });

  testWidgets('swiping a meal left deletes it after confirming', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();
    await tester.tap(find.text('기록'));
    await _settle(tester);

    final cards = find.byType(Dismissible);
    final before = tester.widgetList(cards).length;
    expect(before, greaterThan(0));

    // 취소하면 그대로 남는다.
    await tester.drag(cards.first, const Offset(-600, 0));
    await tester.pumpAndSettle();
    expect(find.text('식사 기록을 삭제할까요?'), findsOneWidget);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(tester.widgetList(cards).length, before);

    await tester.drag(cards.first, const Offset(-600, 0));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, '삭제'));
    await _settle(tester);
    expect(tester.widgetList(cards).length, before - 1);
  });

  testWidgets('registered medication locks start date and dose count', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    SharedPreferences.setMockInitialValues({'session.userId': 'test-user'});
    final session = await UserSession.load();
    final client = ApiClient(session: session, dio: FakeApi().dio);
    await tester.pumpWidget(
      ChangeNotifierProvider(
        create: (_) =>
            MedicationState(MedicationApiService(client), session: session),
        child: const MaterialApp(home: MedicationInfoScreen()),
      ),
    );
    await _settle(tester); // mock GET /medications/current = 등록됨

    expect(find.textContaining('등록 후에는 바꿀 수 없어요'), findsOneWidget);
    final countText = find.textContaining(RegExp(r'^\d+회차$'));
    final before = tester.widget<Text>(countText.first).data;
    await tester.tap(find.byIcon(Icons.add));
    await tester.pump();
    expect(tester.widget<Text>(countText.first).data, before);
  });

  testWidgets('losing the session closes pushed screens and restarts', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();
    await tester.tap(find.text('마이'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('수정'));
    await tester.pumpAndSettle();
    expect(find.text('프로필 수정'), findsOneWidget);

    // ApiClient 가 USER_NOT_FOUND 를 받았을 때와 같다.
    final context = tester.element(find.text('프로필 수정'));
    await Provider.of<UserSession>(context, listen: false).clear();
    await tester.pumpAndSettle();

    expect(find.text('프로필 수정'), findsNothing);
    expect(find.text('프로필 입력'), findsOneWidget);
    expect(find.text('사용자 정보를 찾지 못해 처음부터 다시 시작해요'), findsOneWidget);
  });

  testWidgets('condition popup stays closed after an app restart', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    final api = await _pumpApp(tester);
    await tester.tap(find.text('보통').first);
    await tester.tap(find.text('없음').last);
    await tester.pump();
    await tester.tap(find.widgetWithText(FilledButton, '기록 저장'));
    await tester.pumpAndSettle();

    // 같은 기기 저장소로 앱을 새로 띄운다.
    await tester.pumpWidget(const SizedBox());
    final session = await UserSession.load();
    await tester.pumpWidget(MyApp(session: session, dio: api.dio));
    await tester.pumpAndSettle();
    expect(_popupTitle, findsNothing);
  });
}
