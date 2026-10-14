import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

/// 양을 화면에 적을 때 정수면 소수점을 뗀다(`1.0` → `1`).
String _formatAmount(double v) =>
    v == v.roundToDouble() ? '${v.toInt()}' : v.toString();

/// "끼니" · "식사 시각" 같은 섹션 제목.
final TextStyle _sectionLabel = AppTypography.sectionHead.copyWith(fontSize: 12.5);

/// 직접 입력 폼의 입력창 글씨. 입력값과 단위 선택이 같은 크기를 쓴다.
final TextStyle _fieldText = AppTypography.body.copyWith(
  fontSize: 12.5,
  height: 1.25,
  color: AppColors.textPrimary,
);

/// 직접 입력 탭에서 고를 수 있는 단위.
const List<String> _units = ['g', '개', '인분'];

/// "목록에 추가"로 쌓은 음식 한 줄. 서버로 보내기 전까지 화면 안에서만 들고 있는다.
class _FoodEntry {
  const _FoodEntry({
    required this.name,
    required this.amount,
    required this.unit,
  });

  final String name;
  final double amount;
  final String unit;
}

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

  // 직접 입력 탭: 폼 입력값과 "목록에 추가"로 쌓은 음식.
  final TextEditingController _nameController = TextEditingController();
  final TextEditingController _amountController = TextEditingController();
  final FocusNode _nameFocus = FocusNode();
  String? _unit;
  final List<_FoodEntry> _foods = [];

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
    _nameController.dispose();
    _amountController.dispose();
    _nameFocus.dispose();
    super.dispose();
  }

  /// "목록에 추가" — 입력한 음식을 아래 목록에 쌓고 이름·양 칸을 비운다.
  /// 단위는 이어서 같은 단위로 적는 일이 많아 그대로 둔다.
  void _addFood() {
    final name = _nameController.text.trim();
    final amount = double.tryParse(_amountController.text.trim());
    final unit = _unit;
    if (name.isEmpty) {
      _showError('음식 이름을 적어 주세요');
      return;
    }
    if (amount == null || amount <= 0) {
      _showError('양을 0보다 큰 숫자로 적어 주세요');
      return;
    }
    if (unit == null) {
      _showError('단위를 골라 주세요');
      return;
    }
    setState(() => _foods.add(_FoodEntry(name: name, amount: amount, unit: unit)));
    _nameController.clear();
    _amountController.clear();
    _nameFocus.requestFocus();
  }

  void _removeFood(int index) => setState(() => _foods.removeAt(index));

  void _clearFoods() => setState(_foods.clear);

  /// 분석 시작(`POST /meals`) → AI 분석 진행 화면(4번)으로 교체 이동.
  ///
  /// 교체(`pushReplacement`)라서 분석 이후 화면에서 뒤로 가면 입력 화면이 아니라
  /// 이 화면을 연 탭으로 바로 돌아간다.
  Future<void> _startAnalysis() async {
    if (_selectedTab == 1) {
      _startManualAnalysis();
      return;
    }
    final photo = _photoBytes;
    if (photo == null) {
      _showError('사진을 골라 주세요');
      return;
    }
    setState(() => _submitting = true);
    try {
      final created = await _api.createWithPhoto(
        mealType: _mealTypes[_selectedMealType],
        eatenAt: _eatenAt,
        photo: photo,
        fileName: _photo?.name ?? 'meal.jpg',
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => MealAnalysisScreen(meal: created, photo: photo),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      _showError('분석을 시작하지 못했어요: $e');
      setState(() => _submitting = false);
    }
  }

  /// 직접 입력 탭의 "분석 시작".
  ///
  /// 화면만 먼저 만든 단계라 서버로 보내지 않는다. 음식 목록을 서버에 넘기는
  /// 방식(기존 텍스트 입력으로 이어 붙이기 / `POST /meals` 가 `items[]` 를 받기)이
  /// 정해지면 여기서 [_foods] 를 보낸다.
  void _startManualAnalysis() {
    if (_foods.isEmpty) {
      _showError('먹은 음식을 추가해 주세요');
      return;
    }
    _showError('직접 입력 분석은 아직 연결되지 않았어요');
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
                _buildManualInput(),
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

  /// "직접 입력" 탭: 음식 이름 · 양 · 단위를 적어 목록에 쌓는다.
  /// 목록이 비어 있으면 입력 패널만 보이고, 하나라도 쌓이면 그 아래에 목록이 붙는다.
  Widget _buildManualInput() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _buildAddPanel(),
        if (_foods.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.lg),
          _buildFoodList(),
        ],
      ],
    );
  }

  Widget _buildAddPanel() {
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: AppColors.primaryTint.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ClipOval(
                child: Image.asset(
                  'assets/images/meal_input_food_add.png',
                  width: 39,
                  height: 39,
                  fit: BoxFit.cover,
                ),
              ),
              const SizedBox(width: 10),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '음식 추가',
                    style: AppTypography.cardTitle.copyWith(fontSize: 14),
                  ),
                  const SizedBox(height: 5),
                  Text(
                    '먹은 음식을 추가해 주세요.',
                    style: AppTypography.caption.copyWith(
                      fontSize: 10.5,
                      color: AppColors.textSecondary,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 15),
          _FieldLabel('음식 이름'),
          const SizedBox(height: 5),
          _ClearableField(
            controller: _nameController,
            focusNode: _nameFocus,
            hint: '예) 현미밥, 닭가슴살, 김치',
            height: 35,
            textInputAction: TextInputAction.next,
          ),
          const SizedBox(height: 15),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _FieldLabel('양'),
                    const SizedBox(height: 5),
                    _ClearableField(
                      controller: _amountController,
                      height: 40,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      inputFormatters: [
                        FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 25),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _FieldLabel('단위 (g · 개 · 인분)'),
                    const SizedBox(height: 5),
                    _UnitDropdown(
                      value: _unit,
                      onChanged: (v) => setState(() => _unit = v),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 15),
          _AddToListButton(onTap: _addFood),
        ],
      ),
    );
  }

  Widget _buildFoodList() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 30,
          child: Row(
            children: [
              Text('추가한 음식', style: AppTypography.cardTitle),
              const SizedBox(width: 6),
              _CountBadge(count: _foods.length),
              const Spacer(),
              _ClearAllButton(onTap: _clearFoods),
            ],
          ),
        ),
        const SizedBox(height: 4),
        for (var i = 0; i < _foods.length; i++) ...[
          if (i > 0) const SizedBox(height: 7),
          _FoodCard(entry: _foods[i], onRemove: () => _removeFood(i)),
        ],
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

/// 직접 입력 폼의 입력창 테두리 상자(흰 바탕 + 회색 테두리).
BoxDecoration _fieldBox() => BoxDecoration(
  color: AppColors.surface,
  border: Border.all(color: AppColors.border),
  borderRadius: BorderRadius.circular(10),
);

/// 입력창 위의 라벨("음식 이름" · "양" · "단위").
class _FieldLabel extends StatelessWidget {
  const _FieldLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: AppTypography.body.copyWith(
        fontSize: 13,
        height: 1.25,
        color: AppColors.textPrimary,
      ),
    );
  }
}

/// 글자가 있으면 오른쪽에 지우기(✕) 버튼이 뜨는 입력창.
class _ClearableField extends StatelessWidget {
  const _ClearableField({
    required this.controller,
    required this.height,
    this.focusNode,
    this.hint,
    this.keyboardType,
    this.inputFormatters,
    this.textInputAction,
  });

  final TextEditingController controller;
  final double height;
  final FocusNode? focusNode;
  final String? hint;
  final TextInputType? keyboardType;
  final List<TextInputFormatter>? inputFormatters;
  final TextInputAction? textInputAction;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      padding: const EdgeInsets.only(left: 7, right: 11),
      decoration: _fieldBox(),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              focusNode: focusNode,
              keyboardType: keyboardType,
              inputFormatters: inputFormatters,
              textInputAction: textInputAction,
              style: _fieldText,
              decoration: InputDecoration(
                hintText: hint,
                hintStyle: _fieldText.copyWith(color: AppColors.textTertiary),
                filled: false,
                border: InputBorder.none,
                enabledBorder: InputBorder.none,
                focusedBorder: InputBorder.none,
                isDense: true,
                contentPadding: EdgeInsets.zero,
              ),
            ),
          ),
          ValueListenableBuilder<TextEditingValue>(
            valueListenable: controller,
            builder: (context, value, _) {
              if (value.text.isEmpty) return const SizedBox.shrink();
              return GestureDetector(
                onTap: controller.clear,
                child: const Icon(
                  Icons.cancel,
                  size: 20,
                  color: AppColors.textTertiary,
                ),
              );
            },
          ),
        ],
      ),
    );
  }
}

/// 단위(g · 개 · 인분) 고르기. 아직 안 골랐으면 "단위 선택"이 보인다.
class _UnitDropdown extends StatelessWidget {
  const _UnitDropdown({required this.value, required this.onChanged});

  final String? value;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 40,
      padding: const EdgeInsets.only(left: 7, right: 7),
      decoration: _fieldBox(),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: value,
          isExpanded: true,
          hint: Text(
            '단위 선택',
            style: _fieldText.copyWith(color: AppColors.textTertiary),
          ),
          icon: const Icon(
            Icons.keyboard_arrow_down,
            size: 20,
            color: AppColors.textTertiary,
          ),
          style: _fieldText,
          dropdownColor: AppColors.surface,
          borderRadius: BorderRadius.circular(10),
          items: [
            for (final unit in _units)
              DropdownMenuItem(value: unit, child: Text(unit)),
          ],
          onChanged: onChanged,
        ),
      ),
    );
  }
}

/// "목록에 추가" 버튼(빨강2 윤곽 + 연한 배경).
class _AddToListButton extends StatelessWidget {
  const _AddToListButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final shape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(10),
      side: const BorderSide(color: AppColors.primaryStrong),
    );
    return Material(
      color: AppColors.primaryStrongBg,
      shape: shape,
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: SizedBox(
          height: 37,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.add, size: 16, color: AppColors.primaryStrong),
              const SizedBox(width: 5),
              Text(
                '목록에 추가',
                style: AppTypography.buttonLabel.copyWith(
                  color: AppColors.primaryStrong,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// "추가한 음식" 옆의 개수 배지.
class _CountBadge extends StatelessWidget {
  const _CountBadge({required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 14,
      height: 14,
      alignment: Alignment.center,
      decoration: const BoxDecoration(
        color: AppColors.primaryStrongBg,
        shape: BoxShape.circle,
      ),
      child: Text(
        '$count',
        style: AppTypography.emphasis.copyWith(
          fontSize: 9,
          height: 1,
          color: AppColors.primaryStrong,
        ),
      ),
    );
  }
}

/// "전체 삭제" 버튼.
class _ClearAllButton extends StatelessWidget {
  const _ClearAllButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final shape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(5),
      side: const BorderSide(color: AppColors.border),
    );
    return Material(
      color: AppColors.surface,
      shape: shape,
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: SizedBox(
          width: 93,
          height: 30,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(
                Icons.delete_outline,
                size: 16,
                color: AppColors.primary,
              ),
              const SizedBox(width: 8),
              Text('전체 삭제', style: _fieldText),
            ],
          ),
        ),
      ),
    );
  }
}

/// 목록의 음식 한 줄 — 이름과 "양+단위", 오른쪽 ✕ 로 지운다.
class _FoodCard extends StatelessWidget {
  const _FoodCard({required this.entry, required this.onRemove});

  final _FoodEntry entry;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: AppRadius.lgRadius,
      ),
      child: Row(
        children: [
          Expanded(
            child: Row(
              children: [
                Flexible(
                  child: Text(
                    entry.name,
                    style: AppTypography.cardTitle,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 3),
                Text(
                  '${_formatAmount(entry.amount)}${entry.unit}',
                  style: AppTypography.caption.copyWith(fontSize: 9.5),
                ),
              ],
            ),
          ),
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: onRemove,
            child: const Padding(
              padding: EdgeInsets.symmetric(horizontal: 4),
              child: Icon(Icons.close, size: 14, color: AppColors.textTertiary),
            ),
          ),
        ],
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
