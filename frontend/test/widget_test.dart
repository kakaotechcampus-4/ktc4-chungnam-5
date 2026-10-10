import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:frontend/api/api_client.dart';
import 'package:frontend/api/medication_api.dart';
import 'package:frontend/common/api_format.dart';
import 'package:frontend/main.dart';
import 'package:frontend/popups/daily_condition_popup.dart';
import 'package:frontend/screens/home_screen.dart' show StomachGauge;
import 'package:frontend/api/meal_api.dart';
import 'package:frontend/screens/meal_analysis_screen.dart';
import 'package:frontend/screens/meal_evaluation_screen.dart';
import 'package:frontend/screens/meal_review_screen.dart';
import 'package:frontend/screens/medication_info_screen.dart';
import 'package:frontend/screens/next_meal_suggestion_screen.dart';
import 'package:frontend/state/medication_state.dart';
import 'package:frontend/state/user_session.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'support/fake_api.dart';

/// mock 고정 응답 한 건(`mock/examples.json`)의 body.
Map<String, dynamic> _mockBody(String route) {
  final examples =
      jsonDecode(File('mock/examples.json').readAsStringSync())
          as Map<String, dynamic>;
  final byStatus = examples[route] as Map<String, dynamic>;
  return byStatus.values.first as Map<String, dynamic>;
}

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

/// 하단 탭의 피드백을 눌러 분기 팝업에서 [branch]('단기 피드백' · '장기 피드백')를 고른다.
Future<void> _openFeedback(WidgetTester tester, String branch) async {
  await tester.tap(find.text('피드백'));
  await tester.pumpAndSettle();
  await tester.tap(find.text(branch));
  await _settle(tester);
}

void main() {
  testWidgets('bottom nav shows 홈 · 피드백 · AI · 마이 and switches tabs', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);

    // 앱 진입 팝업을 먼저 닫는다.
    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();

    for (final label in ['홈', '피드백', 'AI', '마이']) {
      expect(find.text(label), findsWidgets);
    }
    expect(find.text('기록'), findsNothing);

    await tester.tap(find.text('AI'));
    await tester.pumpAndSettle();

    expect(find.text('AI 코치'), findsOneWidget);
  });

  testWidgets('feedback tab asks short or long, then shows that screen', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();

    // 피드백을 누르면 화면 전환 없이 분기 팝업만 뜬다.
    await tester.tap(find.text('피드백'));
    await tester.pumpAndSettle();
    expect(find.text('단기 피드백'), findsOneWidget);
    expect(find.text('장기 피드백'), findsOneWidget);

    // 단기 피드백 = 예전 기록(달력) 화면.
    await tester.tap(find.text('단기 피드백'));
    await _settle(tester);
    expect(find.text('단기 피드백'), findsNothing);
    expect(find.text('기록'), findsOneWidget);

    // 장기 피드백 = 예전 피드백 탭 화면.
    await _openFeedback(tester, '장기 피드백');
    expect(find.text('장기 피드백'), findsOneWidget);
    expect(find.text('기록'), findsNothing);
  });

  testWidgets('dismissing the feedback popup keeps the current tab', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('피드백'));
    await tester.pumpAndSettle();
    await tester.tapAt(const Offset(10, 10));
    await tester.pumpAndSettle();

    expect(find.text('단기 피드백'), findsNothing);
    expect(find.text('기록'), findsNothing);
  });

  /// 점수가 있는 날이 [day] 하루뿐인 대시보드로 피드백 탭을 열고
  /// 기간을 모두 돌려 본다. 날마다 추이 차트의 날짜 라벨이 보여야 한다.
  Future<void> expectFeedbackTabDrawsOneDay(
    WidgetTester tester,
    Map<String, Object?> day,
  ) async {
    _useDesignSize(tester);
    final api = FakeApi();
    api.on('GET /dashboard', (o) {
      final data = _mockBody('GET /api/v1/dashboard')['data'] as Map;
      final mockDay = (data['series'] as List).last as Map;
      // `_mockBody` 는 `{{today}}` 자리표시자를 그대로 두어 날짜를 채운다.
      final series = [
        {...mockDay, 'date': '2026-10-07', ...day},
      ];
      return (200, fakeOk({...data, 'series': series}));
    });
    await _pumpApp(tester, api: api);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();

    await _openFeedback(tester, '장기 피드백');
    for (final period in const ['7일', '28일', '전체']) {
      await tester.tap(find.text(period));
      await _settle(tester);
      expect(tester.takeException(), isNull, reason: period);
      expect(find.text('10/07'), findsOneWidget, reason: period);
    }
  }

  testWidgets('feedback tab draws the trend with only one scored day', (
    WidgetTester tester,
  ) async {
    // 신규 사용자처럼 점수가 있는 날이 하루뿐이다.
    await expectFeedbackTabDrawsOneDay(tester, {});
  });

  testWidgets('feedback tab draws the trend when amount and satiety are 0', (
    WidgetTester tester,
  ) async {
    // 막대·선 높이의 기준(최댓값)이 0 이 된다.
    await expectFeedbackTabDrawsOneDay(tester, {'quantity': 0, 'satiety': 0});
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

  testWidgets('weight can be typed and out-of-range blocks saving', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    final api = await _pumpApp(tester);
    await tester.tap(find.text('보통').first); // 식욕
    await tester.tap(find.text('없음').last); // GI 증상
    await tester.pump();

    final weight = find.descendant(
      of: find.byType(DailyConditionPopup),
      matching: find.byType(TextField),
    );
    final save = find.widgetWithText(FilledButton, '기록 저장');

    await tester.enterText(weight, '5');
    await tester.pump();
    expect(find.textContaining('kg 사이로 적어 주세요'), findsOneWidget);
    expect(tester.widget<FilledButton>(save).onPressed, isNull);

    await tester.enterText(weight, '81.3');
    await tester.pump();
    expect(tester.widget<FilledButton>(save).onPressed, isNotNull);
    await tester.tap(save);
    await tester.pumpAndSettle();
    final post = api.requests.singleWhere((r) => r.path == '/user-states');
    expect((post.data as Map)['weightKg'], 81.3);
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
    // 3시간 전에 먹은 끼니. 고정 시각이면 경과 시간이 실행 시각마다 달라진다.
    final api = FakeApi();
    final eatenAt = formatApiDateTime(
      DateTime.now().subtract(const Duration(hours: 3)),
    );
    api.on('GET /meals/{meal_id}', (o) {
      final meal = {
        ...(_mockBody('GET /api/v1/meals/{meal_id}')['data'] as Map),
        'eatenAt': eatenAt,
      };
      return (200, fakeOk(meal));
    });
    await _pumpApp(tester, api: api);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();

    await tester.tap(find.byType(StomachGauge));
    await tester.pumpAndSettle();
    expect(find.text('지금 얼마나 부르세요?'), findsOneWidget);
    // 게이지(mock 68%)에서 시작한다.
    expect(find.widgetWithText(TextField, '68'), findsOneWidget);
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
    expect(find.textContaining('용량을 바꾼 기록이 있어'), findsNothing);
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
    await _openFeedback(tester, '단기 피드백');

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

    // mock 은 용량을 두 번 바꾼 기록이 있어 시작일을 고칠 수 없다.
    expect(find.textContaining('용량을 바꾼 기록이 있어'), findsOneWidget);
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

  /// 식사 흐름 화면 하나를 가짜 서버로 띄운다.
  Future<FakeApi> pumpMealScreen(WidgetTester tester, Widget screen) async {
    _useDesignSize(tester);
    SharedPreferences.setMockInitialValues({'session.userId': 'test-user'});
    final session = await UserSession.load();
    final api = FakeApi();
    await tester.pumpWidget(
      Provider(
        create: (_) => ApiClient(session: session, dio: api.dio),
        child: MaterialApp(home: screen),
      ),
    );
    await _settle(tester);
    return api;
  }

  testWidgets('review sends amount edits together after a pause', (
    WidgetTester tester,
  ) async {
    final api = await pumpMealScreen(
      tester,
      const MealReviewScreen(mealId: 'm1'),
    );
    expect(find.text('250g'), findsOneWidget);
    // 매칭 안 된 음식은 후보를 고르라고 안내한다.
    expect(find.text('영양정보 없음 · 찾아서 고르기'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.add).first);
    await tester.tap(find.byIcon(Icons.add).first);
    await tester.pump();
    expect(find.text('270g'), findsOneWidget);
    expect(api.requests.where((r) => r.method == 'PATCH'), isEmpty);

    await tester.pump(const Duration(seconds: 1));
    final patch = api.requests.singleWhere((r) => r.method == 'PATCH');
    final items = (patch.data as Map)['items'] as List;
    expect(items.single['amount'], 270.0);
  });

  testWidgets('a failed amount edit goes back to the saved amount', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    final api = await pumpMealScreen(
      tester,
      const MealReviewScreen(mealId: 'm1'),
    );
    api.on(
      'PATCH /meals/{meal_id}/items',
      (_) => (500, fakeError('INTERNAL_ERROR')),
    );
    await tester.tap(find.byIcon(Icons.add).first);
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.text('250g'), findsOneWidget);
    expect(find.textContaining('되돌렸어요'), findsOneWidget);
  });

  testWidgets('review moves on without confirming', (
    WidgetTester tester,
  ) async {
    final api = await pumpMealScreen(
      tester,
      const MealReviewScreen(mealId: 'm1'),
    );
    expect(find.text('식사 후 포만감'), findsNothing);
    await tester.scrollUntilVisible(find.text('확인하고 평가받기'), 100);
    await tester.tap(find.text('확인하고 평가받기'));
    await _settle(tester);

    expect(api.requests.where((r) => r.path.endsWith('/confirm')), isEmpty);
    expect(find.text('식사 평가'), findsOneWidget);
    expect(find.text('포만감을 입력하면 계산돼요'), findsNWidgets(2));
  });

  testWidgets('evaluation confirms with the satiety and shows the scores', (
    WidgetTester tester,
  ) async {
    final api = await pumpMealScreen(
      tester,
      const MealEvaluationScreen(mealId: 'm1'),
    );
    await tester.tap(find.text('평가 받기'));
    await _settle(tester);

    final confirm = api.requests.singleWhere(
      (r) => r.path.endsWith('/confirm'),
    );
    expect(confirm.data, {'satietyAfterPct': 50});
    expect(find.text('76'), findsOneWidget); // 양
    expect(find.text('80'), findsOneWidget); // 질
    expect(find.textContaining('단백질 부족'), findsOneWidget);
    expect(find.text('다음 끼니 제안 보기'), findsOneWidget);
  });

  testWidgets('home meal card opens the next meal suggestion', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    await _pumpApp(tester);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();

    await tester.tap(find.text('현미밥, 된장국, 두부조림'));
    await _settle(tester);
    expect(find.text('현재 영양소 상태'), findsOneWidget);
    expect(find.text('두부 반 모'), findsOneWidget);
    // 그날 하루 요약도 같이 보인다.
    await tester.scrollUntilVisible(find.text('오늘 하루 요약'), 200);
    expect(find.textContaining('단백질이 고르게 들어간 하루'), findsOneWidget);
  });

  testWidgets('a missing daily summary is requested, then shown', (
    WidgetTester tester,
  ) async {
    var made = false;
    Map<String, Object?> daily() => {
      'dailyFeedbackId': made ? 'd1' : null,
      'feedbackDate': '2026-10-05',
      'feedbackStatus': made ? 'READY' : 'PENDING',
      'summary': made ? '하루 요약 문장' : null,
      'stale': false,
    };
    _useDesignSize(tester);
    SharedPreferences.setMockInitialValues({'session.userId': 'test-user'});
    final session = await UserSession.load();
    final api = FakeApi()
      ..on('GET /insights/daily', (_) => (200, fakeOk(daily())))
      ..on('POST /insights/daily/refresh', (_) {
        made = true;
        return (
          202,
          fakeOk({'feedbackStatus': 'GENERATING', 'pollIntervalMs': 100}),
        );
      });
    await tester.pumpWidget(
      Provider(
        create: (_) => ApiClient(session: session, dio: api.dio),
        child: MaterialApp(
          home: NextMealSuggestionScreen(
            mealId: 'm1',
            eatenAt: DateTime(2026, 10, 5, 12),
          ),
        ),
      ),
    );
    await _settle(tester);
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    final refresh = api.requests.singleWhere(
      (r) => r.path == '/insights/daily/refresh',
    );
    expect(refresh.data, {'date': '2026-10-05'});
    await tester.scrollUntilVisible(find.text('하루 요약 문장'), 200);
    expect(find.text('하루 요약 문장'), findsOneWidget);
  });

  testWidgets('changing the satiety on evaluation confirms again', (
    WidgetTester tester,
  ) async {
    final api = await pumpMealScreen(
      tester,
      const MealEvaluationScreen(mealId: 'm1'),
    );
    // 확정 전에는 슬라이더를 움직여도 보내지 않는다.
    await tester.drag(find.byType(Slider), const Offset(-600, 0));
    await _settle(tester);
    expect(api.requests.where((r) => r.path.endsWith('/confirm')), isEmpty);

    await tester.tap(find.text('평가 받기'));
    await _settle(tester);
    // 평가 뒤(mock 포만감 68) 끝까지 끌면 100 으로 다시 확정한다.
    await tester.drag(find.byType(Slider), const Offset(600, 0));
    await _settle(tester);
    final confirms = api.requests
        .where((r) => r.path.endsWith('/confirm'))
        .toList();
    expect(confirms.map((r) => r.data), [
      {'satietyAfterPct': 0},
      {'satietyAfterPct': 100},
    ]);
  });

  testWidgets('history reloads when its tab is opened again', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    final api = await _pumpApp(tester);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();

    int mealLists() => api.requests.where((r) => r.path == '/meals').length;
    final before = mealLists();
    await _openFeedback(tester, '단기 피드백');
    expect(mealLists(), greaterThan(before));
  });

  testWidgets('insight with too little data is asked only once', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    final api = FakeApi()
      ..on(
        'GET /insights/long-term',
        (_) => (
          200,
          fakeOk({
            'period': {'from': null, 'to': '2026-10-05'},
            'status': 'PENDING',
            'dataSufficient': false,
            'trendSummary': null,
            'recommendation': null,
            'generatedAt': null,
            'stale': false,
            'staleReason': null,
          }),
        ),
      );
    await _pumpApp(tester, api: api);
    await tester.tap(find.text('나중에')); // 컨디션 팝업
    await tester.pumpAndSettle();
    int asks() =>
        api.requests.where((r) => r.path == '/insights/long-term').length;
    final before = asks(); // 앱을 켤 때 한 번
    await _openFeedback(tester, '장기 피드백');
    await tester.pump(const Duration(seconds: 5));

    // 탭을 열 때 한 번만 묻고, 부족하면 더 기다리지 않는다.
    expect(asks() - before, 1);
    expect(api.requests.where((r) => r.path.endsWith('/refresh')), isEmpty);
    expect(find.textContaining('3일 이상 쌓이면'), findsOneWidget);
  });
  testWidgets('a blocked meal feedback shows the counselling notice', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    SharedPreferences.setMockInitialValues({'session.userId': 'test-user'});
    final session = await UserSession.load();
    final api = FakeApi()
      ..on(
        'GET /meals/{meal_id}/feedback',
        (_) => (
          200,
          {
            'success': true,
            'data': {
              'feedbackStatus': 'READY',
              'summary': null,
              'reasoning': null,
              'suggestions': [],
              'expectedSatietyPct': null,
              'safetyStatus': 'BLOCKED',
            },
            'error': {'code': 'MEDICAL_QUESTION_DETECTED', 'message': ''},
          },
        ),
      );
    await tester.pumpWidget(
      Provider(
        create: (_) => ApiClient(session: session, dio: api.dio),
        child: MaterialApp(
          home: NextMealSuggestionScreen(
            mealId: 'm1',
            eatenAt: DateTime(2026, 10, 5, 12),
          ),
        ),
      ),
    );
    await _settle(tester);
    expect(find.textContaining('담당 의사와 상담'), findsOneWidget);
    expect(
      api.requests.where((r) => r.path.endsWith('/feedback')),
      hasLength(1),
    );
  });

  testWidgets('after an amount edit the nutrition line shows the new value', (
    WidgetTester tester,
  ) async {
    var patched = false;
    final api = await pumpMealScreen(
      tester,
      const MealReviewScreen(mealId: 'm1'),
    );
    final meal = Map<String, dynamic>.from(
      _mockBody('GET /api/v1/meals/{meal_id}')['data'] as Map,
    )..['eatenAt'] = '2026-10-06T12:40:00+09:00';
    api
      ..on('PATCH /meals/{meal_id}/items', (_) {
        patched = true;
        return (200, fakeOk({'status': 'ANALYZING', 'isRecalculation': true}));
      })
      ..on('GET /meals/{meal_id}', (_) {
        if (!patched) return (200, fakeOk(meal));
        final items = [
          for (final i in meal['items'] as List)
            {
              ...(i as Map<String, dynamic>),
              if (i['itemId'] == (meal['items'] as List).first['itemId'])
                'nutrition': {'kcal': 800, 'proteinG': 24.4, 'fiberG': null},
            },
        ];
        return (200, fakeOk({...meal, 'items': items}));
      });
    expect(find.textContaining('400kcal'), findsOneWidget);

    await tester.tap(find.byIcon(Icons.add).first);
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();
    expect(find.textContaining('800kcal'), findsOneWidget);
    expect(find.text('260g'), findsOneWidget);
  });

  testWidgets('a failed analysis offers recording again', (
    WidgetTester tester,
  ) async {
    _useDesignSize(tester);
    SharedPreferences.setMockInitialValues({'session.userId': 'test-user'});
    final session = await UserSession.load();
    final api = FakeApi()
      ..on(
        'GET /meals/{meal_id}',
        (_) => (
          200,
          fakeOk({
            ...(_mockBody('GET /api/v1/meals/{meal_id}')['data'] as Map),
            'eatenAt': '2026-10-06T12:40:00+09:00',
            'status': 'FAILED',
            'items': [],
          }),
        ),
      );
    await tester.pumpWidget(
      Provider(
        create: (_) => ApiClient(session: session, dio: api.dio),
        child: const MaterialApp(
          home: MealAnalysisScreen(
            meal: MealCreated(
              mealId: 'm1',
              pollInterval: Duration(milliseconds: 100),
              timeout: Duration(seconds: 5),
            ),
          ),
        ),
      ),
    );
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pumpAndSettle();
    expect(find.text('음식을 알아보지 못했어요'), findsOneWidget);

    await tester.tap(find.text('다시 기록하기'));
    await tester.pumpAndSettle();
    expect(find.text('식사 기록'), findsOneWidget);
  });
}
