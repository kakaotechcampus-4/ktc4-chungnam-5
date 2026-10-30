/// 서버가 결과를 만드는 동안(202 → `PENDING`·`GENERATING`) 같은 조회를 다시 보낸다.
///
/// 간격은 처음엔 짧게, 갈수록 길게 늘린다(× [growth], 최대 [maxInterval]).
/// 대부분의 결과는 몇 초 안에 끝나 빨리 보여 주고, 오래 걸리는 결과는 서버를
/// 덜 두드린다. 푸시(FCM)가 없어서 쓰는 방식이다 — 푸시가 생기면 기다리는 쪽만
/// 바꾼다.
///
/// - [fetch] 가 실패(네트워크 끊김 등)해도 멈추지 않고 다음 차례에 다시 묻는다.
/// - [isDone] 이 참이면 그 결과를 돌려준다.
/// - [budget] 이 지나거나 [isActive] 가 거짓이 되면(화면을 떠남) null 이다. 화면은
///   "나중에 확인" 안내로 바꾼다. 서버 작업은 계속되므로 다시 물으면 받을 수 있다.
/// - [onResult] 는 받은 결과마다 부른다(화면에 중간 상태를 그릴 때).
/// - [askNow] 면 기다리지 않고 바로 한 번 묻는다. 아니면 [first] 만큼 기다린 뒤 묻는다.
Future<T?> pollUntil<T>({
  required Future<T> Function() fetch,
  required bool Function(T result) isDone,
  required bool Function() isActive,
  void Function(T result)? onResult,
  Duration first = const Duration(milliseconds: 1500),
  Duration maxInterval = const Duration(seconds: 8),
  double growth = 1.5,
  Duration budget = const Duration(seconds: 60),
  bool askNow = false,
}) async {
  // 한도는 기다린 시간의 합으로 센다(요청 시간은 뺀다). 시계를 읽지 않아
  // 위젯 테스트의 가짜 시간에서도 같게 돈다.
  var waited = Duration.zero;
  Duration? interval = askNow ? null : first; // null: 아직 기다린 적 없음
  while (true) {
    if (interval != null) {
      await Future<void>.delayed(interval);
      waited += interval;
    }
    if (!isActive()) return null;
    try {
      final result = await fetch();
      if (!isActive()) return null;
      onResult?.call(result);
      if (isDone(result)) return result;
    } catch (_) {
      // 한 번 실패는 넘긴다. 한도는 그대로 센다.
    }
    if (waited >= budget) return null;
    if (interval == null) {
      interval = first;
      continue;
    }
    final next = Duration(
      milliseconds: (interval.inMilliseconds * growth).round(),
    );
    interval = next > maxInterval ? maxInterval : next;
  }
}
