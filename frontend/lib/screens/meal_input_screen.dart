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
import 'food_search_screen.dart';
import 'meal_analysis_screen.dart';
import 'meal_review_screen.dart';

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

/// 검색 탭에서 고른 음식 하나와 먹은 양(g). 서버로 보내기 전까지 화면 안에서만 들고 있는다.
class _PickedFood {
  _PickedFood(this.candidate)
    : amount = TextEditingController(
        text: _formatAmount(candidate.servingSizeG ?? _defaultAmountG),
      );

  /// 기준량을 모르는 음식의 처음 양.
  static const double _defaultAmountG = 100;

  final FoodCandidate candidate;

  /// 먹은 양(g). 카드의 숫자 칸이 고친다.
  final TextEditingController amount;

  double? get amountG {
    final v = double.tryParse(amount.text.trim());
    return v == null || v <= 0 ? null : v;
  }

  /// 영양 한 줄을 먹은 양으로 환산할 비율. 기준량을 모르면 기준량 값 그대로다.
  double get scale {
    final serving = candidate.servingSizeG;
    final g = amountG;
    if (serving == null || serving <= 0 || g == null) return 1;
    return g / serving;
  }
}

class MealInputScreen extends StatefulWidget {
  const MealInputScreen({super.key});

  @override
  State<MealInputScreen> createState() => _MealInputScreenState();
}

class _MealInputScreenState extends State<MealInputScreen> {
  int _selectedTab = 0; // 0: 사진, 1: 검색
  int _selectedMealType = 2; // 0: 아침, 1: 점심, 2: 저녁

  /// [_selectedMealType] 순서의 서버 값.
  static const _mealTypes = ['BREAKFAST', 'LUNCH', 'DINNER'];
  DateTime _eatenAt = DateTime.now();

  late final MealApiService _api = MealApiService(context.read<ApiClient>());

  /// 분석 요청을 보내는 중. 버튼을 막는다.
  bool _submitting = false;

  /// 검색 탭에서 고른 음식(고른 순서).
  final List<_PickedFood> _foods = [];

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
    for (final f in _foods) {
      f.amount.dispose();
    }
    super.dispose();
  }

  /// 검색 화면을 열어 고른 음식을 목록에 더한다. 이미 있는 음식은 건너뛴다.
  Future<void> _openSearch() async {
    final picked = await Navigator.of(context).push<List<FoodCandidate>>(
      MaterialPageRoute(builder: (_) => FoodSearchScreen(api: _api)),
    );
    if (picked == null || picked.isEmpty || !mounted) return;
    final have = {for (final f in _foods) f.candidate.foodRefId};
    setState(() {
      for (final c in picked) {
        if (have.add(c.foodRefId)) _foods.add(_PickedFood(c));
      }
    });
  }

  void _removeFood(_PickedFood food) {
    setState(() => _foods.remove(food));
    food.amount.dispose();
  }

  /// 등록(`POST /meals`) 뒤 화면으로 교체 이동한다.
  ///
  /// - 사진: AI 분석 진행 화면(4번)
  /// - 검색에서 고른 음식: 분석이 없어 곧바로 음식 확인 화면(5번)
  ///
  /// 교체(`pushReplacement`)라서 뒤로 가면 입력 화면이 아니라 이 화면을 연 탭으로
  /// 바로 돌아간다.
  Future<void> _startAnalysis() async {
    final manual = _selectedTab == 1;
    final photo = manual ? null : _photoBytes;
    if (manual && _foods.isEmpty) {
      _showError('먹은 음식을 검색해 추가해 주세요');
      return;
    }
    if (manual && _foods.any((f) => f.amountG == null)) {
      _showError('양을 0보다 큰 숫자로 적어 주세요');
      return;
    }
    if (!manual && photo == null) {
      _showError('사진을 골라 주세요');
      return;
    }
    setState(() => _submitting = true);
    try {
      final mealType = _mealTypes[_selectedMealType];
      final created = photo == null
          ? await _api.createWithFoods(
              mealType: mealType,
              eatenAt: _eatenAt,
              foods: [
                for (final f in _foods)
                  (foodRefId: f.candidate.foodRefId, amountG: f.amountG!),
              ],
            )
          : await _api.createWithPhoto(
              mealType: mealType,
              eatenAt: _eatenAt,
              photo: photo,
              fileName: _photo?.name ?? 'meal.jpg',
            );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => created.needsAnalysis
              ? MealAnalysisScreen(meal: created, photo: photo)
              : MealReviewScreen(mealId: created.mealId),
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
                        label: '검색',
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

  /// "검색" 탭 — Figma `17:488`(비었을 때) · `17:527`(고른 뒤). 검색창을 누르면
  /// 공공 DB 검색 화면이 열리고, 고른 음식마다 먹은 양(g)을 정한다.
  Widget _buildManualInput() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FoodSearchBar(onTap: _openSearch),
        const SizedBox(height: AppSpacing.sm),
        if (_foods.isEmpty)
          _EmptySearch(onTap: _openSearch)
        else
          for (final food in _foods) ...[
            _PickedFoodCard(
              key: ObjectKey(food),
              food: food,
              onChanged: () => setState(() {}),
              onRemove: () => _removeFood(food),
            ),
            const SizedBox(height: AppSpacing.sm),
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

/// 아직 고른 음식이 없을 때 — Figma `17:488`.
class _EmptySearch extends StatelessWidget {
  const _EmptySearch({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: AppSpacing.xl),
        child: Column(
          children: [
            ClipOval(
              child: Image.asset(
                'assets/images/meal_input_food_add.png',
                width: 72,
                height: 72,
                fit: BoxFit.cover,
              ),
            ),
            const SizedBox(height: AppSpacing.md),
            Text('음식명을 검색하여 추가해 보세요.', style: AppTypography.bodySecondary),
          ],
        ),
      ),
    );
  }
}

/// 고른 음식 카드 — 이름 · 먹은 양 기준 영양 한 줄 · ✕, 아래에 `− 250 g ＋`.
/// 숫자 칸은 직접 고칠 수도 있다.
class _PickedFoodCard extends StatelessWidget {
  const _PickedFoodCard({
    super.key,
    required this.food,
    required this.onChanged,
    required this.onRemove,
  });

  /// − · ＋ 한 번에 바뀌는 양(g).
  static const double _step = 10;

  final _PickedFood food;
  final VoidCallback onChanged;
  final VoidCallback onRemove;

  void _bump(double delta) {
    final next = (food.amountG ?? 0) + delta;
    if (next <= 0) return;
    food.amount.text = _formatAmount(next);
    onChanged();
  }

  @override
  Widget build(BuildContext context) {
    final line = formatNutritionLine(
      food.candidate.nutrition,
      scale: food.scale,
    );
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 8, 10),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: AppRadius.lgRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      food.candidate.displayName,
                      style: AppTypography.cardTitle.copyWith(fontSize: 15),
                    ),
                    if (line.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        line,
                        style: AppTypography.caption.copyWith(
                          color: AppColors.textSecondary,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              IconButton(
                onPressed: onRemove,
                tooltip: '빼기',
                visualDensity: VisualDensity.compact,
                icon: const Icon(
                  Icons.close,
                  size: 18,
                  color: AppColors.textTertiary,
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.xs),
          Row(
            children: [
              _StepButton(
                icon: Icons.remove,
                tooltip: '양 줄이기',
                onTap: () => _bump(-_step),
              ),
              SizedBox(
                width: 56,
                child: TextField(
                  controller: food.amount,
                  onChanged: (_) => onChanged(),
                  textAlign: TextAlign.center,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  inputFormatters: [
                    FilteringTextInputFormatter.allow(RegExp(r'[0-9.]')),
                  ],
                  style: AppTypography.cardTitle.copyWith(fontSize: 16),
                  decoration: const InputDecoration(
                    filled: false,
                    border: InputBorder.none,
                    enabledBorder: InputBorder.none,
                    focusedBorder: InputBorder.none,
                    isDense: true,
                    contentPadding: EdgeInsets.zero,
                  ),
                ),
              ),
              Text('g', style: AppTypography.caption),
              const SizedBox(width: AppSpacing.sm),
              _StepButton(
                icon: Icons.add,
                tooltip: '양 늘리기',
                onTap: () => _bump(_step),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 양 조절 − · ＋ 동그라미 버튼.
class _StepButton extends StatelessWidget {
  const _StepButton({
    required this.icon,
    required this.tooltip,
    required this.onTap,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: AppColors.surfaceMuted,
        shape: const CircleBorder(),
        child: InkWell(
          onTap: onTap,
          customBorder: const CircleBorder(),
          child: SizedBox(
            width: 30,
            height: 30,
            child: Icon(icon, size: 16, color: AppColors.textPrimary),
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
