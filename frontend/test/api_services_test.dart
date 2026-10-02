import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/api/api_client.dart';
import 'package:frontend/api/api_exception.dart';
import 'package:frontend/api/medication_api.dart';
import 'package:frontend/api/user_api.dart';
import 'package:frontend/popups/daily_condition_popup.dart';
import 'package:frontend/screens/long_term_feedback_screen.dart';
import 'package:frontend/screens/meal_history_screen.dart';
import 'package:frontend/state/medication_state.dart';
import 'package:frontend/state/user_session.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 각 화면 API 서비스가 보내는 요청과 응답 해석을 서버 응답 예시로 확인한다.

/// 요청마다 [respond] 로 응답을 만들고, 받은 요청을 [requests] 에 남긴다.
class _RoutingAdapter implements HttpClientAdapter {
  _RoutingAdapter(this.respond);

  final (int, Object?) Function(RequestOptions options) respond;
  final List<RequestOptions> requests = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    final (status, body) = respond(options);
    return ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

Map<String, Object?> _ok(Object? data) => {
  'success': true,
  'data': data,
  'error': null,
};

Map<String, Object?> _error(String code) => {
  'success': false,
  'data': null,
  'error': {'code': code, 'message': ''},
};

Future<(ApiClient, _RoutingAdapter)> _client(
  (int, Object?) Function(RequestOptions options) respond,
) async {
  SharedPreferences.setMockInitialValues({'session.userId': 'user-1'});
  final session = await UserSession.load();
  final adapter = _RoutingAdapter(respond);
  final dio = Dio(BaseOptions(baseUrl: 'http://test/api/v1'))
    ..httpClientAdapter = adapter;
  return (ApiClient(session: session, dio: dio), adapter);
}

Map<String, Object?> _listItem(String id, String eatenAt, {Object? scores}) => {
  'mealId': id,
  'mealType': 'LUNCH',
  'eatenAt': eatenAt,
  'stage': 'MAINTENANCE',
  'displayName': '현미밥',
  'thumbnailUrl': null,
  'scores': scores,
};

/// `GET /medications/current` 응답(`data` 안쪽).
Map<String, Object?> _medication({
  String drugName = '위고비',
  double doseMg = 1.0,
  int doseCount = 4,
  String stage = 'INITIAL',
}) => {
  'medicationId': 'm1',
  'drugName': drugName,
  'doseMg': doseMg,
  'startedAt': '2026-09-01',
  'doseCount': doseCount,
  'nextDoseDate': '2026-09-29',
  'daysUntilNextDose': 1,
  'stage': stage,
  'stageReason': '',
};

Future<MedicationState> _medicationState(ApiClient client) async =>
    MedicationState(MedicationApiService(client), session: await UserSession.load());

void main() {
  group('api client', () {
    test('USER_NOT_FOUND clears the saved user and still throws', () async {
      final (client, _) = await _client((_) => (404, _error('USER_NOT_FOUND')));
      await expectLater(
        client.get('/users/me'),
        throwsA(
          isA<ApiException>().having(
            (e) => e.code,
            'code',
            ApiException.userNotFound,
          ),
        ),
      );
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('session.userId'), isNull);
    });

    test('other 404 codes keep the saved user', () async {
      final (client, _) = await _client((_) => (404, _error('NOT_FOUND')));
      await expectLater(client.get('/meals/x'), throwsA(isA<ApiException>()));
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('session.userId'), 'user-1');
    });
  });

  group('user', () {
    const profile = {
      'userId': 'user-1',
      'nickname': '영우',
      'heightCm': 175.0,
      'weightKg': 78.4,
      'baselineIntake': 700.0,
      'onboardingStatus': 'MEDICATION_REQUIRED',
      'createdAt': '2026-09-29T09:00:00+09:00',
    };

    test('sign-up posts the profile and reads the onboarding status', () async {
      final (client, adapter) = await _client((_) => (201, _ok(profile)));
      final created = await UserApiService(client).createProfile(
        nickname: '영우',
        heightCm: 175,
        weightKg: 78.4,
        baselineIntake: 700,
      );
      expect(created.userId, 'user-1');
      expect(created.onboardingStatus, 'MEDICATION_REQUIRED');
      final request = adapter.requests.single;
      expect(request.method, 'POST');
      expect(request.path, '/users/profile');
      expect(request.data, {
        'nickname': '영우',
        'heightCm': 175,
        'weightKg': 78.4,
        'baselineIntake': 700,
      });
    });

    test('me reads the profile', () async {
      final (client, adapter) = await _client(
        (_) => (200, _ok({...profile, 'onboardingStatus': 'READY'})),
      );
      final me = await UserApiService(client).fetchMe();
      expect(me.nickname, '영우');
      expect(me.onboardingStatus, 'READY');
      expect(adapter.requests.single.path, '/users/me');
    });

    test('profile update sends only the changed fields', () async {
      final (client, adapter) = await _client(
        (_) => (
          200,
          _ok({
            'userId': 'user-1',
            'nickname': '영우',
            'heightCm': 175.0,
            'weightKg': 77.9,
            'baselineIntake': 700.0,
            'onboardingStatus': 'READY',
          }),
        ),
      );
      final updated = await UserApiService(client).updateProfile(weightKg: 77.9);
      expect(updated.weightKg, 77.9);
      final request = adapter.requests.single;
      expect(request.method, 'PATCH');
      expect(request.path, '/users/me');
      expect(request.data, {'weightKg': 77.9});
    });
  });

  group('medication', () {
    test('not registered yet (STAGE_NOT_SET) is null, not a failure', () async {
      final (client, _) = await _client((_) => (409, _error('STAGE_NOT_SET')));
      expect(await MedicationApiService(client).fetchCurrent(), isNull);
    });

    test('save posts drug, dose and start date', () async {
      final (client, adapter) = await _client(
        (_) => (
          200,
          _ok({
            'medicationId': 'm1',
            'drugName': '위고비',
            'doseMg': 0.5,
            'startedAt': '2026-09-01',
            'doseCount': 4,
            'nextDoseDate': '2026-09-29',
            'daysUntilNextDose': 1,
            'stage': 'INITIAL',
            'stageReason': '',
            'ruleVersion': 'v1',
            'doseChanged': true,
            'doseEvent': null,
            'stageChanged': true,
            'decidedAt': '2026-09-28T09:00:00+09:00',
          }),
        ),
      );
      final saved = await MedicationApiService(
        client,
      ).save(drugName: '위고비', doseMg: 0.5, startedAt: DateTime(2026, 9, 1));
      expect(saved.doseCount, 4);
      final request = adapter.requests.single;
      expect(request.method, 'POST');
      expect(request.path, '/medications');
      expect(request.data, {
        'drugName': '위고비',
        'doseMg': 0.5,
        'startedAt': '2026-09-01',
      });
    });

    test('after registration the start date is left out', () async {
      final (client, adapter) = await _client(
        (_) => (
          200,
          _ok({
            'medicationId': 'm2',
            'drugName': '위고비',
            'doseMg': 1.0,
            'startedAt': '2026-09-01',
            'doseCount': 4,
            'nextDoseDate': '2026-09-29',
            'daysUntilNextDose': 1,
            'stage': 'TITRATION',
            'stageReason': '',
            'ruleVersion': 'v1',
            'doseChanged': true,
            'doseEvent': null,
            'stageChanged': true,
            'decidedAt': '2026-09-28T09:00:00+09:00',
          }),
        ),
      );
      await MedicationApiService(client).save(drugName: '위고비', doseMg: 1.0);
      expect(adapter.requests.single.data, {'drugName': '위고비', 'doseMg': 1.0});
    });
  });

  group('medication state', () {
    test('screens asking at once share one request', () async {
      final (client, adapter) = await _client((_) => (200, _ok(_medication())));
      final state = await _medicationState(client);
      await Future.wait([state.ensureLoaded(), state.ensureLoaded()]);
      expect(adapter.requests, hasLength(1));
      // 받아 둔 뒤에는 서버에 다시 묻지 않는다.
      await state.ensureLoaded();
      expect(adapter.requests, hasLength(1));
      expect(state.current?.drugName, '위고비');
    });

    test('asks again once the day has changed', () async {
      final (client, adapter) = await _client((_) => (200, _ok(_medication())));
      var now = DateTime(2026, 10, 2, 23, 50);
      final state = MedicationState(
        MedicationApiService(client),
        session: await UserSession.load(),
        clock: () => now,
      );
      await state.ensureLoaded();
      await state.ensureLoaded();
      expect(adapter.requests, hasLength(1));
      now = DateTime(2026, 10, 3, 0, 10);
      await state.ensureLoaded();
      expect(adapter.requests, hasLength(2));
    });

    test('not registered is loaded with no value', () async {
      final (client, _) = await _client((_) => (409, _error('STAGE_NOT_SET')));
      final state = await _medicationState(client);
      expect(state.isLoaded, isFalse);
      await state.ensureLoaded();
      expect(state.isLoaded, isTrue);
      expect(state.current, isNull);
    });

    test('saving replaces the value and tells the listeners', () async {
      final (client, _) = await _client(
        (o) => o.method == 'POST'
            ? (200, _ok(_medication(doseMg: 0.5)))
            : (409, _error('STAGE_NOT_SET')),
      );
      final state = await _medicationState(client);
      await state.ensureLoaded();
      var notified = 0;
      state.addListener(() => notified++);
      await state.save(drugName: '위고비', doseMg: 0.5);
      expect(state.current?.doseMg, 0.5);
      expect(notified, 1);
    });

    test('a new user starts empty', () async {
      final (client, _) = await _client((_) => (200, _ok(_medication())));
      final session = await UserSession.load();
      final state = MedicationState(
        MedicationApiService(client),
        session: session,
      );
      await state.ensureLoaded();
      await session.clear();
      expect(state.isLoaded, isFalse);
      expect(state.current, isNull);
    });
  });

  group('condition popup', () {
    test('first-time user: no medication, no state → profile weight', () async {
      final (client, _) = await _client(
        (o) => switch (o.path) {
          '/medications/current' => (409, _error('STAGE_NOT_SET')),
          '/user-states/latest' => (200, _ok(null)),
          '/users/me' => (200, _ok({'weightKg': 80.5})),
          _ => (404, _error('NOT_FOUND')),
        },
      );
      final prefill = await ConditionApiService(
        client,
      ).fetchPrefill(await _medicationState(client));
      expect(prefill.drugName, isNull);
      expect(prefill.weightKg, 80.5);
      expect(prefill.weeklyWeightDeltaKg, isNull);
    });

    test('uses the latest state weight and weekly change', () async {
      final (client, adapter) = await _client(
        (o) => switch (o.path) {
          '/medications/current' => (
            200,
            _ok(
              _medication(
                drugName: '마운자로',
                doseMg: 5.0,
                doseCount: 3,
                stage: 'TITRATION',
              ),
            ),
          ),
          '/user-states/latest' => (
            200,
            _ok({'weightKg': 77.9, 'weightChangeKg': -0.4}),
          ),
          _ => (404, _error('NOT_FOUND')),
        },
      );
      final prefill = await ConditionApiService(
        client,
      ).fetchPrefill(await _medicationState(client));
      expect(prefill.drugName, '마운자로');
      expect(prefill.weightKg, 77.9);
      expect(prefill.weeklyWeightDeltaKg, -0.4);
      // 최근 기록이 있으면 프로필은 다시 묻지 않는다.
      expect(adapter.requests.map((r) => r.path), isNot(contains('/users/me')));
    });

    test(
      'save sends one severity per symptom, codes the server accepts',
      () async {
        final (client, adapter) = await _client((_) => (201, _ok({})));
        await ConditionApiService(client).save(
          weightKg: 78.4,
          appetite: Appetite.normal,
          symptoms: {GiSymptom.nausea, GiSymptom.bloating},
          severity: SymptomSeverity.mild,
        );
        final request = adapter.requests.single;
        expect(request.path, '/user-states');
        expect(request.data, {
          'weightKg': 78.4,
          'appetiteLevel': 3,
          'giSymptoms': [
            {'code': 'NAUSEA', 'severity': 'MILD'},
            {'code': 'BLOATING', 'severity': 'MILD'},
          ],
        });
      },
    );

    test('no symptom is an empty list', () async {
      final (client, adapter) = await _client((_) => (201, _ok({})));
      await ConditionApiService(client).save(
        weightKg: 78.4,
        appetite: Appetite.none,
        symptoms: const {},
        severity: null,
      );
      expect((adapter.requests.single.data as Map)['giSymptoms'], isEmpty);
    });
  });

  group('history', () {
    test('delete calls DELETE /meals/{id}', () async {
      final (client, adapter) = await _client(
        (_) => (
          200,
          _ok({
            'mealId': 'meal-1',
            'deletedAt': '2026-09-29T10:00:00+09:00',
            'affectedInsights': [],
          }),
        ),
      );
      await MealHistoryApiService(client).deleteMeal('meal-1');
      final request = adapter.requests.single;
      expect(request.method, 'DELETE');
      expect(request.path, '/meals/meal-1');
    });

    test('collects one day across pages, oldest first, then stops', () async {
      final firstPage = _ok({
        'items': [
          _listItem('d23', '2026-09-23T08:00:00+09:00'),
          _listItem(
            'dinner',
            '2026-09-22T19:00:00+09:00',
            scores: {'quantity': 70, 'quality': 80, 'satiety': 60},
          ),
        ],
        'nextCursor': 'page2',
        'hasMore': true,
      });
      final secondPage = _ok({
        'items': [
          // 평가 전 식사는 scores 가 null 이다.
          _listItem('breakfast', '2026-09-22T08:00:00+09:00'),
          _listItem('d21', '2026-09-21T12:00:00+09:00'),
        ],
        'nextCursor': 'page3',
        'hasMore': true,
      });
      final (client, adapter) = await _client(
        (o) =>
            (200, o.queryParameters['cursor'] == null ? firstPage : secondPage),
      );
      final meals = await MealHistoryApiService(
        client,
      ).fetchMealsByDate(DateTime(2026, 9, 22));

      expect(meals.map((m) => m.mealId), ['breakfast', 'dinner']);
      expect(meals.first.scores, isNull);
      expect(meals.last.scores!.quality, 80);
      // 그날보다 이전 식사를 만나면 page3 은 부르지 않는다.
      expect(adapter.requests, hasLength(2));
      expect(adapter.requests.last.queryParameters['cursor'], 'page2');
    });

    test('calendar with no evaluated meals has null averages', () async {
      final (client, adapter) = await _client(
        (_) => (
          200,
          _ok({
            'month': '2026-09',
            'days': <Object>[],
            'summary': {
              'totalMeals': 0,
              'avgScores': {'quantity': null, 'quality': null, 'satiety': null},
            },
          }),
        ),
      );
      final calendar = await MealHistoryApiService(
        client,
      ).fetchCalendar(DateTime(2026, 9));
      expect(adapter.requests.single.queryParameters['month'], '2026-09');
      expect(calendar.summary.avgScores.quality, isNull);
    });
  });

  group('long-term dashboard', () {
    test('drops points and averages the server left empty', () async {
      final (client, _) = await _client(
        (_) => (
          200,
          _ok({
            'period': {'type': '7d', 'from': '2026-09-22', 'to': '2026-09-28'},
            'series': [
              {
                'date': '2026-09-27',
                'stage': 'INITIAL',
                'quantity': null,
                'quality': null,
                'satiety': null,
              },
              {
                'date': '2026-09-28',
                'stage': 'INITIAL',
                'quantity': 70,
                'quality': 80,
                'satiety': 60,
              },
            ],
            'averages': {'quantity': 70, 'quality': 80, 'satiety': null},
            'byMealType': {
              'LUNCH': {'quantity': null, 'quality': 80, 'satiety': 60},
            },
            'monthlyAverages': <Object>[],
            'weightSeries': <Object>[],
            'stageChanges': <Object>[],
          }),
        ),
      );
      final data = await LongTermApiService(client).fetchDashboard('7d');
      expect(data.series.map((s) => s.date), ['2026-09-28']);
      expect(data.averages, {'quantity': 70, 'quality': 80});
      expect(data.byMealType['LUNCH'], {'quality': 80, 'satiety': 60});
    });

    test('dose events come from the events wrapper', () async {
      final (client, adapter) = await _client(
        (_) => (
          200,
          _ok({
            'events': [
              {
                'doseEventId': 'e1',
                'doseMg': 0.25,
                'direction': 'MAINTAIN',
                'effectiveFrom': '2026-09-01',
              },
            ],
          }),
        ),
      );
      final events = await LongTermApiService(client).fetchDoseEvents();
      expect(adapter.requests.single.path, '/medications/dose-events');
      expect(events.single.doseMg, 0.25);
    });
  });
}
