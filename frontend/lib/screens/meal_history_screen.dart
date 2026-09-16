import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

/// 기록 — 월 달력 + 선택한 날의 식사.
///
/// Figma `hOxrHBitBpjwIBBg2GO49y` node `94:6`.
///
/// **지금은 레이아웃 뼈대다.** 달력 점과 식사 목록 모두 아래 더미에서 나온다.
/// 연동하면:
/// - 달력 집계 → `GET /meals/calendar` (날짜별로 기록된 `MealType` 이 필요하다)
/// - 선택일 목록 → `GET /meals?date=`
/// - 월 요약 → `GET /dashboard` (월 평균)
class MealHistoryScreen extends StatefulWidget {
  const MealHistoryScreen({super.key});

  @override
  State<MealHistoryScreen> createState() => _MealHistoryScreenState();
}

class _MealHistoryScreenState extends State<MealHistoryScreen> {
  /// 더미 기준일. 연동 시 실제 오늘 날짜로 바꾼다.
  static final DateTime _today = DateTime(2026, 8, 21);

  DateTime _focusedMonth = DateTime(_today.year, _today.month);
  DateTime _selectedDay = _today;

  /// 달력(`GET /meals/calendar`)과 목록(`GET /meals?date=`)은 API 가 다르다.
  /// 상태를 따로 들고 있어야 한쪽 실패가 다른 쪽을 같이 죽이지 않는다.
  LoadState _calendarState = LoadState.ready;
  LoadState _listState = LoadState.ready;

  static const List<_HistoryMeal> _dummyMeals = [
    _HistoryMeal('아침', '08:20', '토스트, 그릭요거트', '적정', 90, 80),
    _HistoryMeal('점심', '12:40', '현미밥, 된장국, 두부조림', '적정', 80, 68),
  ];

  void _reloadCalendar() {
    // TODO: GET /meals/calendar 재요청.
    setState(() => _calendarState = LoadState.ready);
  }

  void _reloadList() {
    // TODO: GET /meals?date= 재요청.
    setState(() => _listState = LoadState.ready);
  }

  void _openMealInput() {
    // TODO: 식사 입력 화면(3번)이 머지되면 연결한다.
  }

  void _openEvaluation(_HistoryMeal meal) {
    // TODO: 식사 평가 화면(6번)이 머지되면 연결한다.
  }

  void _moveMonth(int delta) {
    setState(() {
      _focusedMonth = DateTime(_focusedMonth.year, _focusedMonth.month + delta);
      // 달만 바꾸고 그리드는 남겨 둔 채 점만 비운다. 카드를 통째로 스켈레톤으로
      // 바꾸면 월을 넘길 때마다 화면이 깜빡인다.
      _calendarState = LoadState.loading;
    });
    // TODO: 새 달 집계를 받아오면 LoadState.ready 로 되돌린다.
    setState(() => _calendarState = LoadState.ready);
  }

  void _goToToday() {
    setState(() {
      _focusedMonth = DateTime(_today.year, _today.month);
      _selectedDay = _today;
    });
  }

  static const List<String> _weekdayLabels = ['일', '월', '화', '수', '목', '금', '토'];

  @override
  Widget build(BuildContext context) {
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
                        message: '달력을 불러오지 못했어요',
                        onRetry: _reloadCalendar,
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
                            // 로딩 중에는 그리드는 그대로 두고 점만 비운다.
                            showDots: _calendarState == LoadState.ready,
                            onSelect: (day) =>
                                setState(() => _selectedDay = day),
                          ),
                        ],
                      ),
              ),
            ),
            const SizedBox(height: AppSpacing.cardGap),

            _MonthSummaryBar(month: _focusedMonth),
            const SizedBox(height: AppSpacing.xl),

            Row(
              children: [
                Text(
                  _formatDayHeader(_selectedDay),
                  style: AppTypography.sectionHead,
                ),
                const SizedBox(width: AppSpacing.sm),
                const StageBadge(label: '유지기'),
                const Spacer(),
                if (_listState == LoadState.ready)
                  Text(
                    '${_dummyMeals.length}끼 기록됨',
                    style: AppTypography.caption,
                  ),
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
          RetryBlock(message: '식사 기록을 불러오지 못했어요', onRetry: _reloadList),
        ];
      case LoadState.ready:
        if (_dummyMeals.isEmpty) {
          return [
            const EmptyBlock(message: '아직 기록된 식사가 없어요'),
            const SizedBox(height: AppSpacing.cardGap),
            AddMealCard(label: '＋ 식사 기록하기', onTap: _openMealInput),
          ];
        }
        return [
          // 기록 탭의 카드는 탭하면 결과 상세로 간다 → 우측 `>` 를 켠다.
          for (final meal in _dummyMeals) ...[
            MealCard(
              mealTypeLabel: meal.mealTypeLabel,
              time: meal.time,
              foodNames: meal.foodNames,
              quantityLabel: meal.quantityLabel,
              quality: meal.quality,
              satiety: meal.satiety,
              showChevron: true,
              onTap: () => _openEvaluation(meal),
            ),
            const SizedBox(height: AppSpacing.cardGap),
          ],
          AddMealCard(label: '＋ 저녁 식사 기록하기', onTap: _openMealInput),
        ];
    }
  }

  static String _formatDayHeader(DateTime day) {
    const weekdays = ['월', '화', '수', '목', '금', '토', '일'];
    return '${day.month}월 ${day.day}일 ${weekdays[day.weekday - 1]}요일';
  }
}

/// 선택한 날의 식사 한 끼. 연동 시 모델로 대체된다.
class _HistoryMeal {
  const _HistoryMeal(
    this.mealTypeLabel,
    this.time,
    this.foodNames,
    this.quantityLabel,
    this.quality,
    this.satiety,
  );

  final String mealTypeLabel;
  final String time;
  final String foodNames;
  final String quantityLabel;
  final int quality;
  final int satiety;
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
        Text(
          '${month.year}년 ${month.month}월',
          style: AppTypography.sectionHead,
        ),
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
    this.showDots = true,
  });

  final DateTime month;
  final DateTime today;
  final DateTime selectedDay;
  final ValueChanged<DateTime> onSelect;

  /// 달력 집계를 아직 못 받았으면 false. 그리드는 그대로 두고 점만 비운다.
  final bool showDots;

  static const int _rows = 6;
  static const int _columns = 7;

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
    final isSelected = date == selectedDay;

    return _DayCell(
      day: dayNumber,
      isFuture: isFuture,
      isSelected: isSelected,
      // 점 3개 = 아침 · 점심 · 저녁 기록 여부.
      // 연동하면 GET /meals/calendar 의 날짜별 mealType 목록으로 바꾼다.
      // 간식(SNACK)은 Figma 에 자리가 없어 점으로 표시하지 않는다.
      recordedMeals: (isFuture || !showDots)
          ? const [false, false, false]
          : [true, true, dayNumber.isEven],
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
  const _MonthSummaryBar({required this.month});

  final DateTime month;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.cardPadding,
        vertical: AppSpacing.md,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.lgRadius,
      ),
      child: Row(
        children: [
          Text('${month.month}월 42끼 기록', style: AppTypography.cardTitle),
          const Spacer(),
          Text('평균 양 74 · 질 81 · 포만감 65', style: AppTypography.caption),
        ],
      ),
    );
  }
}
