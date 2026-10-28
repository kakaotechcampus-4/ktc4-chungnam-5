import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/common/polling.dart';

void main() {
  test('waits longer each time and gives up after the budget', () async {
    var calls = 0;
    final result = await pollUntil<int>(
      fetch: () async => ++calls,
      isDone: (_) => false,
      isActive: () => true,
      first: const Duration(milliseconds: 10),
      growth: 2,
      maxInterval: const Duration(milliseconds: 40),
      budget: const Duration(milliseconds: 100),
    );

    // 10 → 20 → 40 → 40(최대) 를 기다리고, 합이 110ms 라 네 번째에서 멈춘다.
    expect(result, isNull);
    expect(calls, 4);
  });

  test('a failed request is skipped and the next one is used', () async {
    var calls = 0;
    final result = await pollUntil<String>(
      fetch: () async {
        calls++;
        if (calls == 1) throw Exception('offline');
        return 'READY';
      },
      isDone: (r) => r == 'READY',
      isActive: () => true,
      first: const Duration(milliseconds: 5),
      askNow: true,
    );

    expect(result, 'READY');
    expect(calls, 2);
  });

  test('stops when the screen is gone', () async {
    var active = true;
    var calls = 0;
    final result = await pollUntil<int>(
      fetch: () async {
        active = false;
        return ++calls;
      },
      isDone: (_) => false,
      isActive: () => active,
      first: const Duration(milliseconds: 5),
    );

    expect(result, isNull);
    expect(calls, 1);
  });
}
