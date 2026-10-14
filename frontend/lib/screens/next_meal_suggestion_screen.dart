import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../common/api_format.dart';
import '../common/polling.dart';
import '../navigation/nav_icons.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

/// 끼니 피드백(7번) — 이번 끼니 Q·Q·S + 영양소 상태 + AI 제안.
///
/// Figma `S6BHeo0ZebzY4PAQOSp0EW` node `1:1800`(화면 7 - 단기 피드백(수정)).
///
/// 제안 문장은 AI 가 만들어 늦게 온다. 처음 `GET /meals/{id}/feedback` 을
/// 부르면 서버가 생성 작업을 걸고, 준비될 때까지(`PENDING`·`GENERATING`)
/// 잠깐씩 다시 묻는다. Q·Q·S 와 영양소 상태는 그와 상관없이 먼저 보인다.
///
/// 끼니 종류에 따라 아래가 달라진다.
/// - 아침·점심: `이런 음식은 어때요?` 제안과 `챗봇으로 다른 음식 추천받기`.
/// - 저녁: 제안과 챗봇 버튼 없이 그날의 하루 요약(`/insights/daily`). 없거나 낡았으면
///   새로 만들어 달라고 한 뒤 기다린다.
///
/// 제안을 저장하거나 다시 받는 API 는 아직 없어 그 버튼은 두지 않는다.
class NextMealSuggestionScreen extends StatefulWidget {
  final String mealId;

  /// 평가 화면에서 받은 확정 결과. 없으면(다른 곳에서 열면) 서버에서 받는다.
  final MealEvaluation? evaluation;

  /// 먹은 시각(KST 벽시계). 하루 요약의 날짜다. 없으면 끼니를 조회해 알아낸다.
  final DateTime? eatenAt;

  const NextMealSuggestionScreen({
    super.key,
    required this.mealId,
    this.evaluation,
    this.eatenAt,
  });

  @override
  State<NextMealSuggestionScreen> createState() =>
      _NextMealSuggestionScreenState();
}

class _NextMealSuggestionScreenState extends State<NextMealSuggestionScreen> {
  /// 제안이 준비됐는지 처음 다시 묻는 간격. 갈수록 늘린다([pollUntil]).
  static const _feedbackPoll = Duration(milliseconds: 1500);

  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  late MealEvaluation? evaluation = widget.evaluation;
  MealFeedback? feedback;
  bool isLoading = true;
  String? errorMessage;

  /// 제안 생성이 실패했는지(`FAILED`). 다시 확정해 새로 만들 수 있다.
  bool feedbackFailed = false;

  /// 제안을 기다리다 한도를 넘겼는지. 서버는 계속 만든다 — 나중에 볼 수 있다.
  bool feedbackTimedOut = false;

  /// 확정 때 적은 식후 포만감. 제안을 다시 만들 때(재확정) 그대로 보낸다.
  int? satietyAfterPct;

  /// 끼니 종류(`BREAKFAST` · `LUNCH` · `DINNER` …). 알아내기 전에는 null.
  /// 끼니를 못 불러오면 `UNKNOWN` 으로 두고 저녁이 아닌 것처럼 제안을 보인다.
  String? mealType;

  bool get _isDinner => mealType == 'DINNER';

  /// 이 화면 전체를 그림으로 찍을 때 쓴다(챗봇 버튼의 밀려 나가는 효과).
  final GlobalKey _screenKey = GlobalKey();

  /// 하루 요약. [dailyDate] 는 그 날짜(KST).
  DateTime? dailyDate;
  DailyFeedback? daily;
  bool dailyFailed = false;
  bool dailyTimedOut = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      isLoading = evaluation == null;
      errorMessage = null;
    });
    if (evaluation == null) {
      try {
        final result = await _api.fetchEvaluation(widget.mealId);
        if (!mounted) return;
        setState(() => evaluation = result);
      } catch (e) {
        if (mounted) setState(() => errorMessage = '불러오는 데 실패했어요: $e');
        return;
      } finally {
        if (mounted) setState(() => isLoading = false);
      }
    }
    _loadByMealType();
  }

  /// 끼니 종류를 알아내 저녁이면 하루 요약을, 아니면 AI 제안을 불러온다.
  /// 저녁에는 제안을 보이지 않으므로 제안도 요청하지 않는다.
  Future<void> _loadByMealType() async {
    DateTime? eatenAt = widget.eatenAt;
    try {
      final meal = await _api.fetchMeal(widget.mealId);
      if (!mounted) return;
      eatenAt ??= meal.eatenAt;
      setState(() {
        mealType = meal.mealType;
        satietyAfterPct = meal.satietyAfterPct;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => mealType = 'UNKNOWN');
    }
    if (_isDinner && eatenAt != null) {
      _loadDaily(eatenAt);
    } else {
      _pollFeedback();
    }
  }

  /// 저녁 끼니의 그날 하루 요약을 받는다. 없거나 낡았으면 새로 만들어
  /// 달라고 하고 준비될 때까지 간격을 늘려 가며 다시 묻는다(약 1분).
  /// [refresh] 면(실패 뒤 "다시 시도") 상태와 상관없이 새로 만들어 달라고 한다.
  /// 실패해도 화면은 그대로다 — 이 칸만 안내로 바뀐다.
  Future<void> _loadDaily(DateTime date, {bool refresh = false}) async {
    setState(() {
      dailyDate = date;
      dailyFailed = false;
      dailyTimedOut = false;
      if (refresh) daily = null;
    });
    final Duration interval;
    try {
      if (!refresh) {
        final result = await _api.fetchDailyFeedback(date);
        if (!mounted) return;
        setState(() => daily = result);
        if (!result.needsRefresh && !result.isGenerating) return;
        refresh = result.needsRefresh;
      }
      interval = refresh
          ? await _api.refreshDailyFeedback(date)
          : _feedbackPoll;
    } catch (_) {
      if (mounted) setState(() => dailyFailed = true);
      return;
    }
    final result = await pollUntil<DailyFeedback>(
      fetch: () => _api.fetchDailyFeedback(date),
      isDone: (r) => !r.isGenerating && !r.needsRefresh,
      isActive: () => mounted,
      onResult: (r) => setState(() => daily = r),
      first: interval,
    );
    if (!mounted) return;
    if (result == null) setState(() => dailyTimedOut = true);
  }

  Future<void> _pollFeedback() async {
    setState(() {
      feedbackFailed = false;
      feedbackTimedOut = false;
    });
    final result = await pollUntil<MealFeedback>(
      fetch: () => _api.fetchFeedback(widget.mealId),
      isDone: (r) => !r.isPending,
      isActive: () => mounted,
      onResult: (r) => setState(() => feedback = r),
      first: _feedbackPoll,
      askNow: true,
    );
    if (!mounted) return;
    setState(() {
      if (result == null) {
        feedbackTimedOut = true;
      } else if (result.feedbackStatus == 'FAILED') {
        feedbackFailed = true;
      }
    });
  }

  /// 제안 생성이 실패했을 때 "다시 만들기". 서버의 재시도 진입점은 재확정이다 —
  /// 문장이 없으면 생성 작업을 다시 건다(BE `services/feedback.py`). 확정 때와
  /// 같은 식후 포만감을 보내므로 점수는 바뀌지 않는다.
  Future<void> _regenerateFeedback() async {
    final pct = satietyAfterPct;
    if (pct == null) return;
    setState(() {
      feedbackFailed = false;
      feedback = null;
    });
    try {
      final result = await _api.confirm(widget.mealId, satietyAfterPct: pct);
      if (!mounted) return;
      setState(() => evaluation = result);
    } catch (e) {
      if (!mounted) return;
      setState(() => feedbackFailed = true);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('다시 만들지 못했어요: $e')));
      return;
    }
    _pollFeedback();
  }

  /// 식사 기록 흐름을 모두 닫고 홈 탭(RootShell 0번)으로 돌아간다.
  /// 기록 탭에서 시작했어도 홈으로 간다.
  void _goHome() => _closeFlowAndGoTo(TabState.home);

  /// "챗봇으로 다른 음식 추천받기" — 식사 기록 흐름을 닫고 AI 탭으로 간다.
  ///
  /// 평소 뒤로 가기는 화면이 오른쪽으로 밀려 나가는데, 앞으로 나아가는 느낌을 주려고
  /// 이 화면을 그림으로 찍어 **왼쪽으로** 밀어 내보낸다. 그 사이 화면은 바로 닫고 AI 탭을
  /// 보이게 해 둔다. 그림을 못 찍으면 그냥 닫는다.
  Future<void> _goChatbot() async {
    final navigator = Navigator.of(context);
    final overlay = Overlay.of(context, rootOverlay: true);
    final tabs = context.read<TabState>();
    final route = ModalRoute.of(context);
    final pixelRatio = MediaQuery.devicePixelRatioOf(context);

    ui.Image? snapshot;
    try {
      // 버튼 누름 효과 등으로 아직 다시 그려야 하는 상태면 그림을 찍을 수 없다.
      // 한 프레임이 끝난 뒤에 찍는다.
      await WidgetsBinding.instance.endOfFrame;
      final boundary =
          _screenKey.currentContext?.findRenderObject() as RenderRepaintBoundary?;
      snapshot = await boundary?.toImage(pixelRatio: pixelRatio);
    } catch (_) {
      snapshot = null;
    }

    tabs.setIndex(TabState.ai);
    if (snapshot != null) {
      final image = snapshot;
      late final OverlayEntry entry;
      entry = OverlayEntry(
        builder: (_) => _SlideOutSnapshot(
          image: image,
          onDone: () {
            entry.remove();
            image.dispose();
          },
        ),
      );
      overlay.insert(entry);
    }
    // 밀려 나가는 그림이 이 화면 대신 보이므로 닫는 애니메이션 없이 바로 없앤다.
    if (route != null && route.isActive) navigator.removeRoute(route);
    navigator.popUntil((r) => r.isFirst);
  }

  void _closeFlowAndGoTo(int tab) {
    context.read<TabState>().setIndex(tab);
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    return RepaintBoundary(
      key: _screenKey,
      child: Scaffold(
        backgroundColor: AppColors.background,
        appBar: AppBar(
          centerTitle: true,
          leading: IconButton(
            icon: const Icon(Icons.chevron_left),
            onPressed: () => Navigator.of(context).maybePop(),
            tooltip: '뒤로',
          ),
          title: const Text('끼니 피드백'),
          titleTextStyle: AppTypography.sectionHead.copyWith(fontSize: 14),
        ),
        body: SafeArea(
          child: isLoading
              ? const Center(child: CircularProgressIndicator())
              : evaluation == null
              ? Center(
                  child: RetryBlock(
                    message: errorMessage ?? '잠시 후 다시 시도해 주세요',
                    onRetry: _load,
                  ),
                )
              : _buildContent(),
        ),
      ),
    );
  }

  Widget _buildContent() {
    final eval = evaluation!;
    return Column(
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.screenHorizontal,
              AppSpacing.md,
              AppSpacing.screenHorizontal,
              AppSpacing.lg,
            ),
            children: [
              _ScoreCard(evaluation: eval),
              const SizedBox(height: 14),
              _buildNutrientSection(eval),
              const SizedBox(height: 14),
              // 아침·점심은 제안 + 챗봇 버튼, 저녁은 하루 요약. 끼니 종류를 알기 전에는 자리만 둔다.
              if (mealType == null)
                const SkeletonBox(width: double.infinity, height: 72)
              else if (_isDinner)
                ..._buildDaily()
              else
                ..._buildSuggestionSection(),
              const SizedBox(height: 14),
              const _DoctorNotice(),
            ],
          ),
        ),
        _buildHomeButton(),
      ],
    );
  }

  // ── 영양소 상태 ─────────────────────────────────────────────

  Widget _buildNutrientSection(MealEvaluation eval) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              '영양소 상태',
              style: AppTypography.cardTitle.copyWith(fontSize: 12.5),
            ),
            Text(
              '${stageLabel(eval.stage)} 1끼 기준',
              style: AppTypography.caption.copyWith(
                fontSize: 9.5,
                color: AppColors.textTertiary,
              ),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
          decoration: BoxDecoration(
            color: AppColors.surface,
            border: Border.all(color: AppColors.border),
            borderRadius: AppRadius.lgRadius,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (var i = 0; i < eval.nutrients.length; i++) ...[
                if (i > 0) const SizedBox(height: 11),
                _NutrientRow(nutrient: eval.nutrients[i]),
              ],
            ],
          ),
        ),
      ],
    );
  }

  // ── 제안 ────────────────────────────────────────────────────

  List<Widget> _buildSuggestionSection() {
    return [
      Text(
        '이런 음식은 어때요?',
        style: AppTypography.cardTitle.copyWith(fontSize: 12.5),
      ),
      const SizedBox(height: AppSpacing.sm),
      ..._buildSuggestions(),
      _ChatbotButton(onTap: _goChatbot),
    ];
  }

  List<Widget> _buildSuggestions() {
    // 의료 판단이 필요한 내용이면 제안 대신 상담 안내만 보인다(§7).
    if (feedback?.isBlocked == true) {
      return [const NoticeBlock(), const SizedBox(height: AppSpacing.sm)];
    }
    final suggestions = feedback?.suggestions ?? const [];
    if (suggestions.isNotEmpty) {
      return [
        for (final s in suggestions) ...[
          _SuggestionCard(suggestion: s),
          const SizedBox(height: AppSpacing.sm),
        ],
      ];
    }
    if (feedbackTimedOut) {
      return [
        WaitNotice(
          message: '제안을 만드는 데 시간이 걸리고 있어요. 나중에 기록 탭에서 확인해 주세요.',
          actionLabel: '다시 확인하기',
          onAction: _pollFeedback,
        ),
        const SizedBox(height: AppSpacing.sm),
      ];
    }
    if (feedbackFailed || feedback?.isPending == false) {
      final canRegenerate = feedbackFailed && satietyAfterPct != null;
      return [
        WaitNotice(
          message: canRegenerate
              ? '지금은 제안을 만들지 못했어요.'
              : '지금은 제안을 만들지 못했어요. 나중에 기록 탭에서 다시 볼 수 있어요.',
          actionLabel: canRegenerate ? '다시 만들기' : null,
          onAction: canRegenerate ? _regenerateFeedback : null,
        ),
        const SizedBox(height: AppSpacing.sm),
      ];
    }
    return const [
      SkeletonBox(width: double.infinity, height: 72),
      SizedBox(height: AppSpacing.sm),
      SkeletonBox(width: double.infinity, height: 72),
      SizedBox(height: AppSpacing.sm),
    ];
  }

  // ── 하루 요약(저녁 끼니만) ───────────────────────────────────

  /// "오늘 하루 요약" — 그 끼니를 먹은 날 전체에 대한 한 줄.
  List<Widget> _buildDaily() {
    final date = dailyDate;
    final now = DateTime.now();
    final isToday =
        date != null &&
        date.year == now.year &&
        date.month == now.month &&
        date.day == now.day;
    final title = date == null || isToday
        ? '오늘 하루 요약'
        : '${date.month}월 ${date.day}일 하루 요약';
    final d = daily;
    final summary = d?.summary;
    final Widget body;
    if (summary != null) {
      body = Text(summary, style: AppTypography.body);
    } else if (dailyTimedOut && date != null) {
      body = WaitNotice(
        message: '하루 요약을 만드는 데 시간이 걸리고 있어요. 나중에 기록 탭에서 확인해 주세요.',
        actionLabel: '다시 확인하기',
        onAction: () => _loadDaily(date),
      );
    } else if (dailyFailed ||
        (d != null && !d.isGenerating && !d.needsRefresh)) {
      body = WaitNotice(
        message: '지금은 하루 요약을 만들지 못했어요.',
        actionLabel: date == null ? null : '다시 시도',
        onAction: date == null ? null : () => _loadDaily(date, refresh: true),
      );
    } else {
      body = const SkeletonBox(width: double.infinity, height: 40);
    }
    return [
      Text(title, style: AppTypography.cardTitle.copyWith(fontSize: 12.5)),
      const SizedBox(height: AppSpacing.sm),
      Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: AppColors.surface,
          border: Border.all(color: AppColors.border),
          borderRadius: AppRadius.lgRadius,
        ),
        child: body,
      ),
    ];
  }

  Widget _buildHomeButton() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.sm,
        AppSpacing.screenHorizontal,
        AppSpacing.lg,
      ),
      child: SizedBox(
        width: double.infinity,
        height: 52,
        child: ElevatedButton(
          onPressed: _goHome,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primary,
            elevation: 0,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(15)),
          ),
          child: Text('홈으로', style: AppTypography.buttonLabel),
        ),
      ),
    );
  }
}

/// 이번 끼니 Q·Q·S — `이번 끼니` + 단계 칩, 그 아래 양·질·포만감 점수와 막대.
///
/// 막대 색은 Q·Q·S 색 규칙(양 노랑 · 질 초록 · 포만감 빨강2)을 따른다.
/// 점수가 없으면(`null`) `-` 와 빈 막대다.
class _ScoreCard extends StatelessWidget {
  const _ScoreCard({required this.evaluation});

  final MealEvaluation evaluation;

  @override
  Widget build(BuildContext context) {
    final scores = evaluation.scores;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(14, 13, 14, 14),
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
                '이번 끼니',
                style: AppTypography.cardTitle.copyWith(fontSize: 12),
              ),
              _Chip(
                text: '${stageLabel(evaluation.stage)} 기준',
                background: AppColors.surfaceMuted,
                foreground: AppColors.textSecondary,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: _ScoreColumn(
                  label: '양',
                  score: scores.quantity,
                  color: AppColors.quantity,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _ScoreColumn(
                  label: '질',
                  score: scores.quality,
                  color: AppColors.quality,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: _ScoreColumn(
                  label: '포만감',
                  score: scores.satiety,
                  color: AppColors.satiety,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _ScoreColumn extends StatelessWidget {
  const _ScoreColumn({
    required this.label,
    required this.score,
    required this.color,
  });

  final String label;
  final int? score;
  final Color color;

  @override
  Widget build(BuildContext context) {
    final s = score;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: AppTypography.caption.copyWith(
            fontSize: 9.5,
            color: AppColors.textTertiary,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          s == null ? '-' : '$s',
          style: AppTypography.cardTitle.copyWith(fontSize: 20),
        ),
        const SizedBox(height: 4),
        _Bar(ratio: (s ?? 0) / 100, color: color),
      ],
    );
  }
}

/// 6px 둥근 막대. 빈 부분은 연한 회색이고, 화면이 열리면 왼쪽에서부터 차오른다.
class _Bar extends StatelessWidget {
  const _Bar({required this.ratio, required this.color});

  /// 0~1.
  final double ratio;
  final Color color;

  /// 막대가 차오르는 시간(식사 평가 화면과 같다).
  static const Duration _fillDuration = Duration(milliseconds: 700);

  @override
  Widget build(BuildContext context) {
    return TweenAnimationBuilder<double>(
      // begin 이 0 이라 처음 그릴 때도 0 에서부터 차오른다. 값이 바뀌면 지금 값에서 이어서 움직인다.
      tween: Tween<double>(begin: 0, end: ratio.clamp(0.0, 1.0)),
      duration: _fillDuration,
      curve: Curves.easeOutCubic,
      builder: (context, value, _) => ClipRRect(
        borderRadius: BorderRadius.circular(3),
        child: LinearProgressIndicator(
          value: value,
          minHeight: 6,
          backgroundColor: AppColors.surfaceMuted,
          valueColor: AlwaysStoppedAnimation(color),
        ),
      ),
    );
  }
}

/// 작은 알약 칩(9 Bold · 알약 9 · 안쪽 여백 7×3).
class _Chip extends StatelessWidget {
  const _Chip({
    required this.text,
    required this.background,
    required this.foreground,
  });

  final String text;
  final Color background;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(9),
      ),
      child: Text(
        text,
        style: AppTypography.emphasis.copyWith(
          fontSize: 9,
          height: 1.25,
          color: foreground,
        ),
      ),
    );
  }
}

/// 영양소 한 줄 — 왼쪽에 `이름 · 현재 / 목표`, 오른쪽 끝에 상태 글, 아래에 막대.
///
/// 상태 색: 부족 노랑 · 초과 빨강 · 적정 초록. 목표를 모르면 막대는 비워 둔다.
class _NutrientRow extends StatelessWidget {
  const _NutrientRow({required this.nutrient});

  final NutrientStatus nutrient;

  /// 천 단위 쉼표(`1620` → `1,620`).
  static String _num(double v) => v.toInt().toString().replaceAllMapped(
    RegExp(r'\B(?=(\d{3})+(?!\d))'),
    (_) => ',',
  );

  Color get _color => switch (nutrient.state) {
    'SHORT' => AppColors.quantity,
    'OVER' => AppColors.primary,
    'OK' => AppColors.quality,
    _ => AppColors.textTertiary,
  };

  /// 왼쪽 보조 글(`18 / 30g`). 목표나 현재 값을 모르면 비운다.
  String? get _detail {
    final current = nutrient.current;
    final target = nutrient.target;
    if (current == null || target == null) return null;
    return '${_num(current)} / ${_num(target)}${nutrient.unit}';
  }

  /// 오른쪽 상태 글(`12g 부족` · `320mg 초과` · `적정`).
  String get _status {
    final current = nutrient.current;
    final target = nutrient.target;
    // 영양 정보가 있는 음식이 없으면 이 영양소는 계산하지 못했다.
    if (current == null) return '정보 없음';
    if (target == null) return '${_num(current)}${nutrient.unit}';
    final diff = _num((current - target).abs());
    return switch (nutrient.state) {
      'SHORT' => '$diff${nutrient.unit} 부족',
      'OVER' => '$diff${nutrient.unit} 초과',
      'OK' => '적정',
      _ => '',
    };
  }

  @override
  Widget build(BuildContext context) {
    final target = nutrient.target;
    final hasTarget = target != null && target > 0;
    final ratio = hasTarget ? ((nutrient.current ?? 0) / target).clamp(0.0, 1.0) : 0.0;
    final detail = _detail;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(
              nutrient.label,
              style: AppTypography.cardTitle.copyWith(fontSize: 11.5),
            ),
            if (detail != null) ...[
              const SizedBox(width: 6),
              Text(
                detail,
                style: AppTypography.caption.copyWith(
                  fontSize: 9.5,
                  color: AppColors.textTertiary,
                ),
              ),
            ],
            const Spacer(),
            Text(
              _status,
              style: AppTypography.emphasis.copyWith(
                fontSize: 9.5,
                color: _color,
              ),
            ),
          ],
        ),
        const SizedBox(height: 5),
        _Bar(ratio: ratio, color: _color),
      ],
    );
  }
}

/// 제안 카드 — 음식 이름, 채우거나 줄이는 영양 칩, 한 줄 조언. 사진·아이콘은 없다.
class _SuggestionCard extends StatelessWidget {
  const _SuggestionCard({required this.suggestion});

  final MealSuggestion suggestion;

  static String _nutrientLabel(String code) => switch (code) {
    'PROTEIN' => '단백질',
    'FIBER' => '식이섬유',
    'SODIUM' => '나트륨',
    _ => code,
  };

  @override
  Widget build(BuildContext context) {
    final s = suggestion;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: AppRadius.lgRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            s.foodName,
            style: AppTypography.cardTitle.copyWith(fontSize: 12.5),
          ),
          if (s.nutrients.isNotEmpty) ...[
            const SizedBox(height: 5),
            Wrap(
              spacing: 5,
              runSpacing: 4,
              children: [
                for (final n in s.nutrients)
                  // 채우면 초록, 줄이면(음수) 빨강2 계열.
                  _Chip(
                    text:
                        '${_nutrientLabel(n.code)} '
                        '${n.amountG < 0 ? '−' : '+'}${n.amountG.abs().toInt()}g',
                    background: n.amountG < 0
                        ? AppColors.primaryStrongBg
                        : AppColors.qualityBg,
                    foreground: n.amountG < 0
                        ? AppColors.primaryStrong
                        : AppColors.quality,
                  ),
              ],
            ),
          ],
          const SizedBox(height: 5),
          Text(
            s.advice,
            style: AppTypography.caption.copyWith(
              fontSize: 9.5,
              color: AppColors.textTertiary,
            ),
          ),
        ],
      ),
    );
  }
}

/// "챗봇으로 다른 음식 추천받기" — 점선 테두리 버튼.
class _ChatbotButton extends StatelessWidget {
  const _ChatbotButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      foregroundPainter: const _DashedBorderPainter(),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(13),
        child: SizedBox(
          height: 46,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              IconTheme(
                data: const IconThemeData(
                  size: 24,
                  color: AppColors.textSecondary,
                ),
                child: const AiTabIcon(),
              ),
              const SizedBox(width: 4),
              Text(
                '챗봇으로 다른 음식 추천받기',
                style: AppTypography.cardTitle.copyWith(
                  fontSize: 12,
                  color: AppColors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 점선 둥근 사각형 테두리.
class _DashedBorderPainter extends CustomPainter {
  const _DashedBorderPainter();

  static const double _dash = 5;
  static const double _gap = 4;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.border
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    final rect = (Offset.zero & size).deflate(paint.strokeWidth / 2);
    final path = Path()
      ..addRRect(RRect.fromRectAndRadius(rect, const Radius.circular(13)));
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        canvas.drawPath(metric.extractPath(distance, distance + _dash), paint);
        distance += _dash + _gap;
      }
    }
  }

  @override
  bool shouldRepaint(_DashedBorderPainter oldDelegate) => false;
}

/// 맨 아래 안내 — 투약량 판단은 하지 않고 담당 의사 상담을 안내한다(§7).
class _DoctorNotice extends StatelessWidget {
  const _DoctorNotice();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.mdRadius,
      ),
      child: Text(
        '투약량 조절이 필요하다고 느끼면 담당 의사와 상의해 주세요.',
        style: AppTypography.caption.copyWith(
          fontSize: 10,
          height: 1.5,
          color: AppColors.textSecondary,
        ),
      ),
    );
  }
}

/// 찍어 둔 화면 그림을 왼쪽으로 밀어 내보내고 끝나면 [onDone] 을 부른다.
/// 화면을 닫은 뒤에도 움직이도록 오버레이에 올려 쓴다.
class _SlideOutSnapshot extends StatefulWidget {
  const _SlideOutSnapshot({required this.image, required this.onDone});

  final ui.Image image;
  final VoidCallback onDone;

  @override
  State<_SlideOutSnapshot> createState() => _SlideOutSnapshotState();
}

class _SlideOutSnapshotState extends State<_SlideOutSnapshot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 280),
  );

  late final Animation<Offset> _offset = Tween<Offset>(
    begin: Offset.zero,
    end: const Offset(-1, 0),
  ).animate(CurvedAnimation(parent: _controller, curve: Curves.easeInOutCubic));

  @override
  void initState() {
    super.initState();
    _controller.forward().whenComplete(widget.onDone);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // 움직이는 동안 아래 화면이 눌리지 않게 막는다.
    return AbsorbPointer(
      child: SlideTransition(
        position: _offset,
        child: SizedBox.expand(
          child: RawImage(image: widget.image, fit: BoxFit.fill),
        ),
      ),
    );
  }
}
