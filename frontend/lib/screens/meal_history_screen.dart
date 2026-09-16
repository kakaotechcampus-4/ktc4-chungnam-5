import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
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

/// `GET /meals/calendar` 의 `days[]` 하루.
class CalendarDay {
  const CalendarDay({
    required this.date,
    required this.count,
    required this.recordedMealTypes,
    this.stage,
  });

  final DateTime date;
  final int count;

  /// 그 날 기록된 끼니 종류. 달력의 점 3개가 여기서 나온다.
  final List<String> recordedMealTypes;

  final String? stage;

  factory CalendarDay.fromJson(Map<String, dynamic> json) => CalendarDay(
    date: parseApiDate(json['date'] as String),
    count: json['count'] as int,
    recordedMealTypes: (json['recordedMealTypes'] as List<dynamic>)
        .cast<String>(),
    stage: json['stage'] as String?,
  );
}

/// `GET /meals/calendar` 의 `summary`.
class CalendarSummary {
  const CalendarSummary({required this.totalMeals, required this.avgScores});

  final int totalMeals;
  final MealScores avgScores;

  factory CalendarSummary.fromJson(Map<String, dynamic> json) =>
      CalendarSummary(
        totalMeals: json['totalMeals'] as int,
        avgScores: MealScores.fromJson(
          json['avgScores'] as Map<String, dynamic>,
        ),
      );
}

/// `GET /meals/calendar` 전체.
class CalendarMonth {
  const CalendarMonth({
    required this.month,
    required this.days,
    required this.summary,
  });

  /// `2026-08` 의 1일.
  final DateTime month;
  final List<CalendarDay> days;
  final CalendarSummary summary;

  factory CalendarMonth.fromJson(Map<String, dynamic> json) => CalendarMonth(
    month: parseApiDate('${json['month']}-01'),
    days: (json['days'] as List<dynamic>)
        .map((e) => CalendarDay.fromJson(e as Map<String, dynamic>))
        .toList(),
    summary: CalendarSummary.fromJson(
      json['summary'] as Map<String, dynamic>,
    ),
  );

  /// 날짜 → 그 날 기록. 없으면 null.
  CalendarDay? dayOf(int dayNumber) {
    for (final d in days) {
      if (d.date.day == dayNumber) return d;
    }
    return null;
  }
}

/// `GET /meals` 의 `items[]` 한 끼.
class HistoryMeal {
  const HistoryMeal({
    required this.mealId,
    required this.mealType,
    required this.eatenAt,
    required this.displayName,
    required this.scores,
    this.stage,
    this.thumbnailUrl,
  });

  final String mealId;
  final String mealType;
  final DateTime eatenAt;
  final String displayName;
  final MealScores scores;
  final String? stage;
  final String? thumbnailUrl;

  factory HistoryMeal.fromJson(Map<String, dynamic> json) => HistoryMeal(
    mealId: json['mealId'] as String,
    mealType: json['mealType'] as String,
    eatenAt: parseApiDateTime(json['eatenAt'] as String),
    displayName: json['displayName'] as String,
    stage: json['stage'] as String?,
    thumbnailUrl: json['thumbnailUrl'] as String?,
    scores: MealScores.fromJson(json['scores'] as Map<String, dynamic>),
  );
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

/// `2026-08` 형식. 요청 쿼리에 쓴다.
String formatApiMonth(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}';

/// `2026-08-21` 형식. 요청 쿼리에 쓴다.
String formatApiDate(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

const List<String> _weekdayNames = ['월', '화', '수', '목', '금', '토', '일'];

String _formatDayHeader(DateTime d) =>
    '${d.month}월 ${d.day}일 ${_weekdayNames[d.weekday - 1]}요일';

String _formatTime(DateTime d) =>
    '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';

// ── API ─────────────────────────────────────────────────────

/// 기록 화면이 쓰는 엔드포인트.
///
/// 지금은 명세 예시를 그대로 돌려준다. 통신 라이브러리가 정해지면
/// 메서드 본문만 교체하면 되고 화면은 건드리지 않는다.
class MealHistoryApiService {
  /// `GET /meals/calendar?month=YYYY-MM`
  Future<CalendarMonth> fetchCalendar(DateTime month) async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    await Future.delayed(const Duration(milliseconds: 300));
    return CalendarMonth.fromJson(_sampleCalendar);
  }

  /// `GET /meals?date=YYYY-MM-DD`
  ///
  /// 목록 응답에는 `nextCursor` 도 있다. 하루치는 한 페이지로 충분해서
  /// 지금은 쓰지 않지만, 기간 조회를 붙일 때 필요하다.
  Future<List<HistoryMeal>> fetchMealsByDate(DateTime date) async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    await Future.delayed(const Duration(milliseconds: 300));
    return (_sampleMeals['items'] as List<dynamic>)
        .map((e) => HistoryMeal.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// 명세의 `GET /meals/calendar` 예시. 하루만 오던 걸 화면 확인용으로 늘렸다.
  static const Map<String, dynamic> _sampleCalendar = {
    'month': '2026-08',
    'days': [
      {
        'date': '2026-08-01',
        'count': 1,
        'recordedMealTypes': ['BREAKFAST'],
        'stage': 'MAINTENANCE',
      },
      {
        'date': '2026-08-14',
        'count': 3,
        'recordedMealTypes': ['BREAKFAST', 'LUNCH', 'DINNER'],
        'stage': 'MAINTENANCE',
      },
      {
        'date': '2026-08-20',
        'count': 2,
        'recordedMealTypes': ['LUNCH', 'DINNER'],
        'stage': 'MAINTENANCE',
      },
      {
        'date': '2026-08-21',
        'count': 2,
        'recordedMealTypes': ['BREAKFAST', 'LUNCH'],
        'stage': 'MAINTENANCE',
      },
    ],
    'summary': {
      'totalMeals': 42,
      'avgScores': {'quantity': 74, 'quality': 81, 'satiety': 65},
    },
  };

  /// 명세의 `GET /meals` 예시.
  static const Map<String, dynamic> _sampleMeals = {
    'items': [
      {
        'mealId': 'meal_450',
        'mealType': 'BREAKFAST',
        'eatenAt': '2026-08-21T08:20:00+09:00',
        'stage': 'MAINTENANCE',
        'displayName': '토스트, 그릭요거트',
        'thumbnailUrl': null,
        'scores': {'quantity': 74, 'quality': 90, 'satiety': 80},
      },
      {
        'mealId': 'meal_456',
        'mealType': 'LUNCH',
        'eatenAt': '2026-08-21T12:40:00+09:00',
        'stage': 'MAINTENANCE',
        'displayName': '현미밥, 된장국, 두부조림',
        'thumbnailUrl': null,
        'scores': {'quantity': 76, 'quality': 80, 'satiety': 68},
      },
    ],
    'nextCursor': null,
  };
}

// ── 화면 ────────────────────────────────────────────────────

/// 기록 — 월 달력 + 선택한 날의 식사.
///
/// Figma `hOxrHBitBpjwIBBg2GO49y` node `94:6`.
///
/// 달력과 목록은 API 가 달라 상태를 따로 들고 있다. 한쪽이 실패해도 다른 쪽을
/// 같이 죽이지 않는다(`design-system.md` §10).
class MealHistoryScreen extends StatefulWidget {
  const MealHistoryScreen({super.key});

  @override
  State<MealHistoryScreen> createState() => _MealHistoryScreenState();
}

class _MealHistoryScreenState extends State<MealHistoryScreen> {
  final MealHistoryApiService _api = MealHistoryApiService();

  /// 더미 응답이 2026년 8월 기준이라 화면 확인용으로 그 날짜를 쓴다.
  /// 연동하면 `DateTime.now()` 로 바꾼다.
  static final DateTime _today = DateTime(2026, 8, 21);

  late DateTime _focusedMonth = DateTime(_today.year, _today.month);
  late DateTime _selectedDay = _today;

  CalendarMonth? _calendar;
  List<HistoryMeal> _meals = const [];

  LoadState _calendarState = LoadState.loading;
  LoadState _listState = LoadState.loading;
  String? _calendarError;
  String? _listError;

  @override
  void initState() {
    super.initState();
    _loadCalendar();
    _loadMeals();
  }

  Future<void> _loadCalendar() async {
    setState(() {
      // 카드를 통째로 스켈레톤으로 바꾸지 않는다. 그리드는 남기고 점만 비운다.
      // 월을 넘길 때마다 화면이 깜빡이는 걸 막기 위해서다.
      _calendarState = LoadState.loading;
      _calendarError = null;
    });
    try {
      final result = await _api.fetchCalendar(_focusedMonth);
      if (!mounted) return;
      setState(() {
        _calendar = result;
        _calendarState = LoadState.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _calendarError = '달력을 불러오지 못했어요: $e';
        _calendarState = LoadState.failed;
      });
    }
  }

  Future<void> _loadMeals() async {
    setState(() {
      _listState = LoadState.loading;
      _listError = null;
    });
    try {
      final result = await _api.fetchMealsByDate(_selectedDay);
      if (!mounted) return;
      setState(() {
        _meals = result;
        _listState = LoadState.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _listError = '식사 기록을 불러오지 못했어요: $e';
        _listState = LoadState.failed;
      });
    }
  }

  void _moveMonth(int delta) {
    setState(() {
      _focusedMonth = DateTime(_focusedMonth.year, _focusedMonth.month + delta);
    });
    _loadCalendar();
  }

  void _goToToday() {
    setState(() {
      _focusedMonth = DateTime(_today.year, _today.month);
      _selectedDay = _today;
    });
    _loadCalendar();
    _loadMeals();
  }

  void _selectDay(DateTime day) {
    if (day == _selectedDay) return;
    setState(() => _selectedDay = day);
    _loadMeals();
  }

  void _openMealInput() {
    // TODO: 식사 입력 화면(3번)이 머지되면 연결한다.
  }

  void _openEvaluation(HistoryMeal meal) {
    // TODO: 식사 평가 화면(6번)이 머지되면 연결한다. meal.mealId 를 넘긴다.
  }

  static const List<String> _weekdayLabels = [
    '일',
    '월',
    '화',
    '수',
    '목',
    '금',
    '토',
  ];

  @override
  Widget build(BuildContext context) {
    final calendar = _calendar;
    final selectedStage = calendar?.dayOf(_selectedDay.day)?.stage;

    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.screenHorizontal,
          AppSpacing.titleTop,
          AppSpacing.screenHorizontal,
          AppLayout.scrollBottomPadding,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('기록', style: AppTypography.screenTitle),
            const SizedBox(height: AppSpacing.lg),

            _MonthNavigator(
              month: _focusedMonth,
              onPrevious: () => _moveMonth(-1),
              onNext: () => _moveMonth(1),
              onToday: _goToToday,
            ),
            const SizedBox(height: AppSpacing.md),

            Card(
              child: Padding(
                padding: const EdgeInsets.all(AppSpacing.md),
                child: _calendarState == LoadState.failed
                    ? RetryBlock(
                        message: _calendarError ?? '달력을 불러오지 못했어요',
                        onRetry: _loadCalendar,
                      )
                    : Column(
                        children: [
                          Row(
                            children: [
                              for (var i = 0; i < _weekdayLabels.length; i++)
                                Expanded(
                                  child: Text(
                                    _weekdayLabels[i],
                                    textAlign: TextAlign.center,
                                    style: AppTypography.caption.copyWith(
                                      // 일요일만 코랄.
                                      color: i == 0
                                          ? AppColors.primary
                                          : AppColors.textSecondary,
                                    ),
                                  ),
                                ),
                            ],
                          ),
                          const SizedBox(height: AppSpacing.sm),
                          _MonthGrid(
                            month: _focusedMonth,
                            today: _today,
                            selectedDay: _selectedDay,
                            // 아직 집계를 못 받았으면 점을 비운다.
                            calendar: _calendarState == LoadState.ready
                                ? calendar
                                : null,
                            onSelect: _selectDay,
                          ),
                        ],
                      ),
              ),
            ),
            const SizedBox(height: AppSpacing.cardGap),

            _MonthSummaryBar(
              month: _focusedMonth,
              summary: _calendarState == LoadState.ready
                  ? calendar?.summary
                  : null,
            ),
            const SizedBox(height: AppSpacing.xl),

            Row(
              children: [
                Text(
                  _formatDayHeader(_selectedDay),
                  style: AppTypography.sectionHead,
                ),
                if (selectedStage != null) ...[
                  const SizedBox(width: AppSpacing.sm),
                  StageBadge(label: stageLabel(selectedStage)),
                ],
                const Spacer(),
                if (_listState == LoadState.ready)
                  Text('${_meals.length}끼 기록됨', style: AppTypography.caption),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            ..._buildMealList(),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildMealList() {
    switch (_listState) {
      case LoadState.loading:
        return const [
          MealCardSkeleton(),
          SizedBox(height: AppSpacing.cardGap),
          MealCardSkeleton(),
        ];

      case LoadState.failed:
        // 달력은 건드리지 않는다. 목록만 다시 받는다.
        return [
          RetryBlock(
            message: _listError ?? '식사 기록을 불러오지 못했어요',
            onRetry: _loadMeals,
          ),
        ];

      case LoadState.ready:
        if (_meals.isEmpty) {
          return [
            const EmptyBlock(message: '아직 기록된 식사가 없어요'),
            const SizedBox(height: AppSpacing.cardGap),
            AddMealCard(label: '＋ 식사 기록하기', onTap: _openMealInput),
          ];
        }
        return [
          // 기록 탭의 카드는 탭하면 결과 상세로 간다 → 우측 `>` 를 켠다.
          for (final meal in _meals) ...[
            MealCard(
              mealTypeLabel: mealTypeLabel(meal.mealType),
              time: _formatTime(meal.eatenAt),
              foodNames: meal.displayName,
              quantityLabel: meal.scores.quantityDisplay,
              quality: meal.scores.quality,
              satiety: meal.scores.satiety,
              showChevron: true,
              onTap: () => _openEvaluation(meal),
            ),
            const SizedBox(height: AppSpacing.cardGap),
          ],
          AddMealCard(label: '＋ 저녁 식사 기록하기', onTap: _openMealInput),
        ];
    }
  }
}

/// `‹ 2026년 8월 ›` + 우측 `오늘`.
class _MonthNavigator extends StatelessWidget {
  const _MonthNavigator({
    required this.month,
    required this.onPrevious,
    required this.onNext,
    required this.onToday,
  });

  final DateTime month;
  final VoidCallback onPrevious;
  final VoidCallback onNext;
  final VoidCallback onToday;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        _ArrowButton(icon: Icons.chevron_left, onTap: onPrevious),
        const SizedBox(width: AppSpacing.sm),
        Text('${month.year}년 ${month.month}월', style: AppTypography.sectionHead),
        const SizedBox(width: AppSpacing.sm),
        _ArrowButton(icon: Icons.chevron_right, onTap: onNext),
        const Spacer(),
        InkWell(
          onTap: onToday,
          borderRadius: AppRadius.pillRadius,
          child: Container(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.md,
              vertical: AppSpacing.xs,
            ),
            decoration: const BoxDecoration(
              color: AppColors.surfaceMuted,
              borderRadius: AppRadius.pillRadius,
            ),
            child: Text(
              '오늘',
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _ArrowButton extends StatelessWidget {
  const _ArrowButton({required this.icon, required this.onTap});

  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      customBorder: const CircleBorder(),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xs),
        child: Icon(
          icon,
          size: AppLayout.tabIconSize,
          color: AppColors.textSecondary,
        ),
      ),
    );
  }
}

/// 날짜 그리드. 일요일 시작, 6주 고정.
class _MonthGrid extends StatelessWidget {
  const _MonthGrid({
    required this.month,
    required this.today,
    required this.selectedDay,
    required this.onSelect,
    this.calendar,
  });

  final DateTime month;
  final DateTime today;
  final DateTime selectedDay;
  final ValueChanged<DateTime> onSelect;

  /// null 이면 아직 집계를 못 받은 것. 점을 비운다.
  final CalendarMonth? calendar;

  static const int _rows = 6;
  static const int _columns = 7;

  /// 점은 아침·점심·저녁 순서. 간식은 Figma 에 자리가 없어 목록에만 나온다.
  static const List<String> _dotMealTypes = ['BREAKFAST', 'LUNCH', 'DINNER'];

  @override
  Widget build(BuildContext context) {
    final firstDay = DateTime(month.year, month.month);
    // DateTime.weekday 는 월=1 … 일=7. 일요일 시작 인덱스로 바꾼다.
    final leadingBlanks = firstDay.weekday % 7;
    final daysInMonth = DateTime(month.year, month.month + 1, 0).day;

    return Column(
      children: [
        for (var row = 0; row < _rows; row++)
          Row(
            children: [
              for (var column = 0; column < _columns; column++)
                Expanded(
                  child: _buildCell(
                    row * _columns + column - leadingBlanks + 1,
                    daysInMonth,
                  ),
                ),
            ],
          ),
      ],
    );
  }

  Widget _buildCell(int dayNumber, int daysInMonth) {
    if (dayNumber < 1 || dayNumber > daysInMonth) {
      return const SizedBox(height: 44);
    }

    final date = DateTime(month.year, month.month, dayNumber);
    final isFuture = date.isAfter(today);
    final recorded = calendar?.dayOf(dayNumber)?.recordedMealTypes ?? const [];

    return _DayCell(
      day: dayNumber,
      isFuture: isFuture,
      isSelected: date == selectedDay,
      recordedMeals: [
        for (final type in _dotMealTypes) recorded.contains(type),
      ],
      onTap: () => onSelect(date),
    );
  }
}

class _DayCell extends StatelessWidget {
  const _DayCell({
    required this.day,
    required this.isFuture,
    required this.isSelected,
    required this.recordedMeals,
    required this.onTap,
  });

  final int day;
  final bool isFuture;
  final bool isSelected;

  /// 아침 · 점심 · 저녁 순서.
  final List<bool> recordedMeals;

  final VoidCallback onTap;

  static const double _dotSize = 4;

  @override
  Widget build(BuildContext context) {
    final textColor = isSelected
        ? AppColors.primary
        : isFuture
        ? AppColors.textTertiary
        : AppColors.textPrimary;

    return InkWell(
      onTap: isFuture ? null : onTap,
      borderRadius: AppRadius.lgRadius,
      child: Container(
        height: 44,
        margin: const EdgeInsets.all(1),
        decoration: isSelected
            ? BoxDecoration(
                color: AppColors.primaryTint,
                borderRadius: AppRadius.lgRadius,
                border: Border.all(color: AppColors.primary),
              )
            : null,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(
              '$day',
              style: AppTypography.body.copyWith(
                color: textColor,
                fontWeight: isSelected ? FontWeight.w700 : FontWeight.w400,
              ),
            ),
            const SizedBox(height: 3),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                for (var i = 0; i < recordedMeals.length; i++) ...[
                  Container(
                    width: _dotSize,
                    height: _dotSize,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: recordedMeals[i]
                          ? AppColors.primary
                          : AppColors.border,
                    ),
                  ),
                  if (i != recordedMeals.length - 1)
                    const SizedBox(width: 2),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// `8월 42끼 기록` + `평균 양 74 · 질 81 · 포만감 65`.
class _MonthSummaryBar extends StatelessWidget {
  const _MonthSummaryBar({required this.month, this.summary});

  final DateTime month;

  /// null 이면 아직 못 받은 것. 자리만 남기고 값은 비운다.
  final CalendarSummary? summary;

  @override
  Widget build(BuildContext context) {
    final s = summary;

    return Container(
      width: double.infinity,
      height: 48,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.cardPadding,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.lgRadius,
      ),
      child: s == null
          ? const Row(
              children: [
                SkeletonBox(width: 96, height: 12),
                Spacer(),
                SkeletonBox(width: 140, height: 10),
              ],
            )
          : Row(
              children: [
                Text(
                  '${month.month}월 ${s.totalMeals}끼 기록',
                  style: AppTypography.cardTitle,
                ),
                const Spacer(),
                Text(
                  '평균 양 ${s.avgScores.quantity} · '
                  '질 ${s.avgScores.quality} · '
                  '포만감 ${s.avgScores.satiety}',
                  style: AppTypography.caption,
                ),
              ],
            ),
    );
  }
}
