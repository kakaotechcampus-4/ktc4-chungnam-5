/// 여러 화면이 같이 쓰는 API 값 ↔ 화면 표시 변환.
///
/// 화면마다 따로 갖고 있으면 한쪽만 고쳐져서 화면마다 다른 말이 된다.
/// 특히 시각 파싱([parseApiDateTime])은 어긋나면 9시간 차이가 나므로
/// 반드시 여기 것을 쓴다.
library;

// ── 표시 문구 ────────────────────────────────────────────────
// 열거형 → 한글 변환.

/// 단계 이름은 BE `MedicationStage`(`backend/app/models/enums.py`)를 따른다.
/// `TITRATION` 은 증량이 아니라 **조정기**다 — 중간 용량에서 아직 정착 전이라는
/// 뜻이고 방향을 담지 않는다.
String stageLabel(String stage) => switch (stage) {
  'PRE_DOSE' => '투약 전',
  'INITIAL' => '도입기',
  'TITRATION' => '조정기',
  'MAINTENANCE' => '유지기',
  'REDUCED' => '감량기',
  _ => stage,
};

String mealTypeLabel(String mealType) => switch (mealType) {
  'BREAKFAST' => '아침',
  'LUNCH' => '점심',
  'DINNER' => '저녁',
  'SNACK' => '간식',
  _ => mealType,
};

/// 날짜만 있는 값(`2026-08-21`). 시간대 변환 없이 그대로 읽는다.
DateTime parseApiDate(String value) => DateTime.parse(value);

/// 시각이 붙은 값(`2026-08-21T08:20:00+09:00`).
///
/// `DateTime.parse` 는 오프셋을 UTC 로 접어 버려서 `.hour` 가 9시간 어긋난다.
/// 명세상 모든 시각이 +09:00 이므로, 기기 시간대와 무관하게 그 벽시계 값을
/// 그대로 보여 주려고 UTC 로 바꾼 뒤 9시간을 더한다.
/// `toLocal()` 은 기기 설정에 휘둘려서 쓰지 않는다.
DateTime parseApiDateTime(String value) =>
    DateTime.parse(value).toUtc().add(const Duration(hours: 9));

/// 요청에 쓰는 날짜(`2026-08-21`).
String formatApiDate(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

/// 요청에 쓰는 시각(`2026-08-21T08:20:00+09:00`). 서버는 시간대 없는 시각을
/// 422 로 막는다. [parseApiDateTime] 과 짝이라 기기 시간대와 무관하게 KST 로 적는다.
String formatApiDateTime(DateTime t) {
  final k = t.toUtc().add(const Duration(hours: 9));
  String two(int v) => v.toString().padLeft(2, '0');
  return '${formatApiDate(k)}T${two(k.hour)}:${two(k.minute)}:${two(k.second)}'
      '+09:00';
}

/// 요청에 쓰는 월(`2026-08`).
String formatApiMonth(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}';
