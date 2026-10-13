import 'dart:typed_data';

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/meal_api.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'meal_analysis_screen.dart';

const List<String> _weekdayNames = ['월', '화', '수', '목', '금', '토', '일'];

String _formatDateShort(DateTime d) =>
    '${d.month}월 ${d.day}일 (${_weekdayNames[d.weekday - 1]})';

String _two(int n) => n.toString().padLeft(2, '0');

/// `2026년 08월 21일`.
String _formatDay(DateTime d) => '${d.year}년 ${_two(d.month)}월 ${_two(d.day)}일';

/// `18:10`.
String _formatTime(DateTime d) => '${_two(d.hour)}:${_two(d.minute)}';

/// "끼니" · "식사 시각" 같은 섹션 제목.
final TextStyle _sectionLabel = AppTypography.sectionHead.copyWith(fontSize: 12.5);

class MealInputScreen extends StatefulWidget {
  const MealInputScreen({super.key});

  @override
  State<MealInputScreen> createState() => _MealInputScreenState();
}

class _MealInputScreenState extends State<MealInputScreen> {
  int _selectedTab = 0; // 0: 사진으로, 1: 직접 입력
  int _selectedMealType = 2; // 0: 아침, 1: 점심, 2: 저녁

  /// [_selectedMealType] 순서의 서버 값.
  static const _mealTypes = ['BREAKFAST', 'LUNCH', 'DINNER'];
  DateTime _eatenAt = DateTime.now();

  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  /// 분석 요청을 보내는 중. 버튼을 막는다.
  bool _submitting = false;

  final TextEditingController _searchController = TextEditingController();

  final ImagePicker _imagePicker = ImagePicker();

  /// 고른 식사 사진. 분석 요청(`POST /meals`) 때 올린다.
  XFile? _photo;

  /// 미리보기용. 웹에서는 파일 경로로 그릴 수 없어서 바이트로 들고 있는다.
  Uint8List? _photoBytes;

  /// 사진 올리기 → 카메라/갤러리 선택 → 미리보기.
  ///
  /// 업로드 용량을 줄이려고 긴 변 1600px · 품질 85 로 줄여서 받는다.
  /// 웹에서 "사진 찍기"는 브라우저가 파일 선택/카메라 중 하나를 띄운다.
  Future<void> _pickPhoto() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.photo_camera_outlined),
              title: Text('사진 찍기', style: AppTypography.cardTitle),
              onTap: () => Navigator.of(context).pop(ImageSource.camera),
            ),
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: Text('갤러리에서 선택', style: AppTypography.cardTitle),
              onTap: () => Navigator.of(context).pop(ImageSource.gallery),
            ),
          ],
        ),
      ),
    );
    if (source == null) return;

    try {
      final photo = await _imagePicker.pickImage(
        source: source,
        maxWidth: 1600,
        maxHeight: 1600,
        imageQuality: 85,
      );
      if (photo == null) return; // 사용자가 취소
      final bytes = await photo.readAsBytes();
      if (!mounted) return;
      setState(() {
        _photo = photo;
        _photoBytes = bytes;
      });
    } catch (e) {
      // 권한 거부 · 카메라 없음 등.
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('사진을 불러오지 못했어요: $e')),
      );
    }
  }

  void _removePhoto() {
    setState(() {
      _photo = null;
      _photoBytes = null;
    });
  }

  /// 날짜 행 · 시간 행을 누르면 그 값만 바꾼다. 나머지는 지금 값을 그대로 둔다.
  Future<void> _changeEatenAt(_PickerMode mode) async {
    final picked = await showModalBottomSheet<DateTime>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.sheetRadius),
      builder: (context) => _DateTimeDialPicker(initial: _eatenAt, mode: mode),
    );
    if (picked == null) return;
    setState(() => _eatenAt = picked);
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  /// 분석 시작(`POST /meals`) → AI 분석 진행 화면(4번)으로 교체 이동.
  ///
  /// 교체(`pushReplacement`)라서 분석 이후 화면에서 뒤로 가면 입력 화면이 아니라
  /// 이 화면을 연 탭으로 바로 돌아간다.
  Future<void> _startAnalysis() async {
    final photo = _photoBytes;
    final text = _searchController.text.trim();
    final usePhoto = _selectedTab == 0;
    if (usePhoto ? photo == null : text.isEmpty) {
      _showError(usePhoto ? '사진을 골라 주세요' : '먹은 음식을 적어 주세요');
      return;
    }
    setState(() => _submitting = true);
    try {
      final mealType = _mealTypes[_selectedMealType];
      final created = usePhoto
          ? await _api.createWithPhoto(
              mealType: mealType,
              eatenAt: _eatenAt,
              photo: photo!,
              fileName: _photo?.name ?? 'meal.jpg',
            )
          : await _api.createWithText(
              mealType: mealType,
              eatenAt: _eatenAt,
              text: text,
            );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => MealAnalysisScreen(
            meal: created,
            photo: usePhoto ? photo : null,
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      _showError('분석을 시작하지 못했어요: $e');
      setState(() => _submitting = false);
    }
  }

  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

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
              // 입력 방식 탭
              Container(
                padding: const EdgeInsets.all(AppSpacing.xs),
                decoration: BoxDecoration(
                  color: AppColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(13),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: _SegmentTab(
                        label: '사진',
                        selected: _selectedTab == 0,
                        onTap: () => setState(() => _selectedTab = 0),
                      ),
                    ),
                    const SizedBox(width: AppSpacing.xs),
                    Expanded(
                      child: _SegmentTab(
                        label: '직접 입력',
                        selected: _selectedTab == 1,
                        onTap: () => setState(() => _selectedTab = 1),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: AppSpacing.lg),

              // 사진 / 직접 입력 — 탭에 따라 내용을 바꾼다.
              if (_selectedTab == 0)
                _buildPhotoUpload()
              else
                _buildSearchInput(),
              const SizedBox(height: AppSpacing.lg),

              // 끼니 선택
              Text('끼니', style: _sectionLabel),
              const SizedBox(height: AppSpacing.sm),
              Row(
                children: [
                  _MealTypeButton(
                    label: '아침',
                    selected: _selectedMealType == 0,
                    onTap: () => setState(() => _selectedMealType = 0),
                  ),
                  const SizedBox(width: 6),
                  _MealTypeButton(
                    label: '점심',
                    selected: _selectedMealType == 1,
                    onTap: () => setState(() => _selectedMealType = 1),
                  ),
                  const SizedBox(width: 6),
                  _MealTypeButton(
                    label: '저녁',
                    selected: _selectedMealType == 2,
                    onTap: () => setState(() => _selectedMealType = 2),
                  ),
                ],
              ),
              const SizedBox(height: AppSpacing.lg),

              // 식사 시각 — 날짜와 시간을 따로 고친다.
              Text('식사 시각', style: _sectionLabel),
              const SizedBox(height: AppSpacing.sm),
              _DateTimeRow(
                icon: Icons.calendar_today_outlined,
                iconSize: 14,
                label: '날짜',
                value: _formatDay(_eatenAt),
                onTap: () => _changeEatenAt(_PickerMode.date),
              ),
              const SizedBox(height: AppSpacing.sm),
              _DateTimeRow(
                icon: Icons.access_time,
                iconSize: 16,
                label: '시간',
                value: _formatTime(_eatenAt),
                onTap: () => _changeEatenAt(_PickerMode.time),
              ),
              const SizedBox(height: AppSpacing.lg),

              // 분석 버튼
              SizedBox(
                width: double.infinity,
                height: 56,
                child: ElevatedButton(
                  onPressed: _submitting ? null : _startAnalysis,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    elevation: 0,
                    shape: const RoundedRectangleBorder(
                      borderRadius: AppRadius.xlRadius,
                    ),
                  ),
                  child: _submitting
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: AppColors.textInverse,
                          ),
                        )
                      : Text('분석 시작', style: AppTypography.buttonLabel),
                ),
              ),
              const SizedBox(height: AppSpacing.xl),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPhotoUpload() {
    final bytes = _photoBytes;
    if (_photo != null && bytes != null) return _buildPhotoPreview(bytes);
    // 점선은 흰 면 위에 그려야 해서 foregroundPainter 로 얹는다.
    return CustomPaint(
      foregroundPainter: const _DashedBorderPainter(),
      child: Material(
        color: AppColors.surface,
        borderRadius: AppRadius.xlRadius,
        child: InkWell(
          onTap: _pickPhoto,
          borderRadius: AppRadius.xlRadius,
          child: Container(
            width: double.infinity,
            height: 180,
            alignment: Alignment.center,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 48,
                  height: 48,
                  decoration: const BoxDecoration(
                    color: AppColors.surfaceMuted,
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(
                    Icons.add,
                    size: 22,
                    color: AppColors.textSecondary,
                  ),
                ),
                const SizedBox(height: AppSpacing.sm),
                Text('사진 올리기', style: AppTypography.cardTitle),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  '갤러리에서 선택하거나 텍스트로 입력',
                  style: AppTypography.caption.copyWith(fontSize: 10.5),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// 고른 사진 미리보기. 사진을 탭하면 다시 고르고, ✕ 로 지운다.
  Widget _buildPhotoPreview(Uint8List bytes) {
    return ClipRRect(
      borderRadius: AppRadius.xlRadius,
      child: SizedBox(
        width: double.infinity,
        height: 180,
        child: Stack(
          fit: StackFit.expand,
          children: [
            InkWell(
              onTap: _pickPhoto,
              child: Image.memory(bytes, fit: BoxFit.cover),
            ),
            Positioned(
              top: AppSpacing.sm,
              right: AppSpacing.sm,
              child: Material(
                color: AppColors.overlay,
                shape: const CircleBorder(),
                child: IconButton(
                  onPressed: _removePhoto,
                  icon: const Icon(Icons.close, color: AppColors.textInverse),
                  tooltip: '사진 지우기',
                ),
              ),
            ),
          ],
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
                Icons.edit_outlined,
                size: AppLayout.tabIconSize,
                color: AppColors.inactive,
              ),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: TextField(
                  controller: _searchController,
                  style: AppTypography.body,
                  decoration: InputDecoration(
                    hintText: '먹은 음식을 적어 주세요 (예: 현미밥, 된장국)',
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
        const SizedBox(height: AppSpacing.sm),
        Text(
          'AI 가 음식과 양을 알아봐요. 다음 화면에서 고칠 수 있어요.',
          style: AppTypography.bodySecondary,
        ),
      ],
    );
  }
}

enum _PickerMode { date, time }

/// 날짜 행 · 시간 행이 여는 바텀시트. 달력/시계 대신 숫자 다이얼(휠)로 조정한다.
/// [mode] 가 날짜면 날짜 휠 하나, 시간이면 시 · 분 휠 둘만 보이고, 안 보이는 쪽은 지금 값을 그대로 둔다.
class _DateTimeDialPicker extends StatefulWidget {
  const _DateTimeDialPicker({required this.initial, required this.mode});

  final DateTime initial;
  final _PickerMode mode;

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

  Widget _wheel({
    required int initialItem,
    required ValueChanged<int> onChanged,
    required List<String> labels,
  }) {
    return CupertinoPicker(
      itemExtent: _itemExtent,
      scrollController: FixedExtentScrollController(initialItem: initialItem),
      onSelectedItemChanged: onChanged,
      children: labels
          .map((l) => Center(child: Text(l, style: AppTypography.body)))
          .toList(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDate = widget.mode == _PickerMode.date;
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
            Text(isDate ? '날짜 변경' : '시간 변경', style: AppTypography.sectionHead),
            const SizedBox(height: AppSpacing.md),
            SizedBox(
              height: 180,
              child: isDate
                  ? _wheel(
                      initialItem: _dateIndex,
                      onChanged: (i) => setState(() => _dateIndex = i),
                      labels: _dates.map(_formatDateShort).toList(),
                    )
                  : Row(
                      children: [
                        Expanded(
                          child: _wheel(
                            initialItem: _hour,
                            onChanged: (i) => setState(() => _hour = i),
                            labels: List.generate(24, _two),
                          ),
                        ),
                        Expanded(
                          child: _wheel(
                            initialItem: _minute,
                            onChanged: (i) => setState(() => _minute = i),
                            labels: List.generate(60, _two),
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

/// 식사 시각 한 줄(날짜 · 시간). 줄 전체가 눌리고, 오른쪽 화살표는 "눌러서 고친다"는 표시다.
class _DateTimeRow extends StatelessWidget {
  const _DateTimeRow({
    required this.icon,
    required this.iconSize,
    required this.label,
    required this.value,
    required this.onTap,
  });

  final IconData icon;
  final double iconSize;
  final String label;
  final String value;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final style = AppTypography.body.copyWith(
      fontSize: 13,
      color: AppColors.textPrimary,
    );
    final shape = RoundedRectangleBorder(borderRadius: BorderRadius.circular(13));
    return Material(
      color: AppColors.surface,
      shape: shape.copyWith(side: const BorderSide(color: AppColors.border)),
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: SizedBox(
          height: 48,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: Row(
              children: [
                Icon(icon, size: iconSize, color: AppColors.primary),
                const SizedBox(width: 5),
                Text(label, style: style),
                const Spacer(),
                Text(value, style: style),
                const SizedBox(width: 6),
                const Icon(
                  Icons.keyboard_arrow_down,
                  size: 18,
                  color: AppColors.textTertiary,
                ),
              ],
            ),
          ),
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
    final radius = BorderRadius.circular(10);
    return InkWell(
      onTap: onTap,
      borderRadius: radius,
      child: Container(
        height: 34,
        decoration: BoxDecoration(
          color: selected ? AppColors.surface : Colors.transparent,
          borderRadius: radius,
          boxShadow: selected
              ? [
                  BoxShadow(
                    color: AppColors.textPrimary.withValues(alpha: 0.12),
                    blurRadius: 3,
                    offset: const Offset(0, 1),
                  ),
                ]
              : null,
        ),
        alignment: Alignment.center,
        child: Text(
          label,
          style: AppTypography.body.copyWith(
            fontSize: 11.5,
            height: 1.25,
            fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
            color: selected ? AppColors.textPrimary : AppColors.textSecondary,
          ),
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
    final radius = BorderRadius.circular(11);
    return Expanded(
      child: Material(
        color: selected ? AppColors.primaryTint : AppColors.surface,
        shape: RoundedRectangleBorder(
          borderRadius: radius,
          side: BorderSide(
            color: selected ? AppColors.primary : AppColors.border,
            width: selected ? 1.5 : 1,
          ),
        ),
        child: InkWell(
          onTap: onTap,
          borderRadius: radius,
          child: Container(
            height: 38,
            alignment: Alignment.center,
            child: Text(
              label,
              style: AppTypography.body.copyWith(
                fontSize: 10,
                height: 1.25,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
                color: selected ? AppColors.primary : AppColors.textSecondary,
              ),
            ),
          ),
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
      ..color = AppColors.border
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;

    final path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(
          Offset.zero & size,
          const Radius.circular(AppRadius.xl),
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
