import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/screens/meal_defaults.dart';

void main() {
  DateTime at(int h, [int d = 15]) => DateTime(2026, 11, d, h);

  group('mealTypeForTime', () {
    test('estimates the meal from the hour (04-10 breakfast, 10-15 lunch)', () {
      expect(mealTypeForTime(at(4)), 'BREAKFAST');
      expect(mealTypeForTime(at(9)), 'BREAKFAST');
      expect(mealTypeForTime(at(10)), 'LUNCH');
      expect(mealTypeForTime(at(14)), 'LUNCH');
      expect(mealTypeForTime(at(15)), 'DINNER');
      expect(mealTypeForTime(at(23)), 'DINNER');
      expect(mealTypeForTime(at(3)), 'DINNER'); // 새벽은 전날 저녁의 연장
    });
  });

  group('defaultMealType', () {
    test('today follows the clock when nothing is recorded', () {
      expect(defaultMealType(now: at(8), day: at(8)), 'BREAKFAST');
      expect(defaultMealType(now: at(12), day: at(12)), 'LUNCH');
      expect(defaultMealType(now: at(20), day: at(20)), 'DINNER');
    });

    test('today skips a meal that is already recorded', () {
      // 점심 시간인데 점심이 이미 있으면 아직 안 먹은 첫 끼니.
      expect(
        defaultMealType(now: at(12), day: at(12), recorded: {'LUNCH'}),
        'BREAKFAST',
      );
      expect(
        defaultMealType(
          now: at(12),
          day: at(12),
          recorded: {'BREAKFAST', 'LUNCH'},
        ),
        'DINNER',
      );
    });

    test('another day ignores the clock and takes the first missing meal', () {
      final now = at(20); // 지금은 저녁 시간이지만 어제 기록이다
      final yesterday = at(0, 14);
      expect(defaultMealType(now: now, day: yesterday), 'BREAKFAST');
      expect(
        defaultMealType(now: now, day: yesterday, recorded: {'BREAKFAST'}),
        'LUNCH',
      );
    });

    test('is null when all three meals are recorded', () {
      expect(
        defaultMealType(
          now: at(12),
          day: at(12),
          recorded: {'BREAKFAST', 'LUNCH', 'DINNER'},
        ),
        isNull,
      );
    });
  });

  group('defaultEatenAt', () {
    test('today keeps the current time', () {
      final now = at(17);
      expect(defaultEatenAt(now: now, day: now, mealType: 'DINNER'), now);
    });

    test('a past day starts at the representative hour of the meal', () {
      final now = at(17);
      final day = DateTime(2026, 11, 3);
      expect(
        defaultEatenAt(now: now, day: day, mealType: 'BREAKFAST'),
        DateTime(2026, 11, 3, 8),
      );
      expect(
        defaultEatenAt(now: now, day: day, mealType: 'LUNCH'),
        DateTime(2026, 11, 3, 12),
      );
      expect(
        defaultEatenAt(now: now, day: day, mealType: 'DINNER'),
        DateTime(2026, 11, 3, 19),
      );
    });
  });

  group('canRecordOn', () {
    final now = at(12);

    test('today and past days within a year are allowed', () {
      expect(canRecordOn(now, now), isTrue);
      expect(canRecordOn(DateTime(2026, 11, 1), now), isTrue);
      expect(canRecordOn(now.subtract(const Duration(days: 365)), now), isTrue);
    });

    test('future days and days beyond the picker range are not', () {
      expect(canRecordOn(DateTime(2026, 11, 16), now), isFalse);
      expect(
        canRecordOn(now.subtract(const Duration(days: 366)), now),
        isFalse,
      );
    });
  });
}
