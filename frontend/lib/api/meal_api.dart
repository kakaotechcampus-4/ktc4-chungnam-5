import '../common/api_format.dart';
import 'api_client.dart';

// 식사 기록 흐름(입력 → 분석 → 음식 확인 → 평가 → 피드백)과 포만감 체크인이
// 같이 쓰는 모델·API. 여러 화면이 쓰므로 화면 파일이 아니라 여기 둔다.

// ── 모델 ────────────────────────────────────────────────────

/// `POST /meals` 응답(202). 분석은 서버에서 비동기로 돈다.
class MealCreated {
  const MealCreated({
    required this.mealId,
    required this.pollInterval,
    required this.timeout,
  });

  final String mealId;

  /// 서버가 정해 주는 폴링 간격·한도.
  final Duration pollInterval;
  final Duration timeout;

  factory MealCreated.fromJson(Map<String, dynamic> json) => MealCreated(
    mealId: json['mealId'] as String,
    pollInterval: Duration(milliseconds: json['pollIntervalMs'] as int),
    timeout: Duration(milliseconds: json['timeoutMs'] as int),
  );
}

/// 음식 1개(또는 후보 1개)의 영양 정보. 모르는 값은 null 이다.
class MealNutrition {
  const MealNutrition({
    this.kcal,
    this.proteinG,
    this.fatG,
    this.carbG,
    this.fiberG,
    this.sodiumMg,
  });

  final double? kcal;
  final double? proteinG;
  final double? fatG;
  final double? carbG;
  final double? fiberG;
  final double? sodiumMg;

  static double? _d(Object? v) => (v as num?)?.toDouble();

  factory MealNutrition.fromJson(Map<String, dynamic> json) => MealNutrition(
    kcal: _d(json['kcal']),
    proteinG: _d(json['proteinG']),
    fatG: _d(json['fatG']),
    carbG: _d(json['carbG']),
    fiberG: _d(json['fiberG']),
    sodiumMg: _d(json['sodiumMg']),
  );
}

/// `GET /meals/{mealId}` 의 `items[]`.
class MealItem {
  const MealItem({
    required this.itemId,
    required this.displayName,
    required this.amount,
    required this.unit,
    required this.confidence,
    required this.matched,
    this.nutrition,
  });

  final String itemId;
  final String displayName;

  /// AI 가 양을 못 읽었으면 null — 양 스테퍼를 막는다.
  final double? amount;
  final String? unit;

  /// 0~1. 사용자가 추가한 음식은 null.
  final double? confidence;

  /// 영양 정보가 있는지(서버 정의). false 면 후보 검색으로 고르게 한다.
  final bool matched;
  final MealNutrition? nutrition;

  MealItem copyWith({
    double? amount,
    bool? matched,
    MealNutrition? nutrition,
  }) => MealItem(
    itemId: itemId,
    displayName: displayName,
    amount: amount ?? this.amount,
    unit: unit,
    confidence: confidence,
    matched: matched ?? this.matched,
    nutrition: nutrition ?? this.nutrition,
  );

  factory MealItem.fromJson(Map<String, dynamic> json) => MealItem(
    itemId: json['itemId'] as String,
    displayName: json['displayName'] as String,
    amount: (json['amount'] as num?)?.toDouble(),
    unit: json['unit'] as String?,
    confidence: (json['confidence'] as num?)?.toDouble(),
    matched: json['matched'] as bool,
    nutrition: json['nutrition'] == null
        ? null
        : MealNutrition.fromJson(json['nutrition'] as Map<String, dynamic>),
  );
}

/// `GET /meals/{mealId}`.
///
/// **`status` 가 `ANALYZING` 이어도 `isRecalculation` 이면 분석 중이 아니다.**
/// 음식 확인 화면에서 음식을 고친 뒤 확정을 기다리는 상태라, 폴링하지 않고
/// 계속 고칠 수 있다(BE `services/meal.py` `_is_editable`).
class MealDetail {
  const MealDetail({
    required this.mealId,
    required this.status,
    required this.isRecalculation,
    required this.mealType,
    required this.eatenAt,
    required this.eatenAtInstant,
    required this.items,
    this.imageUrl,
    this.clarifyQuestion,
    this.satietyAfterPct,
  });

  final String mealId;

  /// `ANALYZING` · `REVIEW_REQUIRED` · `EVALUATED` · `FAILED`.
  final String status;
  final bool isRecalculation;
  final String mealType;

  /// KST 벽시계 값([parseApiDateTime]). 화면에 시각을 적을 때 쓴다.
  final DateTime eatenAt;

  /// 실제 시점. 지금부터 몇 분 지났는지 셀 때 쓴다.
  final DateTime eatenAtInstant;
  final List<MealItem> items;

  /// `/media/...` 경로. 그릴 때는 `ApiConfig.mediaUrl` 을 거친다.
  final String? imageUrl;
  final String? clarifyQuestion;

  /// 확정 때 적은 식후 포만감(`satiety.afterPct`). 확정 전이면 null.
  /// 끼니 피드백이 실패했을 때 같은 값으로 다시 확정해 재생성을 건다.
  final int? satietyAfterPct;

  /// 최초 분석이 아직 도는 중인지. 고친 뒤의 `ANALYZING` 은 아니다.
  bool get isFirstAnalysis => status == 'ANALYZING' && !isRecalculation;

  /// 음식 이름을 이어 붙인 한 줄(목록·팝업 제목용).
  String get displayName => items.map((i) => i.displayName).join(', ');

  factory MealDetail.fromJson(Map<String, dynamic> json) => MealDetail(
    mealId: json['mealId'] as String,
    status: json['status'] as String,
    isRecalculation: json['isRecalculation'] as bool? ?? false,
    mealType: json['mealType'] as String,
    eatenAt: parseApiDateTime(json['eatenAt'] as String),
    eatenAtInstant: DateTime.parse(json['eatenAt'] as String),
    imageUrl: json['imageUrl'] as String?,
    clarifyQuestion: json['clarifyQuestion'] as String?,
    satietyAfterPct:
        (json['satiety'] as Map<String, dynamic>?)?['afterPct'] as int?,
    items: (json['items'] as List<dynamic>)
        .map((e) => MealItem.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

/// `GET /nutrition/candidates` 한 건 — 공공 식품영양성분 DB.
class FoodCandidate {
  const FoodCandidate({
    required this.foodRefId,
    required this.name,
    required this.nutrition,
    this.servingSizeG,
  });

  final String foodRefId;

  /// 공공 DB 원문(`달걀_삶은것`). 화면에는 [displayName] 을 쓴다.
  final String name;
  final double? servingSizeG;

  /// 표시용 이름. 원문의 `_` 를 띄어 쓴다(`달걀 삶은것`).
  String get displayName => name.replaceAll('_', ' ').trim();
  final MealNutrition nutrition;

  factory FoodCandidate.fromJson(Map<String, dynamic> json) => FoodCandidate(
    foodRefId: json['foodRefId'] as String,
    name: json['name'] as String,
    servingSizeG: (json['servingSizeG'] as num?)?.toDouble(),
    nutrition: MealNutrition.fromJson(
      json['nutrition'] as Map<String, dynamic>,
    ),
  );
}

/// Q·Q·S 점수. 영양 정보가 없는 음식만 있으면 양·질은 null 이다.
class QqsScores {
  const QqsScores({this.quantity, this.quality, this.satiety});

  final int? quantity;
  final int? quality;
  final int? satiety;

  factory QqsScores.fromJson(Map<String, dynamic> json) => QqsScores(
    quantity: json['quantity'] as int?,
    quality: json['quality'] as int?,
    satiety: json['satiety'] as int?,
  );
}

/// 평가 응답의 `nutrients[]`. 매칭된 음식이 없으면 [current] 가 null 이다.
class NutrientStatus {
  const NutrientStatus({
    required this.code,
    required this.label,
    required this.unit,
    this.current,
    this.target,
    this.state,
  });

  final String code;
  final String label;
  final String unit;
  final double? current;
  final double? target;

  /// `SHORT` · `OK` · `OVER`. 모르면 null.
  final String? state;

  factory NutrientStatus.fromJson(Map<String, dynamic> json) => NutrientStatus(
    code: json['code'] as String,
    label: json['label'] as String,
    unit: json['unit'] as String,
    current: (json['current'] as num?)?.toDouble(),
    target: (json['target'] as num?)?.toDouble(),
    state: json['state'] as String?,
  );
}

/// `POST /meals/{id}/confirm` · `GET /meals/{id}/evaluation`.
class MealEvaluation {
  const MealEvaluation({
    required this.mealId,
    required this.stage,
    required this.scores,
    required this.nutrients,
    this.notice,
  });

  final String mealId;
  final String stage;
  final QqsScores scores;
  final List<NutrientStatus> nutrients;

  /// 성공이지만 알릴 말(`NUTRITION_NOT_MATCHED` — 영양 정보 없는 음식은
  /// 합계에서 빠졌다는 안내).
  final String? notice;

  factory MealEvaluation.fromResult(ApiResult result) {
    final json = result.dataMap;
    return MealEvaluation(
      mealId: json['mealId'] as String,
      stage: json['stage'] as String,
      scores: QqsScores.fromJson(json['scores'] as Map<String, dynamic>),
      nutrients: (json['nutrients'] as List<dynamic>)
          .map((e) => NutrientStatus.fromJson(e as Map<String, dynamic>))
          .toList(),
      notice: result.notice?.message,
    );
  }
}

/// 음식 **100g 에 든** 영양소 하나(`{code, amount, unit}`). 끼니 피드백 제안
/// (`suggestions[].nutrients[]`)과 챗봇 음식 카드(`foodCards[].effects[]`)가 같이 쓴다.
///
/// "먹으면 늘어나는 양" 이 아니라 그 음식의 함량이라 0 이상이다. BE 가
/// `food_refs` 에서 채운다(`crud/feedback.py::nutrients_by_food_ref`).
class NutrientAmount {
  const NutrientAmount({
    required this.code,
    required this.amount,
    required this.unit,
  });

  /// `CALORIE` · `PROTEIN` · `FAT` · `CARB` · `FIBER` · `SODIUM`.
  final String code;
  final double amount;

  /// `g` · `mg` · `kcal`.
  final String unit;

  /// 읽을 수 있는 항목만 돌려준다. 예전 모양 `{code, amountG}` 도 g 으로 읽는다 —
  /// BE 가 `{amount, unit}` 으로 바꾸는 중이라 어느 쪽이 먼저 배포돼도 칩이 남는다.
  static List<NutrientAmount> listFrom(Object? json) => [
    for (final n in json is List<dynamic> ? json : const <dynamic>[])
      ?_tryParse(n),
  ];

  static NutrientAmount? _tryParse(Object? n) {
    if (n is! Map<String, dynamic> || n['code'] is! String) return null;
    final (amount, unit) = switch (n) {
      {'amount': final num a, 'unit': final String u} => (a, u),
      {'amountG': final num a} => (a, 'g'),
      _ => (null, null),
    };
    if (amount == null || unit == null || amount < 0) return null;
    return NutrientAmount(
      code: n['code'] as String,
      amount: amount.toDouble(),
      unit: unit,
    );
  }
}

class MealSuggestion {
  const MealSuggestion({
    required this.foodName,
    required this.nutrients,
    required this.advice,
  });

  final String foodName;
  final List<NutrientAmount> nutrients;
  final String advice;

  factory MealSuggestion.fromJson(Map<String, dynamic> json) => MealSuggestion(
    foodName: json['foodName'] as String,
    nutrients: NutrientAmount.listFrom(json['nutrients']),
    advice: json['advice'] as String,
  );
}

/// `GET /meals/{id}/feedback`. AI 문장이라 늦게 도착한다.
class MealFeedback {
  const MealFeedback({
    required this.feedbackStatus,
    required this.suggestions,
    this.summary,
    this.safetyStatus,
  });

  /// `PENDING` · `GENERATING` · `READY` · `FAILED`.
  final String feedbackStatus;
  final String? summary;
  final List<MealSuggestion> suggestions;

  /// `SAFE` · `BLOCKED` · `REVIEW_REQUIRED`. 아직 없으면 null.
  final String? safetyStatus;

  bool get isPending =>
      feedbackStatus == 'PENDING' || feedbackStatus == 'GENERATING';

  /// 의료 판단이 필요한 내용이라 제안 대신 상담을 안내해야 한다
  /// (`READY` + `BLOCKED`, 서버 notice `MEDICAL_QUESTION_DETECTED`).
  bool get isBlocked => safetyStatus == 'BLOCKED';

  factory MealFeedback.fromJson(Map<String, dynamic> json) => MealFeedback(
    feedbackStatus: json['feedbackStatus'] as String,
    safetyStatus: json['safetyStatus'] as String?,
    summary: json['summary'] as String?,
    suggestions: (json['suggestions'] as List<dynamic>? ?? const [])
        .map((e) => MealSuggestion.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

/// `GET /insights/daily?date=` — 그날(KST) 하루 피드백.
///
/// 새로고침(`POST /insights/daily/refresh`)을 불러야 만들어진다. 한 번도 안
/// 만들었으면 `PENDING`, 만든 뒤 그날 식사가 바뀌었으면 [stale] 이다. 만들었는데
/// 근거(평가한 끼니)가 없으면 `READY` 인데 [summary] 가 비어 있다.
class DailyFeedback {
  const DailyFeedback({
    required this.feedbackStatus,
    required this.stale,
    this.summary,
    this.hasRow = false,
    this.safetyStatus,
  });

  /// `PENDING` · `GENERATING` · `READY` · `FAILED`.
  final String feedbackStatus;

  /// `safetyStatus` 가 `SAFE` 일 때만 온다.
  final String? summary;
  final bool stale;

  /// 서버에 그날 요약 행이 있는지(`dailyFeedbackId`). `READY` 인데 행이 없으면
  /// 요약할 근거(평가된 끼니)가 없어 워커가 만들지 않은 것이다 — 실패가 아니다.
  final bool hasRow;

  /// `SAFE` · `BLOCKED` · `REVIEW_REQUIRED`. 행이 없으면 null.
  final String? safetyStatus;

  /// 만들었지만 안전 검사에 걸려 문장을 보여 주지 않는다.
  bool get isHidden => safetyStatus != null && safetyStatus != 'SAFE';

  /// 새로 만들어 달라고 할지.
  bool get needsRefresh => feedbackStatus == 'PENDING' || stale;

  bool get isGenerating => feedbackStatus == 'GENERATING';

  factory DailyFeedback.fromJson(Map<String, dynamic> json) => DailyFeedback(
    feedbackStatus: json['feedbackStatus'] as String,
    summary: json['summary'] as String?,
    stale: json['stale'] as bool? ?? false,
    hasRow: json['dailyFeedbackId'] != null,
    safetyStatus: json['safetyStatus'] as String?,
  );
}

// ── API ─────────────────────────────────────────────────────

/// 식사 기록 흐름 엔드포인트.
class MealApiService {
  MealApiService(this._client);

  final ApiClient _client;

  /// `POST /meals` — 텍스트 입력(JSON).
  Future<MealCreated> createWithText({
    required String mealType,
    required DateTime eatenAt,
    required String text,
    int? satietyBeforePct,
  }) async {
    final result = await _client.post(
      '/meals',
      body: {
        'mealType': mealType,
        'eatenAt': formatApiDateTime(eatenAt),
        'rawText': text,
        'satietyBeforePct': ?satietyBeforePct,
      },
    );
    return MealCreated.fromJson(result.dataMap);
  }

  /// `POST /meals` — 사진 입력(multipart).
  Future<MealCreated> createWithPhoto({
    required String mealType,
    required DateTime eatenAt,
    required List<int> photo,
    required String fileName,
    int? satietyBeforePct,
  }) async {
    final result = await _client.postMultipart(
      '/meals',
      fields: {
        'mealType': mealType,
        'eatenAt': formatApiDateTime(eatenAt),
        if (satietyBeforePct != null) 'satietyBeforePct': '$satietyBeforePct',
      },
      fileField: 'image',
      fileBytes: photo,
      fileName: fileName,
      fileContentType: imageContentType(photo),
    );
    return MealCreated.fromJson(result.dataMap);
  }

  /// 서버와 같은 방식(앞 바이트)으로 형식을 고른다. 모르면 JPEG 로 보내고
  /// 서버가 422 로 거른다.
  static String imageContentType(List<int> bytes) {
    bool starts(List<int> sig, [int offset = 0]) =>
        bytes.length >= offset + sig.length &&
        Iterable<int>.generate(
          sig.length,
        ).every((i) => bytes[offset + i] == sig[i]);
    if (starts([0x89, 0x50, 0x4E, 0x47])) return 'image/png';
    if (starts([0x52, 0x49, 0x46, 0x46]) &&
        starts([0x57, 0x45, 0x42, 0x50], 8)) {
      return 'image/webp';
    }
    return 'image/jpeg';
  }

  /// `GET /meals/{mealId}`
  Future<MealDetail> fetchMeal(String mealId) async {
    final result = await _client.get('/meals/$mealId');
    return MealDetail.fromJson(result.dataMap);
  }

  /// `POST /meals/{mealId}/items` — 음식 추가. 응답에 이름·양이 없어 다시
  /// 조회한다.
  Future<void> addItem(
    String mealId, {
    required String displayName,
    required double amount,
    required String unit,
  }) => _client.post(
    '/meals/$mealId/items',
    body: {'displayName': displayName, 'amount': amount, 'unit': unit},
  );

  /// `PATCH /meals/{mealId}/items` — 여러 음식의 이름·양을 한 번에 고친다.
  Future<void> updateItems(String mealId, List<MealItem> items) =>
      _client.patch(
        '/meals/$mealId/items',
        body: {
          'items': [
            for (final i in items)
              {
                'itemId': i.itemId,
                'displayName': i.displayName,
                'amount': i.amount,
                'unit': i.unit,
              },
          ],
        },
      );

  /// `DELETE /meals/{mealId}/items/{itemId}`
  Future<void> deleteItem(String mealId, String itemId) =>
      _client.delete('/meals/$mealId/items/$itemId');

  /// `GET /nutrition/candidates?q=`
  Future<List<FoodCandidate>> searchCandidates(String query) async {
    final result = await _client.get(
      '/nutrition/candidates',
      query: {'q': query, 'limit': 20},
    );
    return (result.dataMap['candidates'] as List<dynamic>)
        .map((e) => FoodCandidate.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// `PUT /meals/{mealId}/items/{itemId}/nutrition` — 후보 DB 항목으로 영양
  /// 정보를 정한다. 응답의 영양 정보는 그 음식 양으로 환산된 값이다.
  ///
  /// 먹은 양이 g 으로 환산되지 않으면(예: `2개`) 서버가 저장은 하되 영양
  /// 정보를 비우고 `NUTRITION_NOT_MATCHED` 안내를 붙인다 → [notice] 로 돌려준다.
  Future<({MealNutrition? nutrition, String? notice})> setNutrition(
    String mealId,
    String itemId, {
    String? foodRefId,
    MealNutrition? manual,
  }) async {
    assert(
      (foodRefId == null) != (manual == null),
      'foodRefId 와 manual 중 하나만 준다',
    );
    final result = await _client.put(
      '/meals/$mealId/items/$itemId/nutrition',
      body: {
        'foodRefId': ?foodRefId,
        if (manual != null)
          'manual': {
            'kcal': ?manual.kcal,
            'proteinG': ?manual.proteinG,
            'fatG': ?manual.fatG,
            'carbG': ?manual.carbG,
            'fiberG': ?manual.fiberG,
            'sodiumMg': ?manual.sodiumMg,
          },
      },
    );
    final nutrition = result.dataMap['nutrition'];
    return (
      nutrition: nutrition == null
          ? null
          : MealNutrition.fromJson(nutrition as Map<String, dynamic>),
      notice: result.notice?.message,
    );
  }

  /// `POST /meals/{mealId}/confirm` — 확정하고 Q·Q·S 를 바로 받는다.
  /// 식후 포만감은 필수다.
  Future<MealEvaluation> confirm(
    String mealId, {
    required int satietyAfterPct,
  }) async {
    final result = await _client.post(
      '/meals/$mealId/confirm',
      body: {'satietyAfterPct': satietyAfterPct},
    );
    return MealEvaluation.fromResult(result);
  }

  /// `GET /meals/{mealId}/evaluation`
  Future<MealEvaluation> fetchEvaluation(String mealId) async =>
      MealEvaluation.fromResult(await _client.get('/meals/$mealId/evaluation'));

  /// `GET /meals/{mealId}/feedback` — 조회만 한다. 생성 작업은 확정
  /// (`POST /confirm`)이 건다.
  Future<MealFeedback> fetchFeedback(String mealId) async {
    final result = await _client.get('/meals/$mealId/feedback');
    return MealFeedback.fromJson(result.dataMap);
  }

  /// `PUT /meals/{mealId}/feedback/suggestions` — 챗봇 카드 한 장으로 이 끼니의
  /// 다음 끼니 추천을 **통째로** 바꾼다(본문·점수는 그대로).
  ///
  /// - 404 `NOT_FOUND`: 끼니나 카드가 없다(다른 끼니 대화의 카드 포함).
  /// - 409 `NOT_CONFIRMED`: 끼니가 확정 상태가 아니다.
  /// - 409 `CONFLICT`: 카드가 끼니를 고치기 전에 만들어졌거나 피드백이 아직 준비 전이다.
  Future<MealFeedback> replaceSuggestion(
    String mealId, {
    required String foodCardId,
  }) async {
    final result = await _client.put(
      '/meals/$mealId/feedback/suggestions',
      body: {'foodCardId': foodCardId},
    );
    return MealFeedback.fromJson(result.dataMap);
  }

  /// `GET /insights/daily?date=` — [date] 는 KST 날짜.
  Future<DailyFeedback> fetchDailyFeedback(DateTime date) async {
    final result = await _client.get(
      '/insights/daily',
      query: {'date': formatApiDate(date)},
    );
    return DailyFeedback.fromJson(result.dataMap);
  }

  /// `POST /insights/daily/refresh` — 새로 만들어 달라고 한다(202).
  /// 다음에 다시 물어볼 간격을 돌려준다.
  Future<Duration> refreshDailyFeedback(DateTime date) async {
    final result = await _client.post(
      '/insights/daily/refresh',
      body: {'date': formatApiDate(date)},
    );
    return Duration(
      milliseconds: result.dataMap['pollIntervalMs'] as int? ?? 1500,
    );
  }

  /// `POST /meals/{mealId}/satiety-checkins` — 식사 몇 시간 뒤 포만감.
  Future<void> addSatietyCheckin(
    String mealId, {
    required int checkinOffsetHours,
    required int satietyPct,
    int? hungerReturnMinutes,
  }) => _client.post(
    '/meals/$mealId/satiety-checkins',
    body: {
      'checkinOffsetHours': checkinOffsetHours,
      'satietyPct': satietyPct,
      'hungerReturnMinutes': hungerReturnMinutes,
    },
  );
}
