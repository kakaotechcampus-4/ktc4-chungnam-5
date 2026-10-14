import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../common/polling.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart' show EmptyBlock, WaitNotice;

class DailyScore {
  final String date;
  final int quantity;
  final int quality;
  final int satiety;

  DailyScore({
    required this.date,
    required this.quantity,
    required this.quality,
    required this.satiety,
  });

  factory DailyScore.fromJson(Map<String, dynamic> json) {
    return DailyScore(
      date: json['date'] as String,
      quantity: json['quantity'] as int,
      quality: json['quality'] as int,
      satiety: json['satiety'] as int,
    );
  }
}

class DashboardData {
  final List<DailyScore> series;
  final Map<String, int> averages;
  final Map<String, Map<String, int>> byMealType;

  DashboardData({
    required this.series,
    required this.averages,
    required this.byMealType,
  });

  /// 서버는 평가된 식사가 없는 자리를 null 로 준다(`GET /dashboard`).
  /// 점수가 빠진 날은 그래프에 그릴 수 없어 [series] 에서 뺀다. 평균도 null
  /// 항목은 뺀다 — 0 으로 채우면 "0점"으로 읽힌다.
  factory DashboardData.fromJson(Map<String, dynamic> json) {
    return DashboardData(
      series: [
        for (final e in json['series'] as List<dynamic>)
          if (_hasAllScores(e as Map<String, dynamic>)) DailyScore.fromJson(e),
      ],
      averages: _nonNullScores(json['averages'] as Map),
      byMealType: (json['byMealType'] as Map).map(
        (k, v) => MapEntry(k as String, _nonNullScores(v as Map)),
      ),
    );
  }

  static bool _hasAllScores(Map<String, dynamic> json) =>
      json['quantity'] != null &&
      json['quality'] != null &&
      json['satiety'] != null;

  static Map<String, int> _nonNullScores(Map<dynamic, dynamic> json) => {
    for (final MapEntry(:key, :value) in json.entries)
      if (value is int) key as String: value,
  };
}

class DoseEvent {
  final double doseMg;
  final String direction;
  final String effectiveFrom;

  DoseEvent({
    required this.doseMg,
    required this.direction,
    required this.effectiveFrom,
  });

  factory DoseEvent.fromJson(Map<String, dynamic> json) {
    return DoseEvent(
      doseMg: (json['doseMg'] as num).toDouble(),
      direction: json['direction'] as String,
      effectiveFrom: json['effectiveFrom'] as String,
    );
  }
}

class LongTermInsight {
  /// `PENDING` · `GENERATING` · `READY` · `FAILED`.
  final String status;
  final bool dataSufficient;
  final String? trendSummary;
  final String? recommendation;

  /// 만든 뒤 식사가 바뀌어 다시 만들어야 하는지.
  final bool stale;

  LongTermInsight({
    required this.status,
    required this.dataSufficient,
    required this.stale,
    this.trendSummary,
    this.recommendation,
  });

  /// 새로 만들어 달라고 할지. 데이터가 모자라면 만들어도 소용없다.
  bool get needsRefresh => dataSufficient && (status == 'PENDING' || stale);

  bool get isGenerating => status == 'PENDING' || status == 'GENERATING';

  factory LongTermInsight.fromJson(Map<String, dynamic> json) {
    return LongTermInsight(
      // 명세(10/1)는 feedbackStatus 로 바꾸기로 했고 코드는 아직 status 다.
      // 어느 쪽이 와도 읽는다.
      status: (json['feedbackStatus'] ?? json['status']) as String,
      dataSufficient: json['dataSufficient'] as bool,
      trendSummary: json['trendSummary'] as String?,
      recommendation: json['recommendation'] as String?,
      stale: json['stale'] as bool? ?? false,
    );
  }
}

/// 장기 피드백 화면이 쓰는 엔드포인트.
class LongTermApiService {
  LongTermApiService(this._client);

  final ApiClient _client;

  /// `GET /dashboard?period=7d|28d|all`
  Future<DashboardData> fetchDashboard(String period) async {
    final result = await _client.get('/dashboard', query: {'period': period});
    return DashboardData.fromJson(result.dataMap);
  }

  /// `GET /medications/dose-events` — 오래된 순. 투약 전이면 빈 목록.
  Future<List<DoseEvent>> fetchDoseEvents() async {
    final result = await _client.get('/medications/dose-events');
    return (result.dataMap['events'] as List<dynamic>)
        .map((e) => DoseEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// `GET /insights/long-term?period=`
  Future<LongTermInsight> fetchInsight(String period) async {
    final result = await _client.get(
      '/insights/long-term',
      query: {'period': period},
    );
    return LongTermInsight.fromJson(result.dataMap);
  }

  /// `POST /insights/long-term/refresh` — 새로 만들어 달라고 한다(202).
  /// 다음에 다시 물어볼 간격을 돌려준다.
  Future<Duration> refreshInsight(String period) async {
    final result = await _client.post(
      '/insights/long-term/refresh',
      body: {'period': period},
    );
    return Duration(
      milliseconds: result.dataMap['pollIntervalMs'] as int? ?? 1500,
    );
  }
}

class LongTermFeedbackScreen extends StatefulWidget {
  const LongTermFeedbackScreen({super.key});

  @override
  State<LongTermFeedbackScreen> createState() => _LongTermFeedbackScreenState();
}

class _LongTermFeedbackScreenState extends State<LongTermFeedbackScreen>
    with SingleTickerProviderStateMixin {
  late final LongTermApiService _api = LongTermApiService(
    context.read<ApiClient>(),
  );

  /// 차트가 나타나는 애니메이션(0~1). 데이터가 들어올 때마다 처음부터 다시 돈다.
  /// 막대가 먼저 솟고, 그 뒤에 꺾은선이 오른쪽으로 그려진다.
  late final AnimationController _chartController = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1800),
  );

  Animation<double> get _chartAnimation => _chartController;

  String period = '7d';
  DashboardData? dashboard;
  List<DoseEvent> doseEvents = [];
  LongTermInsight? insight;

  /// 인사이트를 받지 못했는지(요청 실패), 기다리다 한도를 넘겼는지.
  bool insightFailed = false;
  bool insightTimedOut = false;
  bool isLoading = true;
  String? errorMessage;

  /// 식사를 기록·삭제하면 그래프가 바뀐다. 탭이 다시 보일 때 새로 불러온다.
  late final TabState _tabs = context.read<TabState>();

  void _onTabChanged() {
    if (_tabs.currentIndex == TabState.feedback &&
        _tabs.feedbackKind == FeedbackKind.long) {
      _load();
    }
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
    _chartController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      isLoading = true;
      errorMessage = null;
    });
    try {
      final results = await Future.wait([
        _api.fetchDashboard(period),
        _api.fetchDoseEvents(),
      ]);
      setState(() {
        dashboard = results[0] as DashboardData;
        doseEvents = results[1] as List<DoseEvent>;
        insight = null;
      });
      // 인사이트 문장은 AI 가 만들어 늦다. 그래프를 먼저 보이고 따로 채운다.
      _loadInsight(period);
    } catch (e) {
      setState(() => errorMessage = '불러오는 데 실패했어요: $e');
    } finally {
      setState(() => isLoading = false);
      if (dashboard != null) _chartController.forward(from: 0);
    }
  }

  /// 인사이트를 받고, 없거나 낡았으면 새로 만들어 달라고 한 뒤 준비될
  /// 때까지 간격을 늘려 가며 다시 묻는다(약 1분). [refresh] 면(실패 뒤
  /// "다시 시도") 상태와 상관없이 새로 만들어 달라고 한다. 실패해도 그래프는 그대로 둔다.
  Future<void> _loadInsight(String forPeriod, {bool refresh = false}) async {
    bool isCurrent() => mounted && forPeriod == period;
    setState(() {
      insightFailed = false;
      insightTimedOut = false;
    });
    final Duration interval;
    try {
      var result = await _api.fetchInsight(forPeriod);
      if (!isCurrent()) return;
      setState(() => insight = result);
      // 데이터가 모자라면 서버가 PENDING 으로 줘도 기다려 봐야 만들어지지 않는다.
      if (!result.dataSufficient) return;
      refresh = refresh || result.needsRefresh;
      if (!refresh && !result.isGenerating) return;
      interval = refresh
          ? await _api.refreshInsight(forPeriod)
          : const Duration(milliseconds: 1500);
      if (!isCurrent()) return;
      if (refresh) {
        // 새로 만드는 중으로 보이게 한다(실패 문구가 남지 않게).
        result = await _api.fetchInsight(forPeriod);
        if (!isCurrent()) return;
        setState(() => insight = result);
      }
    } catch (_) {
      if (isCurrent()) setState(() => insightFailed = true);
      return;
    }
    final result = await pollUntil<LongTermInsight>(
      fetch: () => _api.fetchInsight(forPeriod),
      isDone: (r) => !r.dataSufficient || !r.isGenerating,
      isActive: isCurrent,
      onResult: (r) => setState(() => insight = r),
      first: interval,
    );
    if (isCurrent() && result == null) setState(() => insightTimedOut = true);
  }

  void _onPeriodTap(String newPeriod) {
    if (newPeriod == period) return;
    setState(() => period = newPeriod);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: isLoading
            ? const Center(child: CircularProgressIndicator())
            : errorMessage != null && dashboard == null
            ? Center(child: Text(errorMessage!, style: AppTypography.body))
            : _buildContent(),
      ),
    );
  }

  Widget _buildContent() {
    return Column(
      children: [
        _buildHeader(),
        const SizedBox(height: AppSpacing.lg),
        _buildPeriodTabs(),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.symmetric(
              horizontal: AppSpacing.screenHorizontal,
              vertical: AppSpacing.lg,
            ),
            children: [
              // 평가된 식사가 없는 기간이면 그래프를 그릴 값이 없다.
              if (dashboard!.series.isEmpty)
                const EmptyBlock(message: '이 기간에 평가된 식사가 없어요')
              else ...[
                _buildTrendChartCard(),
                const SizedBox(height: AppSpacing.cardGap),
                _buildQqsTrendCard(),
              ],
              const SizedBox(height: AppSpacing.cardGap),
              if (insight != null)
                _buildInsightBanner(insight!, dashboard?.series.length ?? 0)
              else if (insightFailed)
                WaitNotice(
                  message: '인사이트를 불러오지 못했어요.',
                  actionLabel: '다시 시도',
                  onAction: () => _loadInsight(period),
                ),
            ],
          ),
        ),
      ],
    );
  }

  /// 제목 크기와 위쪽 여백은 단기 피드백과 같다.
  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal,
        0,
      ),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Text(
          '장기 피드백',
          style: AppTypography.screenTitle.copyWith(fontSize: 17),
        ),
      ),
    );
  }

  Widget _buildPeriodTabs() {
    final options = {'7d': '7일', '28d': '28일', 'all': '전체'};
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.screenHorizontal),
      child: Container(
        padding: const EdgeInsets.all(4),
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: options.entries.map((entry) {
            final selected = period == entry.key;
            return Expanded(
              child: GestureDetector(
                onTap: () => _onPeriodTap(entry.key),
                child: Container(
                  padding: EdgeInsets.symmetric(vertical: AppSpacing.sm),
                  decoration: BoxDecoration(
                    color: selected ? AppColors.surface : Colors.transparent,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    entry.value,
                    style: AppTypography.body.copyWith(
                      fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
                    ),
                  ),
                ),
              ),
            );
          }).toList(),
        ),
      ),
    );
  }

  // ── 식사량 · 포만감 · 투약 용량 ──────────────────────────────

  /// 날짜 문자열(`2026-10-07`)을 그날 0시로 바꾼다.
  static DateTime _day(String iso) {
    final p = iso.split('-');
    return DateTime(int.parse(p[0]), int.parse(p[1]), int.parse(p[2]));
  }

  /// 막대 칸 수.
  /// - 7일: 7칸.
  /// - 28일: 보이는 날이 7일 이하면 7칸(7일 화면과 같다), 그 이상은 하루가 한 칸으로
  ///   늘다가 9칸이 되면 더 늘지 않고 날들을 묶는다.
  /// - 전체: 10칸.
  int _slotCountFor(int span) => switch (period) {
    '7d' => 7,
    '28d' => span <= 7 ? 7 : (span < 9 ? span : 9),
    _ => 10,
  };

  /// 막대 칸은 **첫 기록일부터** 날짜 순서대로 놓인다.
  /// 첫째 날 기록 → 첫째 칸, 둘째 날 → 둘째 칸, 셋째 날 기록 없음 → 셋째 칸은 빈 칸,
  /// 넷째 날 기록 → 넷째 칸. 기록이 적어도 칸 간격은 그대로라 막대는 왼쪽에 모이고
  /// 오른쪽은 비어 있다.
  ///
  /// 7일은 하루가 한 칸이다. 28일(최대 9칸)·전체(10칸)는 칸보다 날이 많으면 날들을
  /// 순서대로 똑같이 묶고 칸마다 평균을 낸다. 날이 칸보다 적으면 하루가 한 칸이다.
  _ChartData _chartData() {
    final series = dashboard!.series;
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final first = _day(series.first.date);
    final last = _day(series.last.date);

    // 첫 기록이 기간 안이면 거기서 시작하고, 기간보다 오래전이면 기간의 첫날부터 본다.
    DateTime laterOf(DateTime a, DateTime b) => a.isAfter(b) ? a : b;
    final DateTime start;
    final int span;
    switch (period) {
      case '7d':
        span = 7;
        start = laterOf(first, today.subtract(const Duration(days: 6)));
      case '28d':
        // 첫 기록(또는 28일 전)부터 오늘까지. 처음 쓰는 사람은 날이 갈수록 늘어난다.
        start = laterOf(first, today.subtract(const Duration(days: 27)));
        span = today.difference(start).inDays + 1;
      default:
        start = first;
        span = last.difference(first).inDays + 1;
    }

    final slots = _slotCountFor(span);
    final perDay = span <= slots;
    int slotOf(int dayIndex) => perDay ? dayIndex : dayIndex * slots ~/ span;

    final quantity = List<List<int>>.generate(slots, (_) => []);
    final satiety = List<List<int>>.generate(slots, (_) => []);
    for (final s in series) {
      final dayIndex = _day(s.date).difference(start).inDays;
      if (dayIndex < 0 || dayIndex >= span) continue;
      final b = slotOf(dayIndex);
      quantity[b].add(s.quantity);
      satiety[b].add(s.satiety);
    }

    // 칸마다 들어 있는 첫 날·마지막 날. 기간 밖(아직 오지 않은 날)의 칸은 null.
    final firstDay = List<int?>.filled(slots, null);
    final lastDay = List<int?>.filled(slots, null);
    for (var d = 0; d < span; d++) {
      final b = slotOf(d);
      if (b >= slots) break;
      firstDay[b] ??= d;
      lastDay[b] = d;
    }

    double? mean(List<int> v) =>
        v.isEmpty ? null : v.reduce((a, b) => a + b) / v.length;

    final buckets = [
      for (var b = 0; b < slots; b++)
        _Bucket(
          start: firstDay[b] == null
              ? null
              : start.add(Duration(days: firstDay[b]!)),
          end: lastDay[b] == null ? null : start.add(Duration(days: lastDay[b]!)),
          quantity: mean(quantity[b]),
          satiety: mean(satiety[b]),
        ),
    ];

    return _ChartData(
      buckets: buckets,
      markers: _doseMarkers(start, span, slotOf, slots),
    );
  }

  /// 증량한 날에만 표시한다. 투약 용량 꺾은선은 그리지 않는다.
  /// 보이는 기간 밖에서 일어난 증량은 그리지 않는다.
  List<_DoseMarker> _doseMarkers(
    DateTime start,
    int span,
    int Function(int) slotOf,
    int slots,
  ) {
    final sorted = [...doseEvents]
      ..sort((a, b) => a.effectiveFrom.compareTo(b.effectiveFrom));
    final markers = <_DoseMarker>[];
    for (var i = 0; i < sorted.length; i++) {
      final event = sorted[i];
      if (event.direction != 'INCREASE') continue;
      final dayIndex = _day(event.effectiveFrom).difference(start).inDays;
      if (dayIndex < 0 || dayIndex >= span) continue;
      final slot = slotOf(dayIndex);
      if (slot >= slots) continue;
      // 맨 처음 기록이 증량이면 이전 용량을 몰라 얼마나 늘었는지 알 수 없다.
      final delta = i > 0 ? (event.doseMg - sorted[i - 1].doseMg).abs() : 0.0;
      // 앞 증량과 글이 겹칠 만큼 가까우면 아랫줄로 내린다.
      final minGap = slots <= 7 ? 2 : 3;
      final close = markers.isNotEmpty && slot - markers.last.slot < minGap;
      markers.add(
        _DoseMarker(
          slot: slot,
          label: delta > 0 ? '${_formatMg(delta)}mg 증량' : '증량',
          lane: close && markers.last.lane == 0 ? 1 : 0,
        ),
      );
    }
    return markers;
  }

  String _formatMg(double mg) {
    final rounded = double.parse(mg.toStringAsFixed(2));
    return rounded == rounded.roundToDouble()
        ? rounded.toStringAsFixed(0)
        : rounded.toString();
  }

  Widget _buildTrendChartCard() {
    final data = _chartData();
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: AppRadius.xlRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '식사량 · 포만감 · 투약 용량',
            style: AppTypography.cardTitle.copyWith(fontSize: 12.5),
          ),
          const SizedBox(height: AppSpacing.sm),
          _buildLegend(),
          const SizedBox(height: AppSpacing.md),
          _TrendChart(data: data, animation: _chartAnimation),
          const SizedBox(height: AppSpacing.sm),
          _DateLabels(buckets: data.buckets),
        ],
      ),
    );
  }

  Widget _buildLegend() {
    return Row(
      children: [
        _legendDot(AppColors.surfaceSage, '식사량'),
        const SizedBox(width: AppSpacing.md),
        _legendDot(AppColors.primary, '포만감'),
      ],
    );
  }

  Widget _legendDot(Color color, String label) {
    return Row(
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 4),
        Text(
          label,
          style: AppTypography.caption.copyWith(
            fontSize: 9.5,
            color: AppColors.textSecondary,
          ),
        ),
      ],
    );
  }

  // ── Q · Q · S 추이 ───────────────────────────────────────────

  Widget _buildQqsTrendCard() {
    final avg = dashboard!.averages;
    final series = dashboard!.series;

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: AppRadius.xlRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                'Q · Q · S 추이',
                style: AppTypography.cardTitle.copyWith(fontSize: 12.5),
              ),
              Text(
                '28일 평균 대비',
                style: AppTypography.caption.copyWith(
                  fontSize: 9.5,
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          _QqsRow(
            label: '양',
            color: AppColors.quantity,
            values: series.map((s) => s.quantity).toList(),
            average: avg['quantity']!,
            animation: _chartAnimation,
          ),
          const SizedBox(height: AppSpacing.lg),
          _QqsRow(
            label: '질',
            color: AppColors.quality,
            values: series.map((s) => s.quality).toList(),
            average: avg['quality']!,
            animation: _chartAnimation,
          ),
          const SizedBox(height: AppSpacing.lg),
          _QqsRow(
            label: '포만감',
            color: AppColors.satiety,
            values: series.map((s) => s.satiety).toList(),
            average: avg['satiety']!,
            animation: _chartAnimation,
          ),
        ],
      ),
    );
  }

  /// [scoredDays] 는 이 기간에 양·질·포만감이 다 있는 날 수(그래프 점 수).
  /// 서버도 같은 기준(3일)으로 인사이트를 만든다
  /// (BE `worker/jobs/feedback_long.py` `MIN_SCORED_DAYS`).
  Widget _buildInsightBanner(LongTermInsight insight, int scoredDays) {
    if (!insight.dataSufficient) {
      return Container(
        padding: EdgeInsets.all(AppSpacing.cardPadding),
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(
          '점수가 있는 날이 3일 이상 쌓이면 인사이트를 만들어 드려요. '
          '(지금 $scoredDays일)',
          style: AppTypography.body,
        ),
      );
    }

    return Container(
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.primaryTint,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (insight.trendSummary == null)
            if (insightTimedOut)
              WaitNotice(
                message: '인사이트를 만드는 데 시간이 걸리고 있어요. 나중에 다시 확인해 주세요.',
                actionLabel: '다시 확인하기',
                onAction: () => _loadInsight(period),
              )
            else if (insight.isGenerating && !insightFailed)
              Text('인사이트를 만들고 있어요…', style: AppTypography.body)
            else
              WaitNotice(
                message: '지금은 인사이트를 만들지 못했어요.',
                actionLabel: '다시 시도',
                onAction: () => _loadInsight(period, refresh: true),
              )
          else
            Text(
              insight.trendSummary!,
              style: AppTypography.cardTitle.copyWith(
                color: AppColors.primaryStrong,
              ),
            ),
          if (insight.recommendation != null) ...[
            SizedBox(height: AppSpacing.xs),
            Text(insight.recommendation!, style: AppTypography.body),
          ],
        ],
      ),
    );
  }
}

// ── 차트 데이터 ────────────────────────────────────────────────

/// 막대 한 칸. 기록이 없는 칸은 값이 null 이다.
class _Bucket {
  const _Bucket({
    required this.start,
    required this.end,
    required this.quantity,
    required this.satiety,
  });

  /// 이 칸에 들어 있는 첫 날(날짜 라벨)과 마지막 날. 아직 오지 않은 날의 칸은 null.
  final DateTime? start;
  final DateTime? end;

  /// 칸 안 점수의 평균. 기록 없는 날의 칸은 null(막대·점이 없다).
  final double? quantity;
  final double? satiety;
}

/// 증량한 날이 몇 번째 칸에 있는지와 위에 붙일 글.
/// 가까운 증량끼리 글이 겹치지 않게 [lane] 으로 윗줄(0)·아랫줄(1)을 나눈다.
class _DoseMarker {
  const _DoseMarker({
    required this.slot,
    required this.label,
    required this.lane,
  });

  final int slot;
  final String label;
  final int lane;
}

class _ChartData {
  const _ChartData({required this.buckets, required this.markers});

  final List<_Bucket> buckets;
  final List<_DoseMarker> markers;
}

/// 막대와 꺾은선이 나타나는 순서. 전체 애니메이션을 0~1 로 보고
/// 막대가 먼저 솟고(0~0.45) 꺾은선이 그 뒤에 오른쪽으로 그려진다(0.4~1).
final Animatable<double> _barReveal = CurveTween(
  curve: const Interval(0.0, 0.45, curve: Curves.easeOutCubic),
);
final Animatable<double> _lineReveal = CurveTween(
  curve: const Interval(0.4, 1.0, curve: Curves.easeInOutCubic),
);

/// 막대(식사량) + 꺾은선(포만감) + 증량 점선 차트. 점수는 0~100 이라 높이 기준은 100 이다.
class _TrendChart extends StatelessWidget {
  const _TrendChart({required this.data, required this.animation});

  final _ChartData data;
  final Animation<double> animation;

  static const double _chartHeight = 140;

  /// 왼쪽 세로축 숫자(100 · 50 · 0)가 들어갈 폭. 눈금선·막대는 그 오른쪽에서 시작한다.
  static const double axisWidth = 26;

  /// 증량 표시 글 한 줄의 높이. 증량 표시가 없으면 자리를 잡지 않고,
  /// 가까운 증량이 있어 글이 두 줄로 나뉘면 두 줄 높이를 잡는다.
  static const double _markerLaneHeight = 24;

  @override
  Widget build(BuildContext context) {
    final count = data.buckets.length;
    final lanes = data.markers.isEmpty
        ? 0
        : data.markers.any((m) => m.lane == 1)
        ? 2
        : 1;
    final markerLane = lanes * _markerLaneHeight;
    return SizedBox(
      height: markerLane + _chartHeight,
      child: Stack(
        children: [
          Positioned(
            left: 0,
            right: 0,
            top: markerLane,
            height: _chartHeight,
            child: CustomPaint(
              painter: _TrendChartPainter(
                buckets: data.buckets,
                markers: data.markers,
                markerLaneHeight: _markerLaneHeight,
                twoLanes: lanes == 2,
                animation: animation,
              ),
            ),
          ),
          // 증량 표시 글은 점선 맨 위에 붙인다. 가장자리에서는 안쪽으로 밀린다.
          for (final m in data.markers)
            Positioned(
              left: axisWidth,
              right: 0,
              top: m.lane * _markerLaneHeight,
              height: _markerLaneHeight,
              child: Align(
                alignment: Alignment(
                  count <= 1 ? 0 : (2 * (m.slot + 0.5) / count) - 1,
                  0,
                ),
                child: _MarkerLabel(text: m.label),
              ),
            ),
        ],
      ),
    );
  }
}

/// 점선 위에 붙는 `1.0mg 증량` 글. 노란색 계열이다.
class _MarkerLabel extends StatelessWidget {
  const _MarkerLabel({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: AppColors.quantityBg,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        text,
        style: AppTypography.emphasis.copyWith(
          fontSize: 9.5,
          height: 1.25,
          color: AppColors.quantity,
        ),
      ),
    );
  }
}

class _TrendChartPainter extends CustomPainter {
  _TrendChartPainter({
    required this.buckets,
    required this.markers,
    required this.markerLaneHeight,
    required this.twoLanes,
    required this.animation,
  }) : super(repaint: animation);

  final List<_Bucket> buckets;
  final List<_DoseMarker> markers;
  final double markerLaneHeight;

  /// 증량 글이 두 줄이면, 윗줄 글에서 내려오는 점선이 아랫줄 높이만큼 더 길어야 이어진다.
  final bool twoLanes;
  final Animation<double> animation;

  @override
  void paint(Canvas canvas, Size size) {
    final n = buckets.length;
    if (n == 0) return;
    final barReveal = _barReveal.transform(animation.value);
    final lineReveal = _lineReveal.transform(animation.value);
    // 왼쪽 세로축 숫자 자리를 빼고 그린다. 점수는 0~100 이다.
    const axis = _TrendChart.axisWidth;
    final plotWidth = size.width - axis;
    final slotWidth = plotWidth / n;

    // 가로 눈금선과 세로축 숫자(100 · 50 · 0).
    final grid = Paint()
      ..color = AppColors.border
      ..strokeWidth = 1;
    for (final (value, label) in const [(1.0, '100'), (0.5, '50'), (0.0, '0')]) {
      final y = (size.height * (1 - value)).clamp(0.5, size.height - 0.5);
      canvas.drawLine(Offset(axis, y), Offset(size.width, y), grid);
      final text = TextPainter(
        text: TextSpan(
          text: label,
          style: AppTypography.caption.copyWith(
            fontSize: 9,
            color: AppColors.textTertiary,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      text.paint(canvas, Offset(axis - 6 - text.width, y - text.height / 2));
    }

    // 증량한 날의 점선(노란색). 글이 두 줄이면 윗줄 글의 점선은 위로 더 길게 이어 붙인다.
    final markerPaint = Paint()
      ..color = AppColors.quantity
      ..strokeWidth = 1.2
      ..style = PaintingStyle.stroke;
    for (final m in markers) {
      final x = axis + slotWidth * (m.slot + 0.5);
      final top = twoLanes && m.lane == 0 ? -markerLaneHeight : 0.0;
      _drawDashedLine(canvas, Offset(x, top), Offset(x, size.height), markerPaint);
    }

    // 막대(식사량): 아래에서 위로 솟는다.
    final barWidth = (slotWidth * 0.58).clamp(4.0, 22.0);
    final barPaint = Paint()..color = AppColors.surfaceSage;
    for (var i = 0; i < n; i++) {
      final q = buckets[i].quantity;
      if (q == null) continue;
      final height = (q / 100).clamp(0.0, 1.0) * size.height * barReveal;
      if (height <= 0) continue;
      final x = axis + slotWidth * i + slotWidth / 2 - barWidth / 2;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(x, size.height - height, barWidth, height),
          const Radius.circular(4),
        ),
        barPaint,
      );
    }

    // 꺾은선(포만감): 막대가 솟은 뒤 왼쪽에서 오른쪽으로 그려진다.
    final points = <Offset>[
      for (var i = 0; i < n; i++)
        if (buckets[i].satiety != null)
          Offset(
            axis + slotWidth * i + slotWidth / 2,
            size.height - (buckets[i].satiety! / 100).clamp(0.0, 1.0) * size.height,
          ),
    ];
    if (points.isEmpty || lineReveal <= 0) return;

    final linePaint = Paint()
      ..color = AppColors.primary
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final path = Path()..moveTo(points.first.dx, points.first.dy);
    for (final p in points.skip(1)) {
      path.lineTo(p.dx, p.dy);
    }
    final head = _drawPartial(canvas, path, linePaint, lineReveal);
    canvas.drawCircle(
      head ?? points.first,
      3.5,
      Paint()..color = AppColors.primary,
    );
  }

  /// 선을 [fraction] 만큼만 그리고 그 끝 위치를 돌려준다.
  Offset? _drawPartial(Canvas canvas, Path path, Paint paint, double fraction) {
    final metrics = path.computeMetrics().toList();
    if (metrics.isEmpty) return null;
    final metric = metrics.first;
    final end = metric.length * fraction.clamp(0.0, 1.0);
    canvas.drawPath(metric.extractPath(0, end), paint);
    return metric.getTangentForOffset(end)?.position;
  }

  void _drawDashedLine(Canvas canvas, Offset a, Offset b, Paint paint) {
    const dash = 4.0;
    const gap = 3.0;
    final length = (b - a).distance;
    final dir = (b - a) / length;
    var d = 0.0;
    while (d < length) {
      final end = (d + dash).clamp(0.0, length);
      canvas.drawLine(a + dir * d, a + dir * end, paint);
      d = end + gap;
    }
  }

  @override
  bool shouldRepaint(covariant _TrendChartPainter old) =>
      old.buckets != buckets ||
      old.markers != markers ||
      old.twoLanes != twoLanes;
}

/// 차트 아래 날짜 라벨. 칸이 많으면 몇 칸씩 건너뛰어 겹치지 않게 한다.
class _DateLabels extends StatelessWidget {
  const _DateLabels({required this.buckets});

  final List<_Bucket> buckets;

  @override
  Widget build(BuildContext context) {
    final n = buckets.length;
    final step = n <= 7 ? 1 : (n / 5).ceil();
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    // 막대와 같은 칸 폭을 쓰도록 왼쪽 세로축 폭만큼 비운다.
    return Padding(
      padding: const EdgeInsets.only(left: _TrendChart.axisWidth),
      child: Row(
        children: [
          for (var i = 0; i < n; i++)
          Expanded(
            child: Center(
              // 아직 오지 않은 날(오늘 이후)의 칸에는 날짜를 붙이지 않는다.
              child: buckets[i].start != null &&
                      !buckets[i].start!.isAfter(today) &&
                      i % step == 0
                  ? Text(
                      '${buckets[i].start!.month}/${buckets[i].start!.day}',
                      maxLines: 1,
                      softWrap: false,
                      overflow: TextOverflow.visible,
                      style: AppTypography.caption.copyWith(
                        fontSize: 9.5,
                        color: AppColors.textTertiary,
                      ),
                    )
                  : const SizedBox.shrink(),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Q · Q · S 추이 한 줄 ───────────────────────────────────────

/// `양` + 꺾은선 + 최근 값 + 평균 대비 증감 칩.
class _QqsRow extends StatelessWidget {
  const _QqsRow({
    required this.label,
    required this.color,
    required this.values,
    required this.average,
    required this.animation,
  });

  final String label;
  final Color color;
  final List<int> values;
  final int average;
  final Animation<double> animation;

  @override
  Widget build(BuildContext context) {
    final latest = values.last;
    final diff = latest - average;

    return Row(
      children: [
        SizedBox(
          width: 44,
          child: Text(
            label,
            style: AppTypography.emphasis.copyWith(fontSize: 11.5, color: color),
          ),
        ),
        Expanded(
          child: SizedBox(
            height: 24,
            child: CustomPaint(
              painter: _SparklinePainter(
                values: values,
                color: color,
                animation: animation,
              ),
            ),
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Text(
          '$latest',
          style: AppTypography.cardTitle.copyWith(fontSize: 11.5),
        ),
        const SizedBox(width: 6),
        _DeltaChip(diff: diff),
      ],
    );
  }
}

/// 평균 대비 증감. 늘었으면 초록 ▲, 줄었으면 빨강 ▼, 같으면 회색.
class _DeltaChip extends StatelessWidget {
  const _DeltaChip({required this.diff});

  final int diff;

  @override
  Widget build(BuildContext context) {
    final Color background;
    final Color foreground;
    final IconData? arrow;
    if (diff > 0) {
      background = AppColors.qualityBg;
      foreground = AppColors.quality;
      arrow = Icons.arrow_drop_up;
    } else if (diff < 0) {
      background = AppColors.primaryStrongBg;
      foreground = AppColors.primary;
      arrow = Icons.arrow_drop_down;
    } else {
      background = AppColors.surfaceMuted;
      foreground = AppColors.textSecondary;
      arrow = null;
    }
    return Container(
      constraints: const BoxConstraints(minWidth: 30),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(9),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            '${diff.abs()}',
            style: AppTypography.emphasis.copyWith(
              fontSize: 9,
              height: 1.25,
              color: foreground,
            ),
          ),
          if (arrow != null) Icon(arrow, size: 12, color: foreground),
        ],
      ),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  _SparklinePainter({
    required this.values,
    required this.color,
    required this.animation,
  }) : super(repaint: animation);

  final List<int> values;
  final Color color;
  final Animation<double> animation;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final reveal = _lineReveal.transform(animation.value);
    if (reveal <= 0) return;
    final minVal = values.reduce((a, b) => a < b ? a : b).toDouble();
    final maxVal = values.reduce((a, b) => a > b ? a : b).toDouble();
    final range = (maxVal - minVal).abs() < 1 ? 1.0 : (maxVal - minVal);

    final paint = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;

    final path = Path();
    final stepX = size.width / (values.length - 1);
    for (int i = 0; i < values.length; i++) {
      final x = stepX * i;
      final normalized = (values[i] - minVal) / range;
      final y = size.height - (normalized * size.height);
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    final metric = path.computeMetrics().first;
    canvas.drawPath(metric.extractPath(0, metric.length * reveal), paint);
  }

  @override
  bool shouldRepaint(covariant _SparklinePainter old) =>
      old.values != values || old.color != color;
}
