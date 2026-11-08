/// 식사 입력 화면의 기본값(끼니·시각)을 정하는 규칙. 화면과 상관없는 순수 함수라 따로 둔다.
library;

/// 끼니 순서. 서버 값이다.
const mealTypeOrder = ['BREAKFAST', 'LUNCH', 'DINNER'];

/// 입력 화면의 날짜 선택기가 오늘부터 거슬러 올라갈 수 있는 일수. 그보다 오래된 날은 고를 수 없어서
/// 넘겨 봐야 조용히 오늘로 바뀐다 — 처음부터 기록 버튼을 막는다.
const mealInputMaxPastDays = 365;

/// 지난 날짜에 끼니를 기록할 때 쓰는 대표 시각(시). 지금 시각은 그날의 식사 시각이 아니다.
const _representativeHour = {'BREAKFAST': 8, 'LUNCH': 12, 'DINNER': 19};

DateTime _dateOnly(DateTime t) => DateTime(t.year, t.month, t.day);

/// 시각으로 끼니를 추정한다. 04~10시 아침, 10~15시 점심, 그 밖(15~04시)은 저녁.
String mealTypeForTime(DateTime t) {
  final h = t.hour;
  if (h >= 4 && h < 10) return 'BREAKFAST';
  if (h >= 10 && h < 15) return 'LUNCH';
  return 'DINNER';
}

/// [day] 에 기록할 끼니의 기본값. [recorded] 는 그날 이미 기록한 끼니(식사 안 함 포함).
///
/// - 오늘이면 지금 시각으로 추정한 끼니. 그 끼니가 이미 기록됐으면 아직 안 먹은 첫 끼니.
/// - 다른 날이면 아직 안 먹은 첫 끼니(그날에는 "지금 시각" 이 의미가 없다).
/// - 세 끼니가 다 기록됐으면 `null`.
String? defaultMealType({
  required DateTime now,
  required DateTime day,
  Set<String> recorded = const {},
}) {
  final missing = [
    for (final t in mealTypeOrder)
      if (!recorded.contains(t)) t,
  ];
  if (missing.isEmpty) return null;
  if (_dateOnly(day) == _dateOnly(now)) {
    final slot = mealTypeForTime(now);
    if (missing.contains(slot)) return slot;
  }
  return missing.first;
}

/// 입력 화면이 처음 보여 줄 식사 시각. 오늘이면 지금, 지난 날이면 끼니별 대표 시각.
DateTime defaultEatenAt({
  required DateTime now,
  required DateTime day,
  required String mealType,
}) {
  final d = _dateOnly(day);
  if (d == _dateOnly(now)) return now;
  return DateTime(d.year, d.month, d.day, _representativeHour[mealType] ?? 12);
}

/// [day] 에 식사를 기록할 수 있는지. 미래이거나 날짜 선택기 범위(오늘부터 365일 전)를 벗어난 날은 `false`.
bool canRecordOn(DateTime day, DateTime now) {
  final d = _dateOnly(day);
  final today = _dateOnly(now);
  if (d.isAfter(today)) return false;
  return !d.isBefore(today.subtract(const Duration(days: mealInputMaxPastDays)));
}
