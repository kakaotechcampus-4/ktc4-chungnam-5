import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/api/api_client.dart';
import 'package:frontend/api/api_exception.dart';
import 'package:frontend/state/user_session.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 실제 네트워크 대신 정해 둔 응답을 돌려주는 어댑터.
class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.status, this.body, {this.fail = false});

  final int status;
  final Object? body;
  final bool fail;
  RequestOptions? lastRequest;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    lastRequest = options;
    if (fail) {
      throw DioException.connectionError(
        requestOptions: options,
        reason: 'offline',
      );
    }
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

Future<(ApiClient, _FakeAdapter)> _client(
  int status,
  Object? body, {
  bool fail = false,
  String? userId = 'user-1',
  // 기본은 빈 값(헤더 없음). null 을 주면 ApiClient 가 ApiConfig 의 키를 쓴다.
  String? demoKey = '',
}) async {
  SharedPreferences.setMockInitialValues(
    userId == null ? {} : {'session.userId': userId},
  );
  final session = await UserSession.load();
  final adapter = _FakeAdapter(status, body, fail: fail);
  final dio = Dio(BaseOptions(baseUrl: 'http://test/api/v1'))
    ..httpClientAdapter = adapter;
  return (ApiClient(session: session, dio: dio, demoKey: demoKey), adapter);
}

void main() {
  test('unwraps data and sends X-User-Id', () async {
    final (client, adapter) = await _client(200, {
      'success': true,
      'data': {'nickname': '영우'},
      'error': null,
    });

    final result = await client.get('/users/me');

    expect(result.dataMap['nickname'], '영우');
    expect(result.notice, isNull);
    expect(adapter.lastRequest!.headers['X-User-Id'], 'user-1');
  });

  test('omits X-User-Id when there is no user yet', () async {
    final (client, adapter) = await _client(201, {
      'success': true,
      'data': {},
      'error': null,
    }, userId: null);

    await client.post('/users/profile', body: {});

    expect(adapter.lastRequest!.headers.containsKey('X-User-Id'), isFalse);
  });

  test('sends X-Demo-Key only when a demo key is set', () async {
    final (withKey, withKeyAdapter) = await _client(200, {
      'success': true,
      'data': {},
      'error': null,
    }, demoKey: 'demo-123');
    await withKey.get('/home');
    expect(withKeyAdapter.lastRequest!.headers['X-Demo-Key'], 'demo-123');

    final (noKey, noKeyAdapter) = await _client(200, {
      'success': true,
      'data': {},
      'error': null,
    });
    await noKey.get('/home');
    expect(
      noKeyAdapter.lastRequest!.headers.containsKey('X-Demo-Key'),
      isFalse,
    );
  });

  group('phase addresses', () {
    test('each phase has its own server', () {
      expect(ApiConfig.baseUrlFor(AppPhase.local), 'http://localhost:4010/api/v1');
      expect(ApiConfig.baseUrlFor(AppPhase.dev), 'http://127.0.0.1:8000/api/v1');
      expect(
        ApiConfig.baseUrlFor(AppPhase.staging),
        'https://glp1-staging.duckdns.org/api/v1',
      );
      expect(
        ApiConfig.baseUrlFor(AppPhase.prod),
        'https://glp1-demo.duckdns.org/api/v1',
      );
    });

    test('API_BASE_URL overrides only local and dev', () {
      expect(
        ApiConfig.baseUrlFor(AppPhase.local, override: 'http://10.0.2.2:4010/api/v1'),
        'http://10.0.2.2:4010/api/v1',
      );
      expect(
        ApiConfig.baseUrlFor(AppPhase.dev, override: 'http://10.0.2.2:8000/api/v1'),
        'http://10.0.2.2:8000/api/v1',
      );
    });

    test('staging and prod reject API_BASE_URL', () {
      // 어느 서버에 붙는 앱인지 알 수 없게 되므로 시작 때 터진다.
      for (final phase in [AppPhase.staging, AppPhase.prod]) {
        expect(
          () => ApiConfig.baseUrlFor(phase, override: 'http://10.0.2.2:8000/api/v1'),
          throwsA(isA<StateError>()),
        );
      }
    });
  });

  test('uses the key the web link provided, read at each request', () async {
    // 웹은 시작한 뒤에 링크로 키를 받는다 — 클라이언트를 만든 뒤에 정해져도 붙어야 한다.
    addTearDown(() => ApiConfig.runtimeDemoKey = '');
    final (client, adapter) = await _client(200, {
      'success': true,
      'data': {},
      'error': null,
    }, demoKey: null);

    await client.get('/home');
    expect(adapter.lastRequest!.headers.containsKey('X-Demo-Key'), isFalse);
    expect(ApiConfig.demoKeyHeaders, isNull);

    ApiConfig.runtimeDemoKey = 'from-link';
    await client.get('/home');
    expect(adapter.lastRequest!.headers['X-Demo-Key'], 'from-link');
    // 사진(`Image.network`)도 같은 키를 쓴다.
    expect(ApiConfig.demoKeyHeaders, {'X-Demo-Key': 'from-link'});
  });

  test('403 without an envelope says the demo key is wrong', () async {
    // 데모 서버 앞단(Caddy)은 래퍼 없이 막는다.
    final (client, _) = await _client(403, 'Forbidden');

    await expectLater(
      client.get('/home'),
      throwsA(
        isA<ApiException>()
            .having((e) => e.statusCode, 'statusCode', 403)
            .having((e) => e.message, 'message', '서버 접속 키가 맞지 않아요'),
      ),
    );
  });

  test('success with an error code is a notice, not a failure', () async {
    final (client, _) = await _client(200, {
      'success': true,
      'data': {'mealId': 'm1'},
      'error': {'code': 'LOW_CONFIDENCE', 'message': '확인해 주세요'},
    });

    final result = await client.get('/meals/m1');

    expect(result.dataMap['mealId'], 'm1');
    expect(result.notice?.code, 'LOW_CONFIDENCE');
  });

  test('error envelope becomes ApiException with its code', () async {
    final (client, _) = await _client(409, {
      'success': false,
      'data': null,
      'error': {'code': 'PROFILE_REQUIRED', 'message': '프로필이 필요해요'},
    });

    await expectLater(
      client.get('/home'),
      throwsA(
        isA<ApiException>()
            .having((e) => e.code, 'code', 'PROFILE_REQUIRED')
            .having((e) => e.statusCode, 'statusCode', 409),
      ),
    );
  });

  test('connection failure becomes NETWORK_ERROR', () async {
    final (client, _) = await _client(0, null, fail: true);

    await expectLater(
      client.get('/users/me'),
      throwsA(isA<ApiException>().having((e) => e.isNetworkError, 'net', true)),
    );
  });

  test('non-envelope body is rejected', () async {
    final (client, _) = await _client(200, {'status': 'ok'});

    await expectLater(
      client.get('/health'),
      throwsA(
        isA<ApiException>().having(
          (e) => e.code,
          'code',
          ApiException.unexpectedResponse,
        ),
      ),
    );
  });
}
