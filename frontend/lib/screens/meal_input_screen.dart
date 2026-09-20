import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

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

  void _pickPhoto() {}

  Future<void> _changeDateTime() async {
    final pickedDate = await showDatePicker(
      context: context,
      initialDate: _eatenAt,
      firstDate: DateTime(2020),
      lastDate: DateTime.now(),
    );
    if (pickedDate == null || !mounted) return;

    final pickedTime = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(_eatenAt),
    );
    if (pickedTime == null) return;

    setState(() {
      _eatenAt = DateTime(
        pickedDate.year,
        pickedDate.month,
        pickedDate.day,
        pickedTime.hour,
        pickedTime.minute,
      );
    });
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

              // 사진 업로드 영역
              CustomPaint(
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
                          child: const Icon(
                            Icons.add,
                            color: AppColors.primary,
                          ),
                        ),
                        const SizedBox(height: AppSpacing.md),
                        Text('사진 올리기', style: AppTypography.sectionHead),
                        const SizedBox(height: AppSpacing.xs),
                        Text(
                          '갤러리에서 선택하거나 텍스트로 입력',
                          style: AppTypography.bodySecondary,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
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
