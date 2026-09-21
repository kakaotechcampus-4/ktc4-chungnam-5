import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_input_screen.dart';
import 'medication_info_screen.dart';
import 'shared_meal_widgets.dart';

// ── 모델 ────────────────────────────────────────────────────

/// Q·Q·S 점수. 셋 다 0~100 정수다.
class MealScores {
  const MealScores({
    required this.quantity,
    required this.quality,
    required this.satiety,
    this.quantityLabel,
  });

  final int quantity;
  final int quality;
  final int satiety;

  /// 양 라벨(`부족` · `적정` · `과다`).
  ///
  /// **명세 초안에 아직 없는 필드다.** Figma 는 양만 숫자가 아니라 라벨로 보여 주고
  /// 라벨 계산은 BE 가 하기로 했는데, `scores` 에 자리가 없어 지금은 null 로 온다.
  /// null 이면 [quantityDisplay] 가 숫자를 그대로 내보낸다.
  final String? quantityLabel;

  factory MealScores.fromJson(Map<String, dynamic> json) => MealScores(
    quantity: json['quantity'] as int,
    quality: json['quality'] as int,
    satiety: json['satiety'] as int,
    quantityLabel: json['quantityLabel'] as String?,
  );

  String get quantityDisplay => quantityLabel ?? '$quantity';
}

/// `GET /home` 의 `medication`.
class HomeMedication {
  const HomeMedication({
    required this.drugName,
    required this.doseMg,
    required this.doseCount,
    required this.stage,
    required this.nextDoseDate,
    required this.daysUntilNextDose,
    required this.doseChangeScheduled,
  });

  final String drugName;
  final double doseMg;
  final int doseCount;
  final String stage;
  final DateTime nextDoseDate;
  final int daysUntilNextDose;
  final bool doseChangeScheduled;

  factory HomeMedication.fromJson(Map<String, dynamic> json) => HomeMedication(
    drugName: json['drugName'] as String,
    doseMg: (json['doseMg'] as num).toDouble(),
    doseCount: json['doseCount'] as int,
    stage: json['stage'] as String,
    nextDoseDate: parseApiDate(json['nextDoseDate'] as String),
    daysUntilNextDose: json['daysUntilNextDose'] as int,
    doseChangeScheduled: json['doseChangeScheduled'] as bool,
  );
}

/// `GET /home` 의 `stomach`. 기록된 식단 중 가장 최근 것 기준이다.
class HomeStomach {
  const HomeStomach({
    required this.satietyPct,
    required this.minutesSinceMeal,
    this.feedbackSummary,
  });

  final int satietyPct;
  final int minutesSinceMeal;

  /// 나중에 도착하는 문구. 비어 있어도 게이지·목록은 정상이어야 한다
  /// (`design-system.md` §7 규칙 3).
  final String? feedbackSummary;

  factory HomeStomach.fromJson(Map<String, dynamic> json) => HomeStomach(
    satietyPct: json['satietyPct'] as int,
    minutesSinceMeal: json['minutesSinceMeal'] as int,
    feedbackSummary: json['feedbackSummary'] as String?,
  );
}

/// `GET /home` 의 `today.meals[]` 한 끼.
class HomeMeal {
  const HomeMeal({
    required this.mealId,
    required this.mealType,
    required this.eatenAt,
    required this.displayName,
    required this.scores,
    this.thumbnailUrl,
  });

  final String mealId;
  final String mealType;
  final DateTime eatenAt;
  final String displayName;
  final MealScores scores;
  final String? thumbnailUrl;

  factory HomeMeal.fromJson(Map<String, dynamic> json) => HomeMeal(
    mealId: json['mealId'] as String,
    mealType: json['mealType'] as String,
    eatenAt: parseApiDateTime(json['eatenAt'] as String),
    displayName: json['displayName'] as String,
    thumbnailUrl: json['thumbnailUrl'] as String?,
    scores: MealScores.fromJson(json['scores'] as Map<String, dynamic>),
  );
}

/// `GET /home` 전체.
class HomeSummary {
  const HomeSummary({
    required this.date,
    required this.medication,
    required this.stomach,
    required this.recordedCount,
    required this.meals,
    required this.missingMealTypes,
  });

  final DateTime date;
  final HomeMedication medication;
  final HomeStomach stomach;
  final int recordedCount;
  final List<HomeMeal> meals;
  final List<String> missingMealTypes;

  factory HomeSummary.fromJson(Map<String, dynamic> json) {
    final today = json['today'] as Map<String, dynamic>;
    return HomeSummary(
      date: parseApiDate(json['date'] as String),
      medication: HomeMedication.fromJson(
        json['medication'] as Map<String, dynamic>,
      ),
      stomach: HomeStomach.fromJson(json['stomach'] as Map<String, dynamic>),
      recordedCount: today['recordedCount'] as int,
      meals: (today['meals'] as List<dynamic>)
          .map((e) => HomeMeal.fromJson(e as Map<String, dynamic>))
          .toList(),
      missingMealTypes: (today['missingMealTypes'] as List<dynamic>)
          .cast<String>(),
    );
  }
}

// ── 표시 문구 ────────────────────────────────────────────────
// 열거형 → 한글 변환. 지금은 화면마다 갖고 있는데, 공용 모델 폴더가 생기면
// 한곳으로 모아야 한다. 화면마다 다른 말이 되면 바로 티가 난다.

String stageLabel(String stage) => switch (stage) {
  'INITIAL' => '도입기',
  'TITRATION' => '증량기',
  'MAINTENANCE' => '유지기',
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

const List<String> _weekdayNames = ['월', '화', '수', '목', '금', '토', '일'];

String _formatFullDate(DateTime d) =>
    '${d.month}월 ${d.day}일 ${_weekdayNames[d.weekday - 1]}요일';

String _formatShortDate(DateTime d) =>
    '${d.month}/${d.day} (${_weekdayNames[d.weekday - 1]})';

String _formatTime(DateTime d) =>
    '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

String _formatElapsed(int minutes) {
  final h = minutes ~/ 60;
  final m = minutes % 60;
  if (h == 0) return '마지막 식사 후 $m분';
  if (m == 0) return '마지막 식사 후 $h시간';
  return '마지막 식사 후 $h시간 $m분';
}

/// `1.0` → `1.0mg`, `2.4` → `2.4mg`. Figma 도 소수점 한 자리로 적는다.
String _formatDose(double mg) => '${mg}mg';

// ── API ─────────────────────────────────────────────────────

/// 홈 화면이 쓰는 엔드포인트.
///
/// 지금은 명세 예시를 그대로 돌려준다. 통신 라이브러리가 정해지면
/// 메서드 본문만 교체하면 되고 화면은 건드리지 않는다.
class HomeApiService {
  /// `GET /home`
  Future<HomeSummary> fetchHome() async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    //   응답 래퍼 { success, data, error } 를 벗기고 data 를 넘긴다.
    //   error.code 분기: PROFILE_REQUIRED -> 프로필 입력,
    //                    STAGE_NOT_SET   -> 투약 정보 입력.
    //   둘 다 실패가 아니라 이동이라 재시도 블록을 띄우지 않는다.
    await Future.delayed(const Duration(milliseconds: 300));
    return HomeSummary.fromJson(_sample);
  }

  /// 명세의 `GET /home` 예시 응답(`data` 안쪽).
  static const Map<String, dynamic> _sample = {
    'date': '2026-08-21',
    'medication': {
      'drugName': '위고비',
      'doseMg': 1.0,
      'doseCount': 10,
      'stage': 'MAINTENANCE',
      'nextDoseDate': '2026-08-23',
      'daysUntilNextDose': 2,
      'doseChangeScheduled': false,
    },
    'stomach': {
      'satietyPct': 68,
      'sourceMealId': 'meal_456',
      'sourceMealAt': '2026-08-21T12:40:00+09:00',
      'minutesSinceMeal': 320,
      'feedbackSummary': '유지기 기준 포만감이 부족한 식사였어요.',
    },
    'today': {
      'recordedCount': 2,
      'meals': [
        {
          'mealId': 'meal_450',
          'mealType': 'BREAKFAST',
          'eatenAt': '2026-08-21T08:20:00+09:00',
          'displayName': '토스트, 그릭요거트',
          'thumbnailUrl': null,
          'scores': {'quantity': 74, 'quality': 90, 'satiety': 80},
        },
        {
          'mealId': 'meal_456',
          'mealType': 'LUNCH',
          'eatenAt': '2026-08-21T12:40:00+09:00',
          'displayName': '현미밥, 된장국, 두부조림',
          'thumbnailUrl': null,
          'scores': {'quantity': 76, 'quality': 80, 'satiety': 68},
        },
      ],
      'missingMealTypes': ['DINNER'],
    },
  };
}

// ── 화면 ────────────────────────────────────────────────────

/// 홈 — 투약 상태 · 위 게이지 · 오늘의 식사.
///
/// Figma `hOxrHBitBpjwIBBg2GO49y` node `60:189`.
/// `GET /home` 단일 호출이라 화면 전체가 하나의 [LoadState] 로 움직인다.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final HomeApiService _api = HomeApiService();

  HomeSummary? _home;
  LoadState _state = LoadState.loading;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _state = LoadState.loading;
      _errorMessage = null;
    });
    try {
      final result = await _api.fetchHome();
      if (!mounted) return;
      setState(() {
        _home = result;
        _state = LoadState.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '불러오는 데 실패했어요: $e';
        _state = LoadState.failed;
      });
    }
  }

  /// `＋ 저녁 식사 기록하기` 카드 탭 → 식사 입력 화면(3번). 돌아오면 끼니가
  /// 새로 기록됐을 수 있으니 홈을 다시 불러온다.
  Future<void> _openMealInput() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const MealInputScreen()),
    );
    if (!mounted) return;
    _load();
  }

  /// 투약 카드 탭 → 투약 정보 화면(1번). 돌아오면 투약 정보가 바뀌었을 수
  /// 있으니 홈을 다시 불러온다.
  Future<void> _openMedicationInfo() async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const MedicationInfoScreen()),
    );
    if (!mounted) return;
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final home = _home;

    return SafeArea(
      child: RefreshIndicator(
        onRefresh: _load,
        color: AppColors.primary,
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.screenHorizontal,
            AppSpacing.titleTop,
            AppSpacing.screenHorizontal,
            AppLayout.scrollBottomPadding,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 날짜는 응답이 오기 전엔 비워 둔다. 자리는 유지한다.
              SizedBox(
                height: 18,
                child: home == null
                    ? null
                    : Text(
                        _formatFullDate(home.date),
                        style: AppTypography.bodySecondary,
                      ),
              ),
              const SizedBox(height: AppSpacing.md),
              ...switch (_state) {
                LoadState.loading => _buildLoading(),
                LoadState.failed => _buildFailed(),
                LoadState.ready => _buildReady(home!),
              },
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _buildLoading() => [
    const SkeletonBox(width: double.infinity, height: 75),
    const SizedBox(height: AppSpacing.xl),
    // 게이지는 실루엣을 그대로 그리고 채움만 0 으로 둔다.
    // 자리를 비우면 완료 시점에 아래 내용이 통째로 밀린다.
    const Center(child: StomachGauge(satiety: 0, showValue: false)),
    const SizedBox(height: AppSpacing.xl),
    const _SectionHeader(trailing: null),
    const SizedBox(height: AppSpacing.md),
    const MealCardSkeleton(),
    const SizedBox(height: AppSpacing.cardGap),
    const MealCardSkeleton(),
  ];

  List<Widget> _buildFailed() => [
    const SizedBox(height: AppSpacing.xxl),
    RetryBlock(
      message: _errorMessage ?? '잠시 후 다시 시도해 주세요',
      onRetry: _load,
    ),
  ];

  List<Widget> _buildReady(HomeSummary home) {
    final hasMeals = home.meals.isNotEmpty;
    final nextMealType = home.missingMealTypes.isEmpty
        ? null
        : mealTypeLabel(home.missingMealTypes.first);

    return [
      _MedicationStatusCard(
        medication: home.medication,
        onTap: _openMedicationInfo,
      ),
      const SizedBox(height: AppSpacing.xl),

      Center(
        child: StomachGauge(satiety: hasMeals ? home.stomach.satietyPct : 0),
      ),
      const SizedBox(height: AppSpacing.lg),

      if (!hasMeals)
        const EmptyBlock(message: '아직 기록된 식사가 없어요')
      else
        Center(
          child: Column(
            children: [
              Text(
                _formatElapsed(home.stomach.minutesSinceMeal),
                style: AppTypography.bodySecondary,
              ),
              // 문구 생성이 실패하면 이 줄만 빠지고 나머지는 정상이다.
              if (home.stomach.feedbackSummary != null)
                Text(
                  home.stomach.feedbackSummary!,
                  textAlign: TextAlign.center,
                  style: AppTypography.bodySecondary,
                ),
            ],
          ),
        ),
      const SizedBox(height: AppSpacing.xl),

      _SectionHeader(trailing: '${home.recordedCount}끼 기록됨'),
      const SizedBox(height: AppSpacing.md),

      for (final meal in home.meals) ...[
        MealCard(
          mealTypeLabel: mealTypeLabel(meal.mealType),
          time: _formatTime(meal.eatenAt),
          foodNames: meal.displayName,
          quantityLabel: meal.scores.quantityDisplay,
          quality: meal.scores.quality,
          satiety: meal.scores.satiety,
        ),
        const SizedBox(height: AppSpacing.cardGap),
      ],

      // 부제(`아래 카메라 버튼으로...`)는 중앙 카메라 버튼이 미확정이라 넣지 않는다.
      if (nextMealType != null)
        AddMealCard(
          label: '＋ $nextMealType 식사 기록하기',
          onTap: _openMealInput,
        ),
    ];
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.trailing});

  /// `2끼 기록됨`. 로딩 중에는 개수를 모르므로 비운다.
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Text('오늘의 식사', style: AppTypography.sectionHead),
        const Spacer(),
        if (trailing != null) Text(trailing!, style: AppTypography.caption),
      ],
    );
  }
}

/// 투약 상태 카드 — 좌측 현재 투약, 우측 다음 투약.
class _MedicationStatusCard extends StatelessWidget {
  const _MedicationStatusCard({required this.medication, this.onTap});

  final HomeMedication medication;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: AppRadius.lgRadius,
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.md),
          child: IntrinsicHeight(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Row(
                        children: [
                          // Figma 는 주사기 일러스트다. 에셋이 들어오면 교체한다.
                          const Icon(
                            Icons.vaccines_outlined,
                            size: 20,
                            color: AppColors.primary,
                          ),
                          const SizedBox(width: AppSpacing.sm),
                          StageBadge(label: stageLabel(medication.stage)),
                        ],
                      ),
                      const SizedBox(height: AppSpacing.sm),
                      Text(
                        '${medication.drugName} ${_formatDose(medication.doseMg)}',
                        style: AppTypography.sectionHead,
                      ),
                      const SizedBox(height: 2),
                      // Figma 는 `12회차 · 투약 10주차` 인데 명세에는 doseCount 뿐이라
                      // 주차를 만들 수 없다. startedAt 이 /home 에 없다.
                      Text(
                        '${medication.doseCount}회차',
                        style: AppTypography.caption,
                      ),
                    ],
                  ),
                ),
                const VerticalDivider(width: AppSpacing.lg),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('다음 투약', style: AppTypography.caption),
                    const SizedBox(height: AppSpacing.xs),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: [
                        Text(
                          'D-${medication.daysUntilNextDose}',
                          style: AppTypography.sectionHead.copyWith(
                            color: AppColors.primary,
                          ),
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Text(
                          _formatShortDate(medication.nextDoseDate),
                          style: AppTypography.caption,
                        ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      medication.doseChangeScheduled
                          ? '증량 예정 있음'
                          : '증량 예정 없음',
                      style: AppTypography.caption,
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 위(胃) 게이지. 채움 높이 = 포만감 %.
///
/// [_StomachPainter] 의 패스는 Figma 실루엣을 손으로 근사한 것이다.
/// 원본 벡터를 SVG 로 내보내 교체하는 게 최종 형태다(`flutter_svg` + `ClipPath`).
/// 지금은 의존성·에셋을 늘리지 않으려고 `CustomPaint` 로만 그린다.
class StomachGauge extends StatelessWidget {
  const StomachGauge({
    super.key,
    required this.satiety,
    this.showValue = true,
  });

  /// 0~100.
  final int satiety;

  /// 로딩 중에는 수치를 감추고 실루엣만 남긴다.
  final bool showValue;

  static const double _width = 208;
  static const double _height = 235;

  @override
  Widget build(BuildContext context) {
    // 채움이 낮으면 수치가 회색 면 위에 얹혀 대비가 죽는다.
    final onFill = satiety >= 35;

    return SizedBox(
      width: _width,
      height: _height,
      child: Stack(
        // Figma 는 수치를 정중앙이 아니라 살짝 아래(채운 영역 한가운데)에 둔다.
        // 위 실루엣이 좌측으로 치우쳐 있어 가로도 조금 왼쪽으로 민다.
        alignment: const Alignment(-0.08, 0.22),
        children: [
          Positioned.fill(
            child: CustomPaint(
              painter: _StomachPainter(fillRatio: satiety / 100),
            ),
          ),
          if (showValue)
            Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  '$satiety%',
                  style: AppTypography.gaugeValue.copyWith(
                    color: onFill
                        ? AppColors.textInverse
                        : AppColors.textPrimary,
                  ),
                ),
                Text(
                  '지금 포만감',
                  style: AppTypography.body.copyWith(
                    color: onFill
                        ? AppColors.textInverse
                        : AppColors.textSecondary,
                  ),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _StomachPainter extends CustomPainter {
  const _StomachPainter({required this.fillRatio});

  /// 0.0 ~ 1.0.
  final double fillRatio;

  /// 패스를 그린 기준 좌표계. 실제 크기에 맞춰 스케일한다.
  static const Size _reference = Size(208, 235);

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(
      size.width / _reference.width,
      size.height / _reference.height,
    );

    canvas.clipPath(_buildPath());

    // 빈 부분. Figma 에는 어두운 윤곽선이 없고 밝은 회색 면으로만 보인다
    // (디자인 가이드 §5 의 1.5px 윤곽선과 다르며, Figma 를 기준으로 삼았다).
    canvas.drawRect(
      Offset.zero & _reference,
      Paint()..color = AppColors.surfaceMuted,
    );

    // 채운 부분. 아래에서 위로 fillRatio 만큼.
    final ratio = fillRatio.clamp(0.0, 1.0);
    if (ratio > 0) {
      final fillRect = Rect.fromLTRB(
        0,
        _reference.height * (1 - ratio),
        _reference.width,
        _reference.height,
      );
      canvas.drawRect(
        fillRect,
        Paint()
          ..shader = const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [AppColors.gaugeFrom, AppColors.gaugeTo],
          ).createShader(fillRect),
      );
    }

    canvas.restore();
  }

  /// 위 몸통 + 상단 분문(식도) + 우하단 유문 쪽 꼬리를 하나로 합친다.
  ///
  /// 세 조각의 감는 방향이 서로 달라 상쇄되는 걸 막으려고
  /// `Path.combine` 으로 명시적으로 합집합을 만든다.
  Path _buildPath() {
    // 몸통. 좌측은 볼록(대만곡), 우측은 오목(소만곡)하게 잡는다.
    final body = Path()
      ..moveTo(96, 36)
      ..cubicTo(60, 42, 38, 70, 34, 104)
      ..cubicTo(30, 134, 10, 154, 8, 182)
      ..cubicTo(5, 216, 40, 240, 82, 240)
      ..cubicTo(126, 240, 160, 216, 166, 184)
      ..cubicTo(171, 160, 162, 142, 150, 126)
      ..cubicTo(137, 108, 130, 92, 130, 68)
      ..cubicTo(130, 46, 116, 34, 96, 36)
      ..close();

    // 분문 — 위 몸통 위로 뻗는 짧은 관.
    final cardia = Path()
      ..moveTo(94, 8)
      ..cubicTo(94, 0, 120, 0, 120, 8)
      ..lineTo(126, 70)
      ..lineTo(96, 70)
      ..close();

    // 유문 — 우하단으로 빠지는 짧은 꼬리.
    final pylorus = Path()
      ..moveTo(156, 166)
      ..cubicTo(176, 172, 190, 188, 199, 208)
      ..cubicTo(205, 219, 192, 227, 186, 216)
      ..cubicTo(177, 198, 166, 186, 150, 180)
      ..close();

    return Path.combine(
      PathOperation.union,
      body,
      Path.combine(PathOperation.union, cardia, pylorus),
    );
  }

  @override
  bool shouldRepaint(_StomachPainter oldDelegate) =>
      oldDelegate.fillRatio != fillRatio;
}
