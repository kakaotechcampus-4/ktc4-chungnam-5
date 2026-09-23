import 'package:dio/dio.dart';

import '../state/user_session.dart';
import 'api_exception.dart';

/// API 연결 설정. 값은 빌드할 때 `--dart-define` 으로 바꾼다.
///
/// ```
/// flutter run -d chrome \
///   --dart-define=USE_REAL_API=true \
///   --dart-define=API_BASE_URL=http://localhost:8000/api/v1
/// ```
/// Android 에뮬레이터에서 PC 의 서버는 `localhost` 가 아니라 `10.0.2.2` 다.
class ApiConfig {
  ApiConfig._();

  static const baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  /// false(기본)면 각 화면의 API 서비스가 더미 응답을 돌려준다.
  /// 백엔드 없이도 화면을 확인할 수 있게 하려는 것이다.
  static const useRealApi = bool.fromEnvironment('USE_REAL_API');

  static const timeout = Duration(seconds: 10);
}

/// 성공 응답. [data] 는 래퍼 `{ success, data, error }` 의 `data` 다.
///
/// **[notice] 가 있어도 실패가 아니다.** 명세상 HTTP 200 인 도메인 code
/// (`LOW_CONFIDENCE` 등)는 `success: true` 와 `error` 가 함께 온다
/// (`backend/app/core/response.py`). 화면이 안내 문구로 쓴다.
class ApiResult {
  const ApiResult(this.data, {this.notice});

  final dynamic data;
  final ApiException? notice;

  Map<String, dynamic> get dataMap => data as Map<String, dynamic>;
}

/// 모든 API 호출이 지나가는 곳.
///
/// - 요청마다 `X-User-Id` 를 붙인다([UserSession]).
/// - 응답 래퍼를 벗겨 [ApiResult] 로 돌려준다.
/// - 실패는 전부 [ApiException] 으로 바꾼다. 화면은 dio 를 몰라도 된다.
///
/// 각 화면의 `XxxApiService` 는 이 클라이언트를 받아 메서드 본문만 채운다.
class ApiClient {
  ApiClient({required UserSession session, Dio? dio})
    : _dio =
          dio ??
          Dio(
            BaseOptions(
              baseUrl: ApiConfig.baseUrl,
              connectTimeout: ApiConfig.timeout,
              receiveTimeout: ApiConfig.timeout,
              contentType: Headers.jsonContentType,
            ),
          ) {
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final userId = session.userId;
          if (userId != null) options.headers['X-User-Id'] = userId;
          handler.next(options);
        },
      ),
    );
  }

  final Dio _dio;

  Future<ApiResult> get(String path, {Map<String, dynamic>? query}) =>
      _send(() => _dio.get<dynamic>(path, queryParameters: query));

  Future<ApiResult> post(String path, {Object? body}) =>
      _send(() => _dio.post<dynamic>(path, data: body));

  Future<ApiResult> patch(String path, {Object? body}) =>
      _send(() => _dio.patch<dynamic>(path, data: body));

  Future<ApiResult> delete(String path) =>
      _send(() => _dio.delete<dynamic>(path));

  Future<ApiResult> _send(Future<Response<dynamic>> Function() request) async {
    final Response<dynamic> response;
    try {
      response = await request();
    } on DioException catch (e) {
      throw _toException(e);
    }
    return _unwrap(response.data, response.statusCode);
  }

  ApiResult _unwrap(dynamic body, int? statusCode) {
    if (body is! Map<String, dynamic> || body['success'] is! bool) {
      throw ApiException(
        code: ApiException.unexpectedResponse,
        message: '응답 형식이 올바르지 않아요',
        statusCode: statusCode,
      );
    }
    final error = _errorOf(body, statusCode);
    if (body['success'] == true) {
      return ApiResult(body['data'], notice: error);
    }
    throw error ??
        ApiException(
          code: ApiException.unexpectedResponse,
          message: '요청을 처리하지 못했어요',
          statusCode: statusCode,
        );
  }

  ApiException? _errorOf(Map<String, dynamic> body, int? statusCode) {
    final error = body['error'];
    if (error is! Map<String, dynamic>) return null;
    return ApiException(
      code: error['code'] as String? ?? ApiException.unexpectedResponse,
      message: error['message'] as String? ?? '',
      statusCode: statusCode,
    );
  }

  ApiException _toException(DioException e) {
    final response = e.response;
    // 서버가 4xx/5xx 로 래퍼를 보낸 경우 — 래퍼의 code 가 진실이다.
    if (response != null) {
      final body = response.data;
      if (body is Map<String, dynamic>) {
        final error = _errorOf(body, response.statusCode);
        if (error != null) return error;
      }
      return ApiException(
        code: ApiException.unexpectedResponse,
        message: '서버 오류가 발생했어요 (${response.statusCode})',
        statusCode: response.statusCode,
      );
    }
    // 서버까지 못 간 경우. 웹에서는 CORS 차단도 여기로 온다.
    return const ApiException(
      code: ApiException.networkError,
      message: '서버에 연결하지 못했어요',
    );
  }
}
