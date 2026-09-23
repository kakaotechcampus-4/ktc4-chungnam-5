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
}) async {
  SharedPreferences.setMockInitialValues(
    userId == null ? {} : {'session.userId': userId},
  );
  final session = await UserSession.load();
  final adapter = _FakeAdapter(status, body, fail: fail);
  final dio = Dio(BaseOptions(baseUrl: 'http://test/api/v1'))
    ..httpClientAdapter = adapter;
  return (ApiClient(session: session, dio: dio), adapter);
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
