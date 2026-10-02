import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';

/// 위젯 테스트용 가짜 서버. 기본 응답은 local mock 서버와 같은
/// `mock/examples.json` 이고, 테스트마다 [on] 으로 일부만 바꾼다.
///
/// 앱의 더미 분기 대신 이걸 쓴다 — 앱 코드는 테스트에서도 실제 경로로 돈다.
class FakeApi implements HttpClientAdapter {
  FakeApi() : _routes = _loadExamples();

  final Map<String, FakeHandler> _routes;

  /// 받은 요청. 경로는 `/api/v1` 을 뺀 값이다(`/users/me`).
  final List<RequestOptions> requests = [];

  /// [route] 는 `'GET /users/me'` · `'DELETE /meals/{meal_id}'` 형식.
  void on(String route, FakeHandler handler) => _routes[route] = handler;

  Dio get dio =>
      Dio(BaseOptions(baseUrl: 'http://fake/api/v1'))..httpClientAdapter = this;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    requests.add(options);
    final handler = _match(options.method, options.path);
    final (status, body) = handler == null
        ? (404, fakeError('NOT_FOUND'))
        : handler(options);
    return ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );
  }

  FakeHandler? _match(String method, String path) {
    for (final MapEntry(:key, :value) in _routes.entries) {
      final [routeMethod, routePath] = key.split(' ');
      if (routeMethod != method) continue;
      final pattern = RegExp(
        '^${routePath.replaceAll(RegExp(r'\{[^}]+\}'), '[^/]+')}\$',
      );
      if (pattern.hasMatch(path)) return value;
    }
    return null;
  }

  @override
  void close({bool force = false}) {}

  static Map<String, FakeHandler> _loadExamples() {
    final raw = _fillDates(File('mock/examples.json').readAsStringSync());
    final examples = jsonDecode(raw) as Map<String, dynamic>;
    return {
      for (final MapEntry(:key, :value) in examples.entries)
        key.replaceFirst('/api/v1', ''): _fixed(value as Map<String, dynamic>),
    };
  }

  static FakeHandler _fixed(Map<String, dynamic> byStatus) {
    final MapEntry(:key, :value) = byStatus.entries.first;
    return (_) => (int.parse(key), value);
  }

  /// `mock/build-spec.mjs` 와 같은 날짜 치환.
  static String _fillDates(String text) {
    final today = DateTime.now();
    String ymd(DateTime d) =>
        '${d.year}-${d.month.toString().padLeft(2, '0')}-'
        '${d.day.toString().padLeft(2, '0')}';
    return text
        .replaceAllMapped(
          RegExp(r'\{\{today([+-]\d+)?\}\}'),
          (m) => ymd(
            DateTime(
              today.year,
              today.month,
              today.day + int.parse(m[1] ?? '0'),
            ),
          ),
        )
        .replaceAll('{{month}}', ymd(today).substring(0, 7));
  }
}

typedef FakeHandler = (int, Object?) Function(RequestOptions options);

Map<String, Object?> fakeOk(Object? data) => {
  'success': true,
  'data': data,
  'error': null,
};

Map<String, Object?> fakeError(String code) => {
  'success': false,
  'data': null,
  'error': {'code': code, 'message': ''},
};
