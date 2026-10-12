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
                        style: AppTypography.bodySecondary,
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
                      _formatElapsed(stomach.minutesSinceMeal),
                      style: AppTypography.bodySecondary,
                    ),
                    // 문구 생성이 실패하면 이 줄만 빠지고 나머지는 정상이다.
                    if (stomach.feedbackSummary != null)
                      Text(
                        stomach.feedbackSummary!,
                        textAlign: TextAlign.center,
                        style: AppTypography.bodySecondary,
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
                    if (medication.doseChangeScheduled case final scheduled?) ...[
                      const SizedBox(height: 2),
                      Text(
                        scheduled ? '증량 예정 있음' : '증량 예정 없음',
                        style: AppTypography.caption,
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
