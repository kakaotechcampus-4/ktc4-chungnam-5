import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

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

class LongTermApiService {
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

  Future<DashboardData> fetchDashboard(String period) async {
    await Future.delayed(const Duration(milliseconds: 300));

    final all = _dummySeries;
    final sliced = switch (period) {
      '7d' => all.length > 7 ? all.sublist(all.length - 7) : all,
      '28d' => all.length > 28 ? all.sublist(all.length - 28) : all,
      _ => all,
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

  Future<List<DoseEvent>> fetchDoseEvents() async {
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

  Future<LongTermInsight> fetchInsight(String period) async {
    await Future.delayed(const Duration(milliseconds: 300));
    return LongTermInsight.fromJson({
      'dataSufficient': true,
      'trendSummary': null,
      'recommendation': null,
      'stale': false,
    });
  }
}

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

  List<DailyScore> _sampledSeries() {
    final series = dashboard!.series;
    if (series.isEmpty) return series;
    switch (period) {
      case '7d':
        return series;
      case '28d':
        return _stepSample(series, 3);
      default:
        final targetCount = ((series.length * 0.08).ceil()).clamp(
          2,
          series.length,
        );
        return _evenSample(series, targetCount);
    }
  }

  List<DailyScore> _stepSample(List<DailyScore> series, int step) {
    final result = [for (int i = 0; i < series.length; i += step) series[i]];
    if (result.last != series.last) result.add(series.last);
    return result;
  }

  List<DailyScore> _evenSample(List<DailyScore> series, int count) {
    if (series.length <= count) return series;
    final indices = <int>{0, series.length - 1};
    for (int i = 0; i < count; i++) {
      indices.add((i * (series.length - 1) / (count - 1)).round());
    }
    final sorted = indices.toList()..sort();
    return sorted.map((i) => series[i]).toList();
  }

  List<double> _doseValuesFor(List<DailyScore> series) {
    if (doseEvents.isEmpty) return List.filled(series.length, 0);
    final sortedEvents = [...doseEvents]
      ..sort((a, b) => a.effectiveFrom.compareTo(b.effectiveFrom));
    return series.map((s) {
      var active = sortedEvents.first;
      for (final e in sortedEvents) {
        if (e.effectiveFrom.compareTo(s.date) <= 0) {
          active = e;
        } else {
          break;
        }
      }
      return active.doseMg;
    }).toList();
  }

  Widget _buildTrendChartCard() {
    final series = _sampledSeries();
    final doseValues = _doseValuesFor(series);
    final maxVal = series
        .expand((s) => [s.quantity, s.satiety])
        .reduce((a, b) => a > b ? a : b)
        .toDouble();
    final maxDose = doseValues.isEmpty
        ? 1.0
        : doseValues.reduce((a, b) => a > b ? a : b);

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
          _buildDoseAnnotations(),
          SizedBox(height: AppSpacing.sm),
          SizedBox(
            height: 160,
            child: CustomPaint(
              painter: _TrendChartPainter(
                series: series,
                maxVal: maxVal,
                doseValues: doseValues,
                maxDose: maxDose == 0 ? 1.0 : maxDose,
                barColor: AppColors.surfaceSage,
                lineColor: AppColors.primary,
                doseColor: AppColors.textTertiary,
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

  Widget _buildDateLabels(List<DailyScore> series) {
    return Row(
      children: series
          .map(
            (s) => Expanded(
              child: Center(
                child: Text(_shortDate(s.date), style: AppTypography.caption),
              ),
            ),
          )
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

  Widget _buildDoseAnnotations() {
    final sorted = [...doseEvents]
      ..sort((a, b) => a.effectiveFrom.compareTo(b.effectiveFrom));
    final changes = <(DoseEvent, double)>[];
    for (int i = 0; i < sorted.length; i++) {
      final event = sorted[i];
      if (event.direction == 'MAINTAIN') continue;
      final prevDose = i > 0 ? sorted[i - 1].doseMg : event.doseMg;
      changes.add((event, event.doseMg - prevDose));
    }
    if (changes.isEmpty) return const SizedBox.shrink();
    return Align(
      alignment: Alignment.centerRight,
      child: Wrap(
        spacing: AppSpacing.xs,
        runSpacing: AppSpacing.xs,
        children: changes.map((c) => _buildDoseAnnotation(c.$1, c.$2)).toList(),
      ),
    );
  }

  Widget _buildDoseAnnotation(DoseEvent event, double delta) {
    final isIncrease = event.direction == 'INCREASE';
    return Container(
      padding: EdgeInsets.symmetric(horizontal: AppSpacing.sm, vertical: 4),
      decoration: BoxDecoration(
        color: isIncrease ? AppColors.primaryTint : AppColors.surfaceMuted,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        isIncrease
            ? '${_formatMg(delta.abs())}mg 증량'
            : '${_formatMg(delta.abs())}mg 감소',
        style: AppTypography.caption.copyWith(
          color: isIncrease ? AppColors.primaryStrong : AppColors.textSecondary,
        ),
      ),
    );
  }

  String _formatMg(double mg) {
    final rounded = double.parse(mg.toStringAsFixed(2));
    return rounded == rounded.roundToDouble()
        ? rounded.toStringAsFixed(0)
        : rounded.toString();
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

class _TrendChartPainter extends CustomPainter {
  final List<DailyScore> series;
  final double maxVal;
  final List<double> doseValues;
  final double maxDose;
  final Color barColor;
  final Color lineColor;
  final Color doseColor;

  _TrendChartPainter({
    required this.series,
    required this.maxVal,
    required this.doseValues,
    required this.maxDose,
    required this.barColor,
    required this.lineColor,
    required this.doseColor,
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

    if (doseValues.length == n) {
      final dosePaint = Paint()
        ..color = doseColor
        ..strokeWidth = 2
        ..style = PaintingStyle.stroke;
      final dosePath = Path();
      for (int i = 0; i < n; i++) {
        final x = slotWidth * i + slotWidth / 2;
        final y = size.height - (doseValues[i] / maxDose) * size.height;
        if (i == 0) {
          dosePath.moveTo(x, y);
        } else {
          dosePath.lineTo(x, y);
        }
      }
      _drawDashedPath(canvas, dosePath, dosePaint);
    }
  }

  void _drawDashedPath(
    Canvas canvas,
    Path path,
    Paint paint, {
    double dashWidth = 4,
    double dashGap = 3,
  }) {
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final end = (distance + dashWidth).clamp(0.0, metric.length);
        canvas.drawPath(metric.extractPath(distance, end), paint);
        distance = end + dashGap;
      }
    }
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
