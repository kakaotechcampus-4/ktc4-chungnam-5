import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../screens/home_screen.dart' show mealTypeLabel;
import '../screens/shared_meal_widgets.dart' show SkeletonBox;
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'popup_widgets.dart';

// ── 모델 ────────────────────────────────────────────────────

/// 다시 배고파진 시점. 서버 `satiety_logs.hunger_return_minutes` 는
/// "식사 후 몇 분 뒤 배고파졌나"라서, [minutesAgo] 를 식사 후 경과 시간에서
/// 빼서 보낸다. "아직 안 고파요"는 null.
enum HungerReturn {
  notYet('아직 안 고파요', null),
  hourAgo('1시간 전', 60),
  halfHourAgo('30분 전', 30),
  justNow('방금', 0);

  const HungerReturn(this.label, this.minutesAgo);

  final String label;
  final int? minutesAgo;

  /// 식사 후 [elapsedMinutes] 가 지난 지금 기준 `hungerReturnMinutes`.
  int? minutesAfterMeal(int elapsedMinutes) {
    final ago = minutesAgo;
    if (ago == null) return null;
    return (elapsedMinutes - ago).clamp(0, elapsedMinutes);
  }
}

/// 체크인 대상 끼니. `GET /meals/{mealId}` 중 팝업에 필요한 부분.
class CheckinMeal {
  const CheckinMeal({
    required this.mealId,
    required this.mealType,
    required this.eatenAt,
    required this.displayName,
  });

  final String mealId;
  final String mealType;

  /// 먹은 시각(절대 시각). 경과 시간 계산에 쓴다. 화면에 시각을 적을 때는
  /// [eatenAtKst] 를 쓴다.
  final DateTime eatenAt;
  final String displayName;

  /// 명세상 시각은 모두 +09:00 이라 그 벽시계 값을 그대로 보여 준다.
  /// `home_screen.dart` 의 `parseApiDateTime` 과 같은 이유로 `toLocal()` 을
  /// 쓰지 않는다.
  DateTime get eatenAtKst => eatenAt.toUtc().add(const Duration(hours: 9));

  factory CheckinMeal.fromJson(Map<String, dynamic> json) => CheckinMeal(
    mealId: json['mealId'] as String,
    mealType: json['mealType'] as String,
    eatenAt: DateTime.parse(json['eatenAt'] as String),
    displayName: json['displayName'] as String,
  );
}

// ── API 서비스 ─────────────────────────────────────────────

class SatietyCheckinApiService {
  /// `GET /meals/{mealId}`
  Future<CheckinMeal> fetchMeal(String mealId) async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    await Future.delayed(const Duration(milliseconds: 300));
    // 더미는 "3시간 전에 먹은 끼니"로 만든다. 고정 날짜면 경과 시간이 수백
    // 시간으로 찍힌다.
    final eatenAt = DateTime.now().subtract(const Duration(hours: 3));
    return CheckinMeal.fromJson({
      ..._sample,
      'mealId': mealId,
      'eatenAt': eatenAt.toIso8601String(),
    });
  }

  /// 사후 포만감 저장.
  Future<void> save({
    required String mealId,
    required int satietyAfterPct,
    required int? hungerReturnMinutes,
  }) async {
    // TODO(백엔드 API 생기면): satiety_logs(satiety_after · hunger_return_minutes)
    //   저장 API 로 교체. 경로 미정.
    await Future.delayed(const Duration(milliseconds: 300));
  }

  static const Map<String, dynamic> _sample = {
    'mealId': 'meal_456',
    'mealType': 'LUNCH',
    'displayName': '현미밥, 된장국, 두부조림',
  };
}

// ── 팝업 ────────────────────────────────────────────────────

/// 사후 포만감 체크인 팝업을 띄운다. 저장했으면 `true`, 아니면 `false`.
///
/// **진입점은 [mealId] 하나뿐이다.** 홈 게이지 탭에서도, 나중에 알림을
/// 눌렀을 때(`NotificationRouter`, 알림 티켓에서 구현)도 이 함수만 부른다.
/// 멘토 리뷰(PR #9) "알림 진입 화면은 id 로 생성" 원칙 — 끼니 정보는 팝업이
/// 스스로 불러온다.
Future<bool> showSatietyCheckinPopup(
  BuildContext context, {
  required String mealId,
}) async {
  final saved = await showDialog<bool>(
    context: context,
    barrierColor: AppColors.overlay,
    builder: (_) => SatietyCheckinPopup(mealId: mealId),
  );
  return saved ?? false;
}

/// 사후 포만감 체크인 — Figma `hOxrHBitBpjwIBBg2GO49y` node `100:6` (10).
///
/// Figma 의 `satietyPct` · `hungerReturnMinutes` 라벨은 API 필드 주석이라
/// 화면에 그리지 않는다. 빈 "메모 (선택)" 자리도 아직 내용이 없어 뺐다.
class SatietyCheckinPopup extends StatefulWidget {
  const SatietyCheckinPopup({super.key, required this.mealId});

  final String mealId;

  @override
  State<SatietyCheckinPopup> createState() => _SatietyCheckinPopupState();
}

class _SatietyCheckinPopupState extends State<SatietyCheckinPopup> {
  static const _defaultSatiety = 50;

  final SatietyCheckinApiService _api = SatietyCheckinApiService();
  final TextEditingController _satietyController = TextEditingController(
    text: '$_defaultSatiety',
  );

  CheckinMeal? _meal;
  bool _mealFailed = false;

  int _satiety = _defaultSatiety;
  HungerReturn? _hungerReturn;

  bool _saving = false;
  String? _saveError;

  @override
  void initState() {
    super.initState();
    _loadMeal();
  }

  @override
  void dispose() {
    _satietyController.dispose();
    super.dispose();
  }

  Future<void> _loadMeal() async {
    if (_mealFailed) setState(() => _mealFailed = false);
    try {
      final meal = await _api.fetchMeal(widget.mealId);
      if (!mounted) return;
      setState(() => _meal = meal);
    } catch (_) {
      if (!mounted) return;
      setState(() => _mealFailed = true);
    }
  }

  int get _elapsedMinutes {
    final meal = _meal;
    if (meal == null) return 0;
    final minutes = DateTime.now().difference(meal.eatenAt).inMinutes;
    return minutes < 0 ? 0 : minutes;
  }

  /// 슬라이더 → 숫자 칸.
  void _onSliderChanged(double value) {
    final v = value.round();
    setState(() => _satiety = v);
    _satietyController.text = '$v';
  }

  /// 숫자 칸 → 슬라이더. 0~100 밖이면 끝값으로 맞춘다.
  void _onFieldChanged(String text) {
    final parsed = int.tryParse(text);
    if (parsed == null) return;
    final v = parsed.clamp(0, 100);
    setState(() => _satiety = v);
    if (v != parsed) {
      _satietyController.value = TextEditingValue(
        text: '$v',
        selection: TextSelection.collapsed(offset: '$v'.length),
      );
    }
  }

  /// 끼니 정보를 불러왔고 배고파진 시점을 골라야 저장할 수 있다.
  bool get _canSave => _meal != null && _hungerReturn != null;

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _saveError = null;
    });
    try {
      await _api.save(
        mealId: widget.mealId,
        satietyAfterPct: _satiety,
        hungerReturnMinutes: _hungerReturn!.minutesAfterMeal(_elapsedMinutes),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saveError = '저장하지 못했어요: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _later() => Navigator.of(context).pop(false);

  @override
  Widget build(BuildContext context) {
    return PopupDialog(
      children: [
        PopupHeader(title: '지금 얼마나 부르세요?', onClose: _later),
        const SizedBox(height: AppSpacing.lg),
        _buildTargetMeal(),
        const SizedBox(height: AppSpacing.lg),
        _buildSatiety(),
        const SizedBox(height: AppSpacing.lg),
        _buildHungerReturn(),
        if (_saveError != null) ...[
          const SizedBox(height: AppSpacing.md),
          PopupErrorText(message: _saveError!),
        ],
        const SizedBox(height: AppSpacing.xl),
        PopupActions(
          onLater: _later,
          onSave: _canSave ? _save : null,
          saving: _saving,
        ),
      ],
    );
  }

  Widget _buildTargetMeal() {
    final meal = _meal;
    final Widget info;
    if (meal != null) {
      info = Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${mealTypeLabel(meal.mealType)} · ${_formatClock(meal.eatenAtKst)} · '
            '${_formatElapsed(_elapsedMinutes)} 경과',
            style: AppTypography.caption.copyWith(
              color: AppColors.textTertiary,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            meal.displayName,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: AppTypography.emphasis,
          ),
        ],
      );
    } else if (_mealFailed) {
      info = Row(
        children: [
          Expanded(
            child: Text('식사 정보를 불러오지 못했어요', style: AppTypography.bodySecondary),
          ),
          TextButton(onPressed: _loadMeal, child: const Text('다시 시도')),
        ],
      );
    } else {
      info = const Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SkeletonBox(width: 120, height: 10),
          SizedBox(height: AppSpacing.xs),
          SkeletonBox(width: 160, height: 14),
        ],
      );
    }

    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: AppRadius.lgRadius,
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        children: [
          // 끼니 썸네일 자리. 사진 URL 이 생기면 이미지로 바꾼다.
          Container(
            width: 40,
            height: 40,
            decoration: const BoxDecoration(
              color: AppColors.surfaceSage,
              borderRadius: AppRadius.mdRadius,
            ),
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(child: info),
        ],
      ),
    );
  }

  Widget _buildSatiety() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const PopupSectionLabel(title: '지금 포만감'),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            Expanded(
              child: Slider(
                value: _satiety.toDouble(),
                min: 0,
                max: 100,
                onChanged: _onSliderChanged,
                activeColor: AppColors.primary,
                inactiveColor: AppColors.surfaceMuted,
                semanticFormatterCallback: (v) => '포만감 ${v.round()}%',
              ),
            ),
            const SizedBox(width: AppSpacing.md),
            Container(
              width: 74,
              height: 32,
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.sm),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: AppRadius.smRadius,
                border: Border.all(color: AppColors.border),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _satietyController,
                      onChanged: _onFieldChanged,
                      keyboardType: TextInputType.number,
                      inputFormatters: [
                        FilteringTextInputFormatter.digitsOnly,
                        LengthLimitingTextInputFormatter(3),
                      ],
                      textAlign: TextAlign.right,
                      style: AppTypography.emphasis.copyWith(fontSize: 14),
                      decoration: const InputDecoration(
                        isDense: true,
                        border: InputBorder.none,
                        enabledBorder: InputBorder.none,
                        focusedBorder: InputBorder.none,
                        filled: false,
                        contentPadding: EdgeInsets.zero,
                      ),
                    ),
                  ),
                  const SizedBox(width: 2),
                  Text(
                    '%',
                    style: AppTypography.caption.copyWith(
                      color: AppColors.textTertiary,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildHungerReturn() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const PopupSectionLabel(title: '다시 배고파진 시점'),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            for (final h in HungerReturn.values) ...[
              if (h != HungerReturn.values.first)
                const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: PopupChoiceTile(
                  label: h.label,
                  height: 36,
                  selected: _hungerReturn == h,
                  onTap: () => setState(() => _hungerReturn = h),
                ),
              ),
            ],
          ],
        ),
      ],
    );
  }
}

String _formatClock(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

/// `180` → `3시간`, `200` → `3시간 20분`, `40` → `40분`.
String _formatElapsed(int minutes) {
  final h = minutes ~/ 60;
  final m = minutes % 60;
  if (h == 0) return '$m분';
  if (m == 0) return '$h시간';
  return '$h시간 $m분';
}
