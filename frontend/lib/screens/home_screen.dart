import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/medication_api.dart';
import '../common/api_format.dart';
import '../popups/popup_gate.dart';
import '../state/medication_state.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_input_screen.dart';
import 'meal_review_screen.dart';
import 'next_meal_suggestion_screen.dart';
import 'medication_info_screen.dart';
import 'shared_meal_widgets.dart';

// ── 모델 ────────────────────────────────────────────────────

/// Q·Q·S 점수. 0~100 정수. 평가 전이면 셋 다, 영양 정보가 있는 음식이
/// 없으면 양·질이 null 이다.
class MealScores {
  const MealScores({
    required this.quantity,
    required this.quality,
    required this.satiety,
    this.quantityLabel,
  });

  final int? quantity;
  final int? quality;
  final int? satiety;

  /// 양 라벨(`부족` · `적정` · `과다`).
  ///
  /// **명세 초안에 아직 없는 필드다.** Figma 는 양만 숫자가 아니라 라벨로 보여 주고
  /// 라벨 계산은 BE 가 하기로 했는데, `scores` 에 자리가 없어 지금은 null 로 온다.
  /// null 이면 [quantityDisplay] 가 숫자를 그대로 내보낸다.
  final String? quantityLabel;

  factory MealScores.fromJson(Map<String, dynamic> json) => MealScores(
    quantity: json['quantity'] as int?,
    quality: json['quality'] as int?,
    satiety: json['satiety'] as int?,
    quantityLabel: json['quantityLabel'] as String?,
  );

  String? get quantityDisplay => quantityLabel ?? quantity?.toString();
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

  /// `/home` 에만 있는 값. `GET /medications/current` 로 채우면 null 이고,
  /// 카드는 그 줄을 숨긴다.
  final bool? doseChangeScheduled;

  /// [MedicationState] 의 값으로 만든다. 투약 화면에서 저장하면 홈을 다시
  /// 부르지 않아도 카드가 바뀌어야 해서, 카드 값은 `/home` 이 아니라 이걸 쓴다.
  /// `/home` 에만 있는 [doseChangeScheduled] 는 따로 받는다.
  factory HomeMedication.fromCurrent(
    MedicationCurrent current, {
    bool? doseChangeScheduled,
  }) => HomeMedication(
    drugName: current.drugName,
    doseMg: current.doseMg,
    doseCount: current.doseCount,
    stage: current.stage,
    nextDoseDate: current.nextDoseDate,
    daysUntilNextDose: current.daysUntilNextDose,
    doseChangeScheduled: doseChangeScheduled,
  );

  factory HomeMedication.fromJson(Map<String, dynamic> json) => HomeMedication(
    drugName: json['drugName'] as String,
    doseMg: (json['doseMg'] as num).toDouble(),
    doseCount: json['doseCount'] as int,
    stage: json['stage'] as String,
    nextDoseDate: parseApiDate(json['nextDoseDate'] as String),
    daysUntilNextDose: json['daysUntilNextDose'] as int,
    doseChangeScheduled: json['doseChangeScheduled'] as bool?,
  );
}

/// `GET /home` 의 `stomach`. 기록된 식단 중 가장 최근 것 기준이다.
class HomeStomach {
  const HomeStomach({
    required this.satietyPct,
    required this.minutesSinceMeal,
    this.sourceMealId,
    this.feedbackSummary,
  });

  final int satietyPct;
  final int minutesSinceMeal;

  /// 게이지가 기준으로 삼은 끼니. 사후 포만감 체크인 팝업에 넘긴다.
  final String? sourceMealId;

  /// 나중에 도착하는 문구. 비어 있어도 게이지·목록은 정상이어야 한다
  /// (`design-system.md` §7 규칙 3).
  final String? feedbackSummary;

  factory HomeStomach.fromJson(Map<String, dynamic> json) => HomeStomach(
    satietyPct: json['satietyPct'] as int,
    minutesSinceMeal: json['minutesSinceMeal'] as int,
    sourceMealId: json['sourceMealId'] as String?,
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
  /// 아직 평가 전이면 null.
  final MealScores? scores;
  final String? thumbnailUrl;

  factory HomeMeal.fromJson(Map<String, dynamic> json) => HomeMeal(
    mealId: json['mealId'] as String,
    mealType: json['mealType'] as String,
    eatenAt: parseApiDateTime(json['eatenAt'] as String),
    displayName: json['displayName'] as String,
    thumbnailUrl: json['thumbnailUrl'] as String?,
    scores: switch (json['scores']) {
      final Map<String, dynamic> s => MealScores.fromJson(s),
      _ => null,
    },
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

  /// 투약 미등록이면 null. 홈 투약 카드는 `MedicationState` 를 보고, 여기서는
  /// `doseChangeScheduled` 만 가져다 쓴다.
  final HomeMedication? medication;

  /// 기록한 식사가 하나도 없으면 null.
  final HomeStomach? stomach;
  final int recordedCount;
  final List<HomeMeal> meals;
  final List<String> missingMealTypes;

  factory HomeSummary.fromJson(Map<String, dynamic> json) {
    final today = json['today'] as Map<String, dynamic>;
    return HomeSummary(
      date: parseApiDate(json['date'] as String),
      medication: switch (json['medication']) {
        final Map<String, dynamic> m => HomeMedication.fromJson(m),
        _ => null,
      },
      stomach: switch (json['stomach']) {
        final Map<String, dynamic> m => HomeStomach.fromJson(m),
        _ => null,
      },
      recordedCount: today['recordedCount'] as int,
      meals: (today['meals'] as List<dynamic>)
          .map((e) => HomeMeal.fromJson(e as Map<String, dynamic>))
          .toList(),
      missingMealTypes: (today['missingMealTypes'] as List<dynamic>)
          .cast<String>(),
    );
  }
}

// 표시 문구·날짜 파싱(stageLabel · mealTypeLabel · parseApiDate*)은
// `common/api_format.dart` 에 있다.

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
class HomeApiService {
  HomeApiService(this._client);

  final ApiClient _client;

  /// `GET /home`. 투약 미등록이어도 실패가 아니다(`medication: null`).
  Future<HomeSummary> fetchHome() async =>
      HomeSummary.fromJson((await _client.get('/home')).dataMap);
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
  late final HomeApiService _api = HomeApiService(context.read<ApiClient>());

  HomeSummary? _home;
  LoadState _state = LoadState.loading;

  /// 불러오는 중 · 불러온 뒤에 같은 게이지를 이어 쓰려고 쥔다(수위를 이전 값에서부터 움직인다).
  final GlobalKey _gaugeKey = GlobalKey();
  String? _errorMessage;

  /// 기록 탭에서 식사를 기록하고 돌아오면 홈은 모른다. 탭이 다시 보일 때
  /// 새로 불러온다.
  late final TabState _tabs = context.read<TabState>();

  void _onTabChanged() {
    if (_tabs.currentIndex == TabState.home) _load();
  }

  @override
  void initState() {
    super.initState();
    _tabs.addListener(_onTabChanged);
    _load();
  }

  @override
  void dispose() {
    _tabs.removeListener(_onTabChanged);
    super.dispose();
  }

  /// [refreshMedication] 이면 투약도 서버에서 다시 받는다(당겨서 새로고침).
  /// 아니면 [MedicationState] 에 이미 있는 값을 쓴다.
  Future<void> _load({bool refreshMedication = false}) async {
    final medication = context.read<MedicationState>();
    setState(() {
      _state = LoadState.loading;
      _errorMessage = null;
    });
    try {
      // Future.wait 은 먼저 난 에러 하나를 그대로 던진다(재시도 블록 문구용).
      final results = await Future.wait<Object?>([
        _api.fetchHome(),
        refreshMedication ? medication.refresh() : medication.ensureLoaded(),
      ]);
      if (!mounted) return;
      setState(() {
        _home = results[0] as HomeSummary;
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

  /// 위 게이지 탭 → 사후 포만감 체크인 팝업. 저장했으면 게이지가 바뀌니
  /// 홈을 다시 불러온다.
  Future<void> _openSatietyCheckin(String mealId, int satietyPct) async {
    final saved = await context.read<PopupGate>().showSatietyCheckin(
      context,
      mealId: mealId,
      initialSatiety: satietyPct,
    );
    if (!mounted || !saved) return;
    _load();
  }

  /// 오늘의 식사 카드 탭 — 평가한 끼니는 다음 끼니 제안(7번), 아직 확정
  /// 전이면 음식 확인 화면(5번)을 연다. 기록 탭과 같다.
  Future<void> _openMeal(HomeMeal meal) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => meal.scores?.satiety == null
            ? MealReviewScreen(mealId: meal.mealId)
            : NextMealSuggestionScreen(
                mealId: meal.mealId,
                eatenAt: meal.eatenAt,
              ),
      ),
    );
    if (!mounted) return;
    _load();
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

  /// 투약 카드 탭 → 투약 정보 화면(1번). 저장하면 [MedicationState] 가
  /// 바뀌어 카드도 같이 바뀌므로 돌아와서 다시 불러오지 않는다.
  void _openMedicationInfo() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const MedicationInfoScreen()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final home = _home;
    // 투약은 홈 응답이 아니라 MedicationState 에서 읽는다 — 다른 화면에서
    // 저장해도 여기서 바로 바뀐다.
    final current = context.watch<MedicationState>().current;
    final medication = current == null
        ? null
        : HomeMedication.fromCurrent(
            current,
            doseChangeScheduled: home?.medication?.doseChangeScheduled,
          );

    return SafeArea(
      child: RefreshIndicator(
        onRefresh: () => _load(refreshMedication: true),
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
                        style: AppTypography.body.copyWith(
                          color: AppColors.textTertiary,
                        ),
                      ),
              ),
              const SizedBox(height: AppSpacing.md),
              ...switch (_state) {
                LoadState.loading => _buildLoading(),
                LoadState.failed => _buildFailed(),
                LoadState.ready => _buildReady(home!, medication),
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
    // 게이지는 실루엣을 그대로 그린다. 자리를 비우면 완료 시점에 아래 내용이 통째로 밀린다.
    // 다시 불러오는 중에는 지금 수위를 그대로 두고(처음이면 빈 실루엣),
    // 같은 key 로 이어서 써야 새 값이 왔을 때 이전 값에서부터 움직인다.
    Center(
      child: StomachGauge(key: _gaugeKey, satiety: _home?.stomach?.satietyPct),
    ),
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
      onRetry: () => _load(refreshMedication: true),
    ),
  ];

  List<Widget> _buildReady(HomeSummary home, HomeMedication? medication) {
    final stomach = home.stomach;
    final nextMealType = home.missingMealTypes.isEmpty
        ? null
        : mealTypeLabel(home.missingMealTypes.first);

    return [
      if (medication == null)
        AddMealCard(label: '＋ 투약 정보 입력하기', onTap: _openMedicationInfo)
      else
        _MedicationStatusCard(
          medication: medication,
          onTap: _openMedicationInfo,
        ),
      const SizedBox(height: AppSpacing.xl),

      // 위 게이지 영역 탭 → 사후 포만감 체크인 팝업. 기준 끼니가 없으면 막는다.
      InkWell(
        onTap: stomach?.sourceMealId == null
            ? null
            : () => _openSatietyCheckin(
                stomach!.sourceMealId!,
                stomach.satietyPct,
              ),
        borderRadius: AppRadius.lgRadius,
        child: Column(
          children: [
            Center(
              child: StomachGauge(
                key: _gaugeKey,
                satiety: stomach?.satietyPct ?? 0,
              ),
            ),
            const SizedBox(height: AppSpacing.lg),

            if (stomach == null)
              const EmptyBlock(message: '아직 기록된 식사가 없어요')
            else
              Center(
                child: Column(
                  children: [
                    Text(
                      '${stomach.satietyPct}%',
                      style: AppTypography.gaugeValue.copyWith(
                        fontSize: 21,
                        color: AppColors.textPrimary,
                      ),
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      _formatElapsed(stomach.minutesSinceMeal),
                      style: AppTypography.body,
                    ),
                  ],
                ),
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
          quantityLabel: meal.scores?.quantityDisplay,
          quality: meal.scores?.quality,
          satiety: meal.scores?.satiety,
          thumbnailUrl: meal.thumbnailUrl,
          showChevron: true,
          onTap: () => _openMeal(meal),
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
        Text(
          '오늘의 식사',
          style: AppTypography.sectionHead.copyWith(fontSize: 14.5),
        ),
        const Spacer(),
        if (trailing != null)
          Text(
            trailing!,
            style: AppTypography.caption.copyWith(fontSize: 11),
          ),
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
          // Figma 카드는 위아래가 좁아 높이가 약 75 다.
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.md,
            9,
            AppSpacing.md,
            AppSpacing.sm,
          ),
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
                          // 주사기는 35px 이지만 배지 줄 높이만 차지하고 위아래로 겹쳐 나온다.
                          SizedBox(
                            width: 35,
                            height: 20,
                            child: OverflowBox(
                              maxWidth: 35,
                              maxHeight: 35,
                              child: Image.asset(
                                'assets/images/syringe.png',
                                width: 35,
                                height: 35,
                                fit: BoxFit.cover,
                              ),
                            ),
                          ),
                          const SizedBox(width: AppSpacing.sm),
                          StageBadge(label: stageLabel(medication.stage)),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(
                        '${medication.drugName} ${_formatDose(medication.doseMg)}',
                        style: AppTypography.sectionHead.copyWith(fontSize: 15),
                      ),
                      const SizedBox(height: 2),
                      // Figma 는 `12회차 · 투약 10주차` 인데 명세에는 doseCount 뿐이라
                      // 주차를 만들 수 없다. startedAt 이 /home 에 없다.
                      Text(
                        '${medication.doseCount}회차',
                        style: AppTypography.caption.copyWith(
                          fontSize: 10.5,
                          color: AppColors.textPrimary,
                        ),
                      ),
                    ],
                  ),
                ),
                const VerticalDivider(width: AppSpacing.lg),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      '다음 투약',
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: [
                        Text(
                          'D-${medication.daysUntilNextDose}',
                          style: AppTypography.sectionHead.copyWith(
                            fontSize: 14.5,
                            color: AppColors.primary,
                          ),
                        ),
                        const SizedBox(width: AppSpacing.sm),
                        Text(
                          _formatShortDate(medication.nextDoseDate),
                          style: AppTypography.caption.copyWith(
                            fontSize: 11,
                            color: AppColors.textPrimary,
                          ),
                        ),
                      ],
                    ),
                    if (medication.doseChangeScheduled case final scheduled?) ...[
                      const SizedBox(height: 2),
                      Text(
                        scheduled ? '증량 예정 있음' : '증량 예정 없음',
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
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
/// Figma `화면 2 · 홈 (수정)` 의 위 그룹(식도 · 몸통 · 꼬리 · 채움)을 SVG 원본 좌표
/// 그대로 `CustomPaint` 로 옮겼다. 수치는 이 위젯이 그리지 않고 아래에 따로 놓는다.
///
/// 움직임:
/// - 수면은 항상 잔잔하게 찰랑인다.
/// - 값이 바뀌면 **이전 값에서 새 값까지** 수위가 부드럽게 오르거나 내린다.
///   바뀐 폭이 클수록 오래 걸리고, 그 사이 출렁임이 커졌다가 잦아든다.
/// - 처음 값을 받을 때는 비어 있던 상태가 아니라 그 값 그대로 보여 준다.
/// - 기기가 "애니메이션 줄이기" 이면 움직임 없이 값만 보여 준다.
///
/// 이전 값을 기억해야 하므로, 화면을 다시 불러오는 동안에도 같은 위젯(같은 `key`)을
/// 유지해서 쓴다.
class StomachGauge extends StatefulWidget {
  /// [satiety] 가 null 이면 값을 아직 모르는 상태라 수위를 바꾸지 않는다
  /// (처음 불러오는 중에는 빈 실루엣, 다시 불러오는 중에는 지금 수위 그대로).
  const StomachGauge({super.key, required this.satiety});

  /// 0~100.
  final int? satiety;

  static const double _width = 175;
  static const double _height = 193;

  @override
  State<StomachGauge> createState() => _StomachGaugeState();
}

class _StomachGaugeState extends State<StomachGauge>
    with TickerProviderStateMixin {
  /// 수면이 찰랑이는 움직임. 계속 돈다.
  late final AnimationController _wave = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2600),
  );

  /// 수위가 이전 값에서 새 값까지 움직이는 진행도.
  late final AnimationController _level = AnimationController(vsync: this);
  late final Animation<double> _levelCurve = CurvedAnimation(
    parent: _level,
    curve: Curves.easeInOutCubic,
  );

  /// 수위가 바뀐 직후의 출렁임(1 → 0 으로 잦아든다).
  late final AnimationController _slosh = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 2400),
  );

  double _from = 0;
  double _to = 0;
  bool _hasValue = false;
  bool _reduceMotion = false;

  /// 지금 화면에 보이는 수위(0~1).
  double get _ratio => _from + (_to - _from) * _levelCurve.value;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _reduceMotion = MediaQuery.disableAnimationsOf(context);
    if (_reduceMotion) {
      _wave.stop();
    } else if (!_wave.isAnimating) {
      _wave.repeat();
    }
    if (!_hasValue) _moveTo(widget.satiety);
  }

  @override
  void didUpdateWidget(StomachGauge oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.satiety != widget.satiety) _moveTo(widget.satiety);
  }

  void _moveTo(int? satiety) {
    if (satiety == null) return;
    final target = satiety / 100;
    // 처음 받은 값이거나 움직임을 줄인 경우에는 그대로 보여 준다.
    if (!_hasValue || _reduceMotion) {
      _hasValue = true;
      _from = _to = target;
      _level.value = 1;
      _slosh.value = 0;
      return;
    }
    _from = _ratio;
    _to = target;
    final change = (_to - _from).abs();
    if (change == 0) return;
    // 바뀐 폭이 클수록 오래 걸리고 더 크게 출렁인다.
    _level.duration = Duration(milliseconds: (700 + 1300 * change).round());
    _level.forward(from: 0);
    _slosh.reverse(from: math.min(1.0, 0.4 + change * 1.2));
  }

  @override
  void dispose() {
    _wave.dispose();
    _level.dispose();
    _slosh.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: StomachGauge._width,
      height: StomachGauge._height,
      child: RepaintBoundary(
        child: AnimatedBuilder(
          animation: Listenable.merge([_wave, _level, _slosh]),
          builder: (context, _) => CustomPaint(
            painter: _StomachPainter(
              fillRatio: _hasValue ? _ratio : 0,
              phase: _wave.value * 2 * math.pi,
              amplitude: 2.2 + 4.8 * _slosh.value,
            ),
          ),
        ),
      ),
    );
  }
}

class _StomachPainter extends CustomPainter {
  const _StomachPainter({
    required this.fillRatio,
    required this.phase,
    required this.amplitude,
  });

  /// 0.0 ~ 1.0.
  final double fillRatio;

  /// 수면 물결의 위상(0 ~ 2π). 한 바퀴 돌면 처음과 같아 끊김 없이 이어진다.
  final double phase;

  /// 수면 물결의 높이(px, 기준 좌표계).
  final double amplitude;

  /// 패스를 그린 기준 좌표계(Figma 위 그룹 크기). 실제 크기에 맞춰 스케일한다.
  static const Size reference = Size(StomachGauge._width, StomachGauge._height);

  /// 식도 아래 몸통이 시작하는 높이와 몸통 높이.
  static const double _bodyTop = 26.8357;
  static const double _bodyHeight = 165.369;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / reference.width, size.height / reference.height);

    // 식도 · 꼬리는 회색 면 하나로 보인다.
    final track = Paint()..color = AppColors.surfaceMuted;
    canvas.drawPath(_esophagus(), track);
    canvas.drawPath(
      _tail(),
      Paint()
        ..color = AppColors.surfaceMuted
        ..style = PaintingStyle.stroke
        ..strokeWidth = 14.85
        ..strokeCap = StrokeCap.round,
    );

    // 몸통의 빈 부분.
    final body = _body();
    canvas.drawPath(body, track);

    // 채운 부분. 0% 에서는 몸통 아래로 완전히 숨고, 100% 에서는 물결 마루까지 덮는다.
    // 몸통 안에서만 보이고 윗면은 물결이다.
    final ratio = fillRatio.clamp(0.0, 1.0);
    if (ratio > 0.001) {
      final bottom = _bodyTop + _bodyHeight + 8;
      final top = _bodyTop - 10;
      canvas.save();
      canvas.clipPath(body);
      canvas.drawPath(
        _wave(bottom - ratio * (bottom - top)),
        Paint()..color = AppColors.stomach,
      );
      canvas.restore();
    }

    canvas.restore();
  }

  /// 가로 [x] 에서의 수면 높이. 큰 물결과 작은 물결을 겹친다.
  double _surfaceY(double x, double top) {
    final big = amplitude * math.sin(x * 2 * math.pi / 86 + phase);
    final small = amplitude * 0.45 * math.sin(x * 2 * math.pi / 41 - 2 * phase);
    return top + big + small;
  }

  /// 채움의 물결 윗면. 몸통보다 넓게 그려서 가로로 빈틈이 없게 한다.
  Path _wave(double top) {
    const from = -10.0;
    const to = 185.0;
    final path = Path()..moveTo(from, _surfaceY(from, top));
    for (var x = from + 3; x <= to; x += 3) {
      path.lineTo(x, _surfaceY(x, top));
    }
    return path
      ..lineTo(to, top + 400)
      ..lineTo(from, top + 400)
      ..close();
  }

  /// 위 몸통. Figma 벡터 `stomachClip` 좌표 그대로.
  Path _body() => Path()
    ..moveTo(69.534, _bodyTop)
    ..cubicTo(
      36.9989,
      _bodyTop + 4.47262,
      2.29489,
      _bodyTop + 29.072,
      0.125885,
      _bodyTop + 71.5619,
    )
    ..cubicTo(
      -2.04312,
      _bodyTop + 114.052,
      23.9849,
      _bodyTop + 154.305,
      65.196,
      _bodyTop + 163.251,
    )
    ..cubicTo(
      95.562,
      _bodyTop + 171.078,
      123.759,
      _bodyTop + 156.542,
      135.689,
      _bodyTop + 130.824,
    )
    ..cubicTo(
      140.027,
      _bodyTop + 120.761,
      125.928,
      _bodyTop + 115.17,
      117.252,
      _bodyTop + 108.461,
    )
    ..cubicTo(
      105.323,
      _bodyTop + 98.3977,
      99.9,
      _bodyTop + 80.5072,
      95.562,
      _bodyTop + 58.1441,
    )
    ..cubicTo(
      92.3085,
      _bodyTop + 31.3083,
      89.055,
      _bodyTop + 15.6542,
      86.886,
      _bodyTop,
    )
    ..close();

  /// 몸통 위로 올라가는 식도(위쪽이 살짝 좁은 사다리꼴).
  Path _esophagus() => Path()
    ..moveTo(67.365, 0)
    ..lineTo(67.365 + 2.169, _bodyTop)
    ..lineTo(67.365 + 19.521, _bodyTop)
    ..lineTo(67.365 + 16.2675, 0)
    ..close();

  /// 우하단으로 빠지는 꼬리(굵은 선).
  Path _tail() => Path()
    ..moveTo(123.3 + 7.4259, 130 + 23.0799)
    ..cubicTo(
      123.3 + 24.7779,
      130 + 34.2615,
      123.3 + 42.13,
      130 + 25.3162,
      123.3 + 44.299,
      130 + 7.42575,
    );

  @override
  bool shouldRepaint(_StomachPainter old) =>
      old.fillRatio != fillRatio ||
      old.phase != phase ||
      old.amplitude != amplitude;
}
