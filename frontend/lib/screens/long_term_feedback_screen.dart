import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

// ── 데이터 모델 ────────────────────────────────────────────

class DailyScore {
  final String date;
  final int
  quantity; // 식사량 막대. TODO: 0~100 원본값. README상 1~5로 변환 필요할 수 있음, 팀 확인 필요.
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

  factory DashboardData.fromJson(Map<String, dynamic> json) {
    return DashboardData(
      series: (json['series'] as List<dynamic>)
          .map((e) => DailyScore.fromJson(e as Map<String, dynamic>))
          .toList(),
      averages: Map<String, int>.from(json['averages'] as Map),
      byMealType: (json['byMealType'] as Map).map(
        (k, v) => MapEntry(k as String, Map<String, int>.from(v as Map)),
      ),
    );
  }
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
  final bool dataSufficient;
  final String? trendSummary;
  final String? recommendation;
  final bool stale;

  LongTermInsight({
    required this.dataSufficient,
    required this.stale,
    this.trendSummary,
    this.recommendation,
  });

  factory LongTermInsight.fromJson(Map<String, dynamic> json) {
    return LongTermInsight(
      dataSufficient: json['dataSufficient'] as bool,
      trendSummary: json['trendSummary'] as String?,
      recommendation: json['recommendation'] as String?,
      stale: json['stale'] as bool? ?? false,
    );
  }
}

// ── API 서비스 ────────────────────────────────────────────

class LongTermApiService {
  // TODO: 직접 작성한 더미 데이터 15개. 실제 API 붙으면 이 리스트 전체를 지우고
  // 서버가 주는 값을 그대로 씀.
  final List<Map<String, dynamic>> _dummySeries = [
    {'date': '2026-07-25', 'quantity': 60, 'quality': 70, 'satiety': 58},
    {'date': '2026-07-26', 'quantity': 68, 'quality': 74, 'satiety': 66},
    {'date': '2026-07-27', 'quantity': 72, 'quality': 78, 'satiety': 60},
    {'date': '2026-07-28', 'quantity': 75, 'quality': 80, 'satiety': 64},
    {'date': '2026-07-29', 'quantity': 75, 'quality': 83, 'satiety': 68},
    {'date': '2026-07-30', 'quantity': 60, 'quality': 70, 'satiety': 58},
    {'date': '2026-07-31', 'quantity': 68, 'quality': 74, 'satiety': 66},
    {'date': '2026-08-01', 'quantity': 72, 'quality': 78, 'satiety': 60},
    {'date': '2026-08-02', 'quantity': 75, 'quality': 80, 'satiety': 64},
    {'date': '2026-08-03', 'quantity': 75, 'quality': 83, 'satiety': 68},
    {'date': '2026-08-04', 'quantity': 60, 'quality': 70, 'satiety': 58},
    {'date': '2026-08-05', 'quantity': 68, 'quality': 74, 'satiety': 66},
    {'date': '2026-08-06', 'quantity': 72, 'quality': 78, 'satiety': 60},
    {'date': '2026-08-07', 'quantity': 75, 'quality': 80, 'satiety': 64},
    {'date': '2026-08-08', 'quantity': 75, 'quality': 83, 'satiety': 68},
  ];

  /// GET /dashboard?period=7d|28d|all
  Future<DashboardData> fetchDashboard(String period) async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    // 실제 API는 period에 맞게 서버가 알아서 잘라서 줌 — 프론트는 그냥 쿼리만 붙이면 됨.
    await Future.delayed(const Duration(milliseconds: 300));

    final all = _dummySeries;
    final sliced = switch (period) {
      '7d' => all.length > 7 ? all.sublist(all.length - 7) : all,
      '28d' =>
        all.length > 28
            ? all.sublist(all.length - 28)
            : all, // 15개뿐이라 실제로는 항상 전체 반환됨
      _ => all, // all
    };

    final quantities = sliced.map((e) => e['quantity'] as int).toList();
    final qualities = sliced.map((e) => e['quality'] as int).toList();
    final satieties = sliced.map((e) => e['satiety'] as int).toList();

    return DashboardData.fromJson({
      'series': sliced,
      'averages': {
        'quantity': (quantities.reduce((a, b) => a + b) / quantities.length)
            .round(),
        'quality': (qualities.reduce((a, b) => a + b) / qualities.length)
            .round(),
        'satiety': (satieties.reduce((a, b) => a + b) / satieties.length)
            .round(),
      },
      'byMealType': {
        'BREAKFAST': {'quantity': 70, 'quality': 88, 'satiety': 78},
        'LUNCH': {'quantity': 76, 'quality': 80, 'satiety': 66},
        'DINNER': {'quantity': 78, 'quality': 74, 'satiety': 58},
      },
    });
  }

  /// GET /medications/dose-events
  Future<List<DoseEvent>> fetchDoseEvents() async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    await Future.delayed(const Duration(milliseconds: 200));
    return [
      {
        'doseEventId': 'de_001',
        'doseMg': 0.25,
        'direction': 'MAINTAIN',
        'effectiveFrom': '2026-06-14',
      },
      {
        'doseEventId': 'de_002',
        'doseMg': 0.5,
        'direction': 'INCREASE',
        'effectiveFrom': '2026-08-01',
      },
    ].map((e) => DoseEvent.fromJson(e)).toList();
  }

  /// GET /insights/long-term?period=7d|28d|all
  Future<LongTermInsight> fetchInsight(String period) async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    await Future.delayed(const Duration(milliseconds: 300));
    return LongTermInsight.fromJson({
      'dataSufficient': true,
      // TODO: 이 문장은 서버(AI)가 그때그때 생성하는 값. 실제로는 fetchInsight가
      // 반환하는 문자열을 그대로 화면에 표시해야 함 — 아래 값은 구조 확인용 예시일 뿐.
      'trendSummary': null,
      'recommendation': null,
      'stale': false,
    });
  }
}

// ── 화면 ────────────────────────────────────────────────────

/// 8. 장기 피드백 화면
class LongTermFeedbackScreen extends StatefulWidget {
  const LongTermFeedbackScreen({super.key});

  @override
  State<LongTermFeedbackScreen> createState() => _LongTermFeedbackScreenState();
}

class _LongTermFeedbackScreenState extends State<LongTermFeedbackScreen> {
  final LongTermApiService _api = LongTermApiService();

  String period = '7d';
  DashboardData? dashboard;
  List<DoseEvent> doseEvents = [];
  LongTermInsight? insight;
  bool isLoading = true;
  String? errorMessage;

  @override
  void initState() {
    super.initState();
    _load();
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
        _api.fetchInsight(period),
      ]);
      setState(() {
        dashboard = results[0] as DashboardData;
        doseEvents = results[1] as List<DoseEvent>;
        insight = results[2] as LongTermInsight;
      });
    } catch (e) {
      setState(() => errorMessage = '불러오는 데 실패했어요: $e');
    } finally {
      setState(() => isLoading = false);
    }
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
        SizedBox(height: AppSpacing.md),
        _buildPeriodTabs(),
        Expanded(
          child: ListView(
            padding: EdgeInsets.symmetric(
              horizontal: AppSpacing.screenHorizontal,
              vertical: AppSpacing.lg,
            ),
            children: [
              _buildTrendChartCard(),
              SizedBox(height: AppSpacing.cardGap),
              _buildQqsTrendCard(),
              SizedBox(height: AppSpacing.cardGap),
              if (insight != null) _buildInsightBanner(insight!),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildHeader() {
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.screenHorizontal),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text('장기 피드백', style: AppTypography.screenTitle),
          // TODO: 다운로드 아이콘 동작이 명세서에 없음. PDF 내보내기인지,
          // 새로고침(POST /insights/long-term/refresh)인지 팀 확인 필요.
          Container(
            width: 36,
            height: 36,
            decoration: BoxDecoration(
              color: AppColors.surfaceMuted,
              shape: BoxShape.circle,
            ),
            child: const Icon(
              Icons.arrow_downward,
              size: 18,
              color: AppColors.textSecondary,
            ),
          ),
        ],
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

  Widget _buildTrendChartCard() {
    final series = dashboard!.series;
    final maxVal = series
        .expand((s) => [s.quantity, s.satiety])
        .reduce((a, b) => a > b ? a : b)
        .toDouble();

    return Container(
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('식사량 · 포만감 · 투약 용량', style: AppTypography.sectionHead),
          SizedBox(height: AppSpacing.sm),
          _buildLegend(),
          SizedBox(height: AppSpacing.lg),
          if (doseEvents.any((e) => e.direction == 'INCREASE'))
            _buildDoseAnnotation(
              doseEvents.firstWhere((e) => e.direction == 'INCREASE'),
            ),
          SizedBox(height: AppSpacing.sm),
          SizedBox(
            height: 160,
            child: CustomPaint(
              painter: _TrendChartPainter(
                series: series,
                maxVal: maxVal,
                barColor: AppColors.surfaceSage,
                lineColor: AppColors.primary,
              ),
              child: Container(),
            ),
          ),
          SizedBox(height: AppSpacing.sm),
          _buildDateLabels(series),
        ],
      ),
    );
  }

  /// 데이터가 많아도 라벨은 최대 5개까지만 균등 간격으로 골라 보여줌 (넘침 방지)
  Widget _buildDateLabels(List<DailyScore> series) {
    const maxLabels = 5;
    List<DailyScore> picked;
    if (series.length <= maxLabels) {
      picked = series;
    } else {
      picked = [];
      for (int i = 0; i < maxLabels; i++) {
        final index = (i * (series.length - 1) / (maxLabels - 1)).round();
        picked.add(series[index]);
      }
    }
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: picked
          .map((s) => Text(_shortDate(s.date), style: AppTypography.caption))
          .toList(),
    );
  }

  Widget _buildLegend() {
    return Row(
      children: [
        _legendDot(AppColors.surfaceSage, '식사량'),
        SizedBox(width: AppSpacing.md),
        _legendDot(AppColors.primary, '포만감'),
        SizedBox(width: AppSpacing.md),
        _legendDot(AppColors.textTertiary, '투약 용량'),
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
        SizedBox(width: 4),
        Text(label, style: AppTypography.caption),
      ],
    );
  }

  Widget _buildDoseAnnotation(DoseEvent event) {
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 4),
        decoration: BoxDecoration(
          color: AppColors.primaryTint,
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(
          '${event.doseMg}mg 증량',
          style: AppTypography.caption.copyWith(color: AppColors.primaryStrong),
        ),
      ),
    );
  }

  String _shortDate(String isoDate) {
    final parts = isoDate.split('-');
    return '${parts[1]}/${parts[2]}';
  }

  Widget _buildQqsTrendCard() {
    final avg = dashboard!.averages;
    final series = dashboard!.series;

    return Container(
      padding: EdgeInsets.all(AppSpacing.cardPadding),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('Q·Q·S 추이', style: AppTypography.sectionHead),
              Text(
                '28일 평균 대비',
                style: AppTypography.caption.copyWith(
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
          SizedBox(height: AppSpacing.lg),
          _buildQqsRow(
            '양',
            AppColors.quantity,
            series.map((s) => s.quantity).toList(),
            avg['quantity']!,
          ),
          SizedBox(height: AppSpacing.md),
          _buildQqsRow(
            '질',
            AppColors.quality,
            series.map((s) => s.quality).toList(),
            avg['quality']!,
          ),
          SizedBox(height: AppSpacing.md),
          _buildQqsRow(
            '포만감',
            AppColors.satiety,
            series.map((s) => s.satiety).toList(),
            avg['satiety']!,
          ),
        ],
      ),
    );
  }

  Widget _buildQqsRow(
    String label,
    Color color,
    List<int> values,
    int average,
  ) {
    final latest = values.last;
    final diff = latest - average;
    final diffText = diff >= 0 ? '+$diff' : '$diff';

    return Row(
      children: [
        SizedBox(width: 56, child: Text(label, style: AppTypography.cardTitle)),
        Expanded(
          child: SizedBox(
            height: 24,
            child: CustomPaint(
              painter: _SparklinePainter(values: values, color: color),
            ),
          ),
        ),
        SizedBox(width: AppSpacing.md),
        Text('$latest', style: AppTypography.cardTitle),
        SizedBox(width: 4),
        Text(
          diffText,
          style: AppTypography.caption.copyWith(
            color: diff >= 0 ? AppColors.primary : AppColors.textSecondary,
          ),
        ),
      ],
    );
  }

  Widget _buildInsightBanner(LongTermInsight insight) {
    if (!insight.dataSufficient) {
      // TODO: 데이터 부족 시 문구도 명세서에 없음. 팀 확인 필요.
      return Container(
        padding: EdgeInsets.all(AppSpacing.cardPadding),
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text('아직 인사이트를 만들기엔 데이터가 부족해요.', style: AppTypography.body),
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
          if (insight.trendSummary != null)
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

// ── 커스텀 차트 페인터 ────────────────────────────────────
// TODO: fl_chart 같은 차트 패키지가 pubspec.yaml에 없어서 직접 그림.
// 팀이 차트 패키지 쓰기로 하면 이 부분 통째로 교체 권장.

class _TrendChartPainter extends CustomPainter {
  final List<DailyScore> series;
  final double maxVal;
  final Color barColor;
  final Color lineColor;

  _TrendChartPainter({
    required this.series,
    required this.maxVal,
    required this.barColor,
    required this.lineColor,
  });

  @override
  void paint(Canvas canvas, Size size) {
    if (series.isEmpty) return;
    final n = series.length;
    final slotWidth = size.width / n;
    final barWidth = (slotWidth * 0.5).clamp(2.0, 24.0);

    final barPaint = Paint()..color = barColor;
    for (int i = 0; i < n; i++) {
      final x = slotWidth * i + slotWidth / 2 - barWidth / 2;
      final barHeight = (series[i].quantity / maxVal) * size.height;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(x, size.height - barHeight, barWidth, barHeight),
          const Radius.circular(4),
        ),
        barPaint,
      );
    }

    final linePaint = Paint()
      ..color = lineColor
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    final path = Path();
    for (int i = 0; i < n; i++) {
      final x = slotWidth * i + slotWidth / 2;
      final y = size.height - (series[i].satiety / maxVal) * size.height;
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(path, linePaint);
  }

  @override
  bool shouldRepaint(covariant _TrendChartPainter oldDelegate) => true;
}

class _SparklinePainter extends CustomPainter {
  final List<int> values;
  final Color color;

  _SparklinePainter({required this.values, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final minVal = values.reduce((a, b) => a < b ? a : b).toDouble();
    final maxVal = values.reduce((a, b) => a > b ? a : b).toDouble();
    final range = (maxVal - minVal).abs() < 1 ? 1.0 : (maxVal - minVal);

    final paint = Paint()
      ..color = color
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round;

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
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(covariant _SparklinePainter oldDelegate) => true;
}
