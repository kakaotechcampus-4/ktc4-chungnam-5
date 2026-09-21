import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

const List<String> _weekdayNames = ['월', '화', '수', '목', '금', '토', '일'];

String _formatDateShort(DateTime d) =>
    '${d.month}월 ${d.day}일 (${_weekdayNames[d.weekday - 1]})';

class MealInputScreen extends StatefulWidget {
  const MealInputScreen({super.key});

  @override
  State<MealInputScreen> createState() => _MealInputScreenState();
}

class _MealInputScreenState extends State<MealInputScreen> {
  int _selectedTab = 0; // 0: 사진으로, 1: 검색
  int _selectedMealType = 2; // 0: 아침, 1: 점심, 2: 저녁
  DateTime _eatenAt = DateTime.now();
  double _satiety = 20;

  final TextEditingController _searchController = TextEditingController();

  void _pickPhoto() {}

  Future<void> _changeDateTime() async {
    final picked = await showModalBottomSheet<DateTime>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
      builder: (context) => _DateTimeDialPicker(initial: _eatenAt),
    );
    if (picked == null) return;
    setState(() => _eatenAt = picked);
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _startAnalysis() {}

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(title: const Text('식사 기록')),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.screenHorizontal,
            AppSpacing.lg,
            AppSpacing.screenHorizontal,
            AppLayout.scrollBottomPadding,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // 탭
              Container(
                padding: const EdgeInsets.all(AppSpacing.xs),
                decoration: const BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: AppRadius.pillRadius,
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: _SegmentTab(
                        label: '사진으로',
                        selected: _selectedTab == 0,
                        onTap: () => setState(() => _selectedTab = 0),
                      ),
                    ),
                    Expanded(
                      child: _SegmentTab(
                        label: '검색',
                        selected: _selectedTab == 1,
                        onTap: () => setState(() => _selectedTab = 1),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: AppSpacing.lg),

              // 사진으로 / 검색 — 탭에 따라 내용을 바꾼다.
              if (_selectedTab == 0)
                _buildPhotoUpload()
              else
                _buildSearchInput(),
              const SizedBox(height: AppSpacing.xl),

              // 끼니 선택
              Text('끼니', style: AppTypography.sectionHead),
              const SizedBox(height: AppSpacing.md),
              Row(
                children: [
                  _MealTypeButton(
                    label: '아침',
                    selected: _selectedMealType == 0,
                    onTap: () => setState(() => _selectedMealType = 0),
                  ),
                  const SizedBox(width: AppSpacing.md),
                  _MealTypeButton(
                    label: '점심',
                    selected: _selectedMealType == 1,
                    onTap: () => setState(() => _selectedMealType = 1),
                  ),
                  const SizedBox(width: AppSpacing.md),
                  _MealTypeButton(
                    label: '저녁',
                    selected: _selectedMealType == 2,
                    onTap: () => setState(() => _selectedMealType = 2),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.lg),

              // 식사 시각
              Text('식사 시각', style: AppTypography.sectionHead),
              const SizedBox(height: AppSpacing.md),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(AppSpacing.md),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          '${_formatDate(_eatenAt)}',
                          style: AppTypography.cardTitle,
                        ),
                      ),
                      TextButton(
                        onPressed: _changeDateTime,
                        child: const Text('변경'),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.lg),

              // 포만감
              Text('식사 후 포만감', style: AppTypography.sectionHead),
              const SizedBox(height: AppSpacing.sm),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(AppSpacing.md),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '식사 직전 배부른 정도예요. 알림을 놓쳤다면 여기에 입력해요.',
                        style: AppTypography.bodySecondary,
                      ),
                      const SizedBox(height: AppSpacing.md),
                      Row(
                        children: [
                          Expanded(
                            child: Slider(
                              value: _satiety,
                              min: 0,
                              max: 100,
                              divisions: 20,
                              onChanged: (v) => setState(() => _satiety = v),
                              activeColor: AppColors.primary,
                              inactiveColor: AppColors.surfaceMuted,
                            ),
                          ),
                          const SizedBox(width: AppSpacing.md),
                          Container(
                            width: 56,
                            height: 36,
                            alignment: Alignment.center,
                            decoration: BoxDecoration(
                              color: AppColors.surface,
                              borderRadius: AppRadius.mdRadius,
                            ),
                            child: Text(
                              '${_satiety.toInt()}%',
                              style: AppTypography.cardTitle,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: AppSpacing.xl),

              // 분석 버튼
              SizedBox(
                width: double.infinity,
                height: 52,
                child: ElevatedButton(
                  onPressed: _startAnalysis,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    shape: RoundedRectangleBorder(
                      borderRadius: AppRadius.lgRadius,
                    ),
                  ),
                  child: Text('분석 시작', style: AppTypography.buttonLabel),
                ),
              ),
              const SizedBox(height: AppSpacing.xl),
            ],
          ),
        ),
      ),
    );
  }

  String _formatDate(DateTime dt) {
    // YYYY. MM. DD hh:mm 형태(간단 구현)
    return '${dt.year.toString().padLeft(4, '0')}. ${dt.month.toString().padLeft(2, '0')}. ${dt.day.toString().padLeft(2, '0')} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
  }

  Widget _buildPhotoUpload() {
    return CustomPaint(
      painter: const _DashedBorderPainter(),
      child: InkWell(
        onTap: _pickPhoto,
        borderRadius: AppRadius.lgRadius,
        child: Container(
          width: double.infinity,
          height: 220,
          alignment: Alignment.center,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 56,
                height: 56,
                decoration: const BoxDecoration(
                  color: AppColors.surface,
                  shape: BoxShape.circle,
                ),
                child: const Icon(Icons.add, color: AppColors.primary),
              ),
              const SizedBox(height: AppSpacing.md),
              Text('사진 올리기', style: AppTypography.sectionHead),
              const SizedBox(height: AppSpacing.xs),
              Text('갤러리에서 선택하거나 텍스트로 입력', style: AppTypography.bodySecondary),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildSearchInput() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: AppRadius.mdRadius,
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: [
              const Icon(
                Icons.search,
                size: AppLayout.tabIconSize,
                color: AppColors.inactive,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: TextField(
                  controller: _searchController,
                  style: AppTypography.body,
                  decoration: InputDecoration(
                    hintText: '음식 이름으로 검색해요',
                    hintStyle: AppTypography.bodySecondary,
                    border: InputBorder.none,
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                      vertical: AppSpacing.md,
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: AppSpacing.xl),
        Center(
          child: Text('검색 결과가 여기에 표시돼요', style: AppTypography.bodySecondary),
        ),
      ],
    );
  }
}

/// "변경" 버튼이 여는 바텀시트. 달력/시계 대신 숫자 다이얼(휠)로 조정한다.
class _DateTimeDialPicker extends StatefulWidget {
  const _DateTimeDialPicker({required this.initial});

  final DateTime initial;

  @override
  State<_DateTimeDialPicker> createState() => _DateTimeDialPickerState();
}

class _DateTimeDialPickerState extends State<_DateTimeDialPicker> {
  static const double _itemExtent = 36;

  late final List<DateTime> _dates;
  late int _dateIndex;
  late int _hour;
  late int _minute;

  @override
  void initState() {
    super.initState();
    final today = DateTime.now();
    final startDate = DateTime(
      today.year,
      today.month,
      today.day,
    ).subtract(const Duration(days: 365));
    _dates = List.generate(366, (i) => startDate.add(Duration(days: i)));

    final initialDay = DateTime(
      widget.initial.year,
      widget.initial.month,
      widget.initial.day,
    );
    final foundIndex = _dates.indexWhere((d) => d == initialDay);
    _dateIndex = foundIndex >= 0 ? foundIndex : _dates.length - 1;
    _hour = widget.initial.hour;
    _minute = widget.initial.minute;
  }

  DateTime get _result {
    final d = _dates[_dateIndex];
    return DateTime(d.year, d.month, d.day, _hour, _minute);
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AppSpacing.screenHorizontal,
          AppSpacing.lg,
          AppSpacing.screenHorizontal,
          AppSpacing.lg,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('식사 시각 변경', style: AppTypography.sectionHead),
            const SizedBox(height: AppSpacing.md),
            SizedBox(
              height: 180,
              child: Row(
                children: [
                  Expanded(
                    flex: 3,
                    child: CupertinoPicker(
                      itemExtent: _itemExtent,
                      scrollController: FixedExtentScrollController(
                        initialItem: _dateIndex,
                      ),
                      onSelectedItemChanged: (i) =>
                          setState(() => _dateIndex = i),
                      children: _dates
                          .map(
                            (d) => Center(
                              child: Text(
                                _formatDateShort(d),
                                style: AppTypography.body,
                              ),
                            ),
                          )
                          .toList(),
                    ),
                  ),
                  Expanded(
                    child: CupertinoPicker(
                      itemExtent: _itemExtent,
                      scrollController: FixedExtentScrollController(
                        initialItem: _hour,
                      ),
                      onSelectedItemChanged: (i) => setState(() => _hour = i),
                      children: List.generate(
                        24,
                        (h) => Center(
                          child: Text(
                            h.toString().padLeft(2, '0'),
                            style: AppTypography.body,
                          ),
                        ),
                      ),
                    ),
                  ),
                  Expanded(
                    child: CupertinoPicker(
                      itemExtent: _itemExtent,
                      scrollController: FixedExtentScrollController(
                        initialItem: _minute,
                      ),
                      onSelectedItemChanged: (i) => setState(() => _minute = i),
                      children: List.generate(
                        60,
                        (m) => Center(
                          child: Text(
                            m.toString().padLeft(2, '0'),
                            style: AppTypography.body,
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: AppSpacing.lg),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Text('취소'),
                  ),
                ),
                const SizedBox(width: AppSpacing.md),
                Expanded(
                  child: FilledButton(
                    onPressed: () => Navigator.of(context).pop(_result),
                    child: const Text('완료'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _SegmentTab extends StatelessWidget {
  const _SegmentTab({required this.label, required this.selected, this.onTap});

  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadius.pillRadius,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.sm),
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : Colors.transparent,
          borderRadius: AppRadius.pillRadius,
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: selected ? AppTypography.emphasis : AppTypography.caption,
        ),
      ),
    );
  }
}

class _MealTypeButton extends StatelessWidget {
  const _MealTypeButton({
    required this.label,
    required this.selected,
    this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: OutlinedButton(
        onPressed: onTap,
        style: OutlinedButton.styleFrom(
          backgroundColor: selected
              ? AppColors.primaryTint
              : Colors.transparent,
          foregroundColor: selected ? AppColors.primary : AppColors.textPrimary,
          side: BorderSide(
            color: selected ? AppColors.primary : AppColors.borderStrong,
          ),
          shape: RoundedRectangleBorder(borderRadius: AppRadius.lgRadius),
        ),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Text(label, style: AppTypography.body),
        ),
      ),
    );
  }
}

class _DashedBorderPainter extends CustomPainter {
  const _DashedBorderPainter();

  static const double _dash = 6;
  static const double _gap = 6;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.borderStrong
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;

    final path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(
          Offset.zero & size,
          const Radius.circular(AppRadius.lg),
        ),
      );

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
