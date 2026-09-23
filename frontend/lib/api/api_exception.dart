/// API 호출 실패. 분기는 [code] 로 한다 — HTTP status 는 참고용이다.
///
/// [code] 는 백엔드 `ErrorCode`(`backend/app/core/errors.py`) 값 그대로다.
/// 서버까지 닿지 못한 경우는 [networkError] 를 쓴다.
class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.statusCode,
  });

  /// 서버 응답을 받지 못했다(연결 실패·타임아웃·CORS 등).
  static const networkError = 'NETWORK_ERROR';

  /// 응답이 `{ success, data, error }` 래퍼 모양이 아니다.
  static const unexpectedResponse = 'UNEXPECTED_RESPONSE';

  final String code;
  final String message;
  final int? statusCode;

  bool get isNetworkError => code == networkError;

  @override
  String toString() => 'ApiException($code, $statusCode): $message';
}
