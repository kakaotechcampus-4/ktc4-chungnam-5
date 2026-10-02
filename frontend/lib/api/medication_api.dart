import '../common/api_format.dart';
import 'api_client.dart';
import 'api_exception.dart';

// ── 모델 ────────────────────────────────────────────────────

/// `POST /medications` · `GET /medications/current` 응답.
class MedicationCurrent {
  const MedicationCurrent({
    required this.medicationId,
    required this.drugName,
    required this.doseMg,
    required this.startedAt,
    required this.doseCount,
    required this.nextDoseDate,
    required this.daysUntilNextDose,
    required this.stage,
    this.stageReason,
  });

  final String medicationId;
  final String drugName;
  final double doseMg;
  final DateTime startedAt;

  /// 서버 계산: `floor((today − startedAt) / 7) + 1`.
  final int doseCount;

  final DateTime nextDoseDate;
  final int daysUntilNextDose;

  /// `INITIAL` · `TITRATION` · `MAINTENANCE` · `REDUCED`. 투약 기록이 있을 때만
  /// 이 응답이 오므로 `PRE_DOSE` 는 여기 안 온다(미등록은 409 `STAGE_NOT_SET`).
  final String stage;

  /// `약효가 줄고 식욕이 돌아오는 구간` 같은 한 줄 설명.
  final String? stageReason;

  factory MedicationCurrent.fromJson(Map<String, dynamic> json) =>
      MedicationCurrent(
        medicationId: json['medicationId'] as String,
        drugName: json['drugName'] as String,
        doseMg: (json['doseMg'] as num).toDouble(),
        startedAt: DateTime.parse(json['startedAt'] as String),
        doseCount: json['doseCount'] as int,
        nextDoseDate: DateTime.parse(json['nextDoseDate'] as String),
        daysUntilNextDose: json['daysUntilNextDose'] as int,
        stage: json['stage'] as String,
        stageReason: json['stageReason'] as String?,
      );
}

// ── API ─────────────────────────────────────────────────────

/// 투약 엔드포인트. 화면은 이걸 직접 부르지 않고 `MedicationState` 를 거친다 —
/// 홈·컨디션 팝업·투약 화면이 같은 값을 봐야 해서다.
class MedicationApiService {
  MedicationApiService(this._client);

  final ApiClient _client;

  /// `GET /medications/current`
  ///
  /// 미등록이면 409 `STAGE_NOT_SET` 이 온다. 이건 실패가 아니라
  /// "아직 입력 안 함" 이므로 null 로 바꿔 돌려주고 화면은 빈 폼을 보여 준다.
  Future<MedicationCurrent?> fetchCurrent() async {
    try {
      final result = await _client.get('/medications/current');
      return MedicationCurrent.fromJson(result.dataMap);
    } on ApiException catch (e) {
      if (e.code == 'STAGE_NOT_SET') return null;
      rethrow;
    }
  }

  /// `POST /medications` — **등록 전용**이다.
  ///
  /// - `doseMg` 가 현재 값과 다르면 서버가 용량 변경 1건을 기록한다. 그래서
  ///   미리보기 용도로는 절대 부르면 안 된다.
  /// - [startedAt] 은 첫 등록에만 보낸다. null 이면 서버가 기존 시작일을
  ///   그대로 둔다. 등록 후 다른 값을 보내면 409 `CONFLICT` 다.
  /// - 오늘 등록·변경한 용량을 오늘 또 바꿔도 409 `CONFLICT` 다.
  ///   정정은 `PATCH /medications/{recordId}`.
  Future<MedicationCurrent> save({
    required String drugName,
    required double doseMg,
    DateTime? startedAt,
  }) async {
    final result = await _client.post(
      '/medications',
      body: {
        'drugName': drugName,
        'doseMg': doseMg,
        if (startedAt != null) 'startedAt': formatApiDate(startedAt),
      },
    );
    return MedicationCurrent.fromJson(result.dataMap);
  }
}
