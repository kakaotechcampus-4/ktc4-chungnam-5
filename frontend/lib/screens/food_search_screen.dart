import 'dart:async';

import 'package:flutter/material.dart';

import '../api/meal_api.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 영양 한 줄(`400kcal · 단백질 12g · 식이섬유 4g`). 값이 없는 성분은 뺀다.
/// [scale] 은 기준량 대비 먹은 양의 비율이다(기준량 값 그대로면 1).
String formatNutritionLine(MealNutrition n, {double scale = 1}) {
  String num(double v) => (v * scale).round().toString();
  final kcal = n.kcal;
  final protein = n.proteinG;
  final fiber = n.fiberG;
  final sodium = n.sodiumMg;
  return [
    if (kcal != null) '${num(kcal)}kcal',
    if (protein != null) '단백질 ${num(protein)}g',
    if (fiber != null)
      '식이섬유 ${num(fiber)}g'
    else if (sodium != null)
      '나트륨 ${num(sodium)}mg',
  ].join(' · ');
}

/// 식사 입력 "검색" 탭에서 여는 음식 검색 — Figma `NBoO62W0inEr5KsLtarAyH`
/// node `17:2196`.
///
/// 공공 식품영양성분 DB(`GET /nutrition/candidates`)를 검색해 여러 개를 고른다.
/// 검색어를 바꿔도 고른 것은 남는다. "음식 추가하기" 를 누르면 고른 순서대로
/// 돌려주고, 뒤로 가면 null 이다.
class FoodSearchScreen extends StatefulWidget {
  const FoodSearchScreen({super.key, required this.api});

  final MealApiService api;

  @override
  State<FoodSearchScreen> createState() => _FoodSearchScreenState();
}

class _FoodSearchScreenState extends State<FoodSearchScreen> {
  /// 글자를 칠 때마다 묻지 않고 잠깐 멈추면 검색한다.
  static const _debounce = Duration(milliseconds: 300);

  final _query = TextEditingController();
  Timer? _timer;

  List<FoodCandidate>? _results;
  String? _error;
  bool _searching = false;

  /// 검색한 글자. 응답이 늦게 와서 앞선 검색 결과가 덮어쓰지 않게 비교한다.
  String _lastQuery = '';

  /// 고른 음식(고른 순서). 검색어를 바꿔도 남는다.
  final Map<String, FoodCandidate> _picked = {};

  @override
  void initState() {
    super.initState();
    _query.addListener(_onQueryChanged);
  }

  @override
  void dispose() {
    _timer?.cancel();
    _query.dispose();
    super.dispose();
  }

  void _onQueryChanged() {
    _timer?.cancel();
    _timer = Timer(_debounce, _search);
    setState(() {}); // 지우기(✕) 버튼
  }

  Future<void> _search() async {
    _timer?.cancel();
    final q = _query.text.trim();
    if (q == _lastQuery) return;
    _lastQuery = q;
    if (q.isEmpty) {
      setState(() {
        _results = null;
        _error = null;
      });
      return;
    }
    setState(() {
      _searching = true;
      _error = null;
    });
    try {
      final results = await widget.api.searchCandidates(q);
      if (!mounted || q != _lastQuery) return;
      setState(() => _results = results);
    } catch (e) {
      if (!mounted || q != _lastQuery) return;
      setState(() => _error = '검색하지 못했어요: $e');
    } finally {
      if (mounted && q == _lastQuery) setState(() => _searching = false);
    }
  }

  void _toggle(FoodCandidate c) {
    setState(() {
      if (_picked.remove(c.foodRefId) == null) _picked[c.foodRefId] = c;
    });
  }

  void _done() => Navigator.of(context).pop(_picked.values.toList());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        centerTitle: true,
        leading: IconButton(
          icon: const Icon(Icons.chevron_left),
          onPressed: () => Navigator.of(context).maybePop(),
          tooltip: '뒤로',
        ),
        title: const Text('식사 기록'),
        titleTextStyle: AppTypography.sectionHead.copyWith(fontSize: 14),
      ),
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.screenHorizontal,
                AppSpacing.lg,
                AppSpacing.screenHorizontal,
                AppSpacing.sm,
              ),
              child: _SearchField(
                controller: _query,
                autofocus: true,
                onSubmitted: _search,
              ),
            ),
            Expanded(child: _buildResults()),
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppSpacing.screenHorizontal,
                AppSpacing.sm,
                AppSpacing.screenHorizontal,
                AppSpacing.lg,
              ),
              child: _AddButton(
                count: _picked.length,
                onTap: _picked.isEmpty ? null : _done,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildResults() {
    final results = _results;
    if (_searching && results == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final error = _error;
    if (error != null) {
      return _CenterMessage(error);
    }
    if (results == null) {
      return const _CenterMessage('음식명을 검색하여 추가해 보세요.');
    }
    if (results.isEmpty) {
      return const _CenterMessage('찾은 음식이 없어요. 다른 이름으로 검색해 보세요.');
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.screenHorizontal,
        vertical: AppSpacing.sm,
      ),
      itemCount: results.length,
      separatorBuilder: (_, _) => const SizedBox(height: 6),
      itemBuilder: (context, i) {
        final c = results[i];
        return _ResultCard(
          candidate: c,
          selected: _picked.containsKey(c.foodRefId),
          onTap: () => _toggle(c),
        );
      },
    );
  }
}

/// 식사 입력 "검색" 탭의 검색창 자리 — Figma `17:527`. 누르면 [onTap] 으로
/// 검색 화면을 연다. 글자는 검색 화면에서 친다.
class FoodSearchBar extends StatelessWidget {
  const FoodSearchBar({super.key, required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surface,
      shape: RoundedRectangleBorder(
        borderRadius: AppRadius.pillRadius,
        side: const BorderSide(color: AppColors.border),
      ),
      child: InkWell(
        onTap: onTap,
        customBorder: const StadiumBorder(),
        child: SizedBox(
          height: 40,
          child: Row(
            children: [
              const SizedBox(width: 16),
              Expanded(
                child: Text(
                  '음식명을 검색해 주세요.',
                  style: AppTypography.body.copyWith(
                    fontSize: 13,
                    color: AppColors.textSecondary,
                  ),
                ),
              ),
              const Icon(
                Icons.search,
                size: 20,
                color: AppColors.textSecondary,
              ),
              const SizedBox(width: 14),
            ],
          ),
        ),
      ),
    );
  }
}

/// 검색 화면 위쪽의 둥근 회색 검색창. 글자가 있으면 오른쪽 ✕ 로 지운다.
class _SearchField extends StatelessWidget {
  const _SearchField({
    required this.controller,
    required this.onSubmitted,
    this.autofocus = false,
  });

  final TextEditingController controller;
  final VoidCallback onSubmitted;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    final style = AppTypography.body.copyWith(fontSize: 14);
    return Container(
      height: 40,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: const BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.pillRadius,
      ),
      child: Row(
        children: [
          const Icon(Icons.search, size: 20, color: AppColors.textSecondary),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: controller,
              autofocus: autofocus,
              textInputAction: TextInputAction.search,
              onSubmitted: (_) => onSubmitted(),
              style: style,
              decoration: InputDecoration(
                hintText: '음식명을 검색해 주세요.',
                hintStyle: style.copyWith(color: AppColors.textTertiary),
                filled: false,
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                isDense: true,
                contentPadding: EdgeInsets.zero,
              ),
            ),
          ),
          if (controller.text.isNotEmpty)
            GestureDetector(
              onTap: controller.clear,
              child: const Icon(
                Icons.close,
                size: 20,
                color: AppColors.textSecondary,
              ),
            ),
        ],
      ),
    );
  }
}

/// 검색 결과 한 줄. 고르면 빨간 테두리·연한 바탕에 체크가 찬다.
class _ResultCard extends StatelessWidget {
  const _ResultCard({
    required this.candidate,
    required this.selected,
    required this.onTap,
  });

  final FoodCandidate candidate;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final serving = candidate.servingSizeG;
    final line = formatNutritionLine(candidate.nutrition);
    final shape = RoundedRectangleBorder(
      borderRadius: AppRadius.lgRadius,
      side: BorderSide(
        color: selected ? AppColors.primary : AppColors.border,
        width: selected ? 1.5 : 1,
      ),
    );
    return Material(
      color: selected ? AppColors.primaryTint : AppColors.surface,
      shape: shape,
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      candidate.displayName,
                      style: AppTypography.cardTitle.copyWith(fontSize: 15),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      [
                        if (line.isNotEmpty) line,
                        if (serving != null) '${serving.round()}g 기준',
                      ].join(' · '),
                      style: AppTypography.caption.copyWith(
                        color: AppColors.textSecondary,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Icon(
                selected ? Icons.check_circle : Icons.radio_button_unchecked,
                size: 22,
                color: selected ? AppColors.primary : AppColors.border,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// "② 음식 추가하기 →". 하나도 안 골랐으면 누를 수 없다.
class _AddButton extends StatelessWidget {
  const _AddButton({required this.count, required this.onTap});

  final int count;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      height: 52,
      child: ElevatedButton(
        onPressed: onTap,
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.primary,
          elevation: 0,
          shape: const RoundedRectangleBorder(borderRadius: AppRadius.xlRadius),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (count > 0) ...[
              Container(
                width: 18,
                height: 18,
                alignment: Alignment.center,
                decoration: const BoxDecoration(
                  color: AppColors.textInverse,
                  shape: BoxShape.circle,
                ),
                child: Text(
                  '$count',
                  style: AppTypography.emphasis.copyWith(
                    fontSize: 11,
                    height: 1,
                    color: AppColors.primary,
                  ),
                ),
              ),
              const SizedBox(width: 6),
            ],
            Text('음식 추가하기', style: AppTypography.buttonLabel),
            const SizedBox(width: 4),
            const Icon(
              Icons.arrow_forward,
              size: 16,
              color: AppColors.textInverse,
            ),
          ],
        ),
      ),
    );
  }
}

class _CenterMessage extends StatelessWidget {
  const _CenterMessage(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.screenHorizontal),
        child: Text(
          text,
          textAlign: TextAlign.center,
          style: AppTypography.bodySecondary,
        ),
      ),
    );
  }
}
