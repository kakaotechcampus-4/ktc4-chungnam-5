import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/medication_api.dart';
import '../common/api_format.dart';
import '../state/medication_state.dart';
import '../state/profile_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'popup_widgets.dart';

// ── 모델 ────────────────────────────────────────────────────

/// 식욕 5단계. 서버 `appetiteLevel` 에 [level] 로 보낸다(서버도 없음=1 ~ 매우 강함=5).
enum Appetite {
  none('없음', 1),
  weak('약함', 2),
  normal('보통', 3),
  strong('강함', 4),
  veryStrong('매우 강함', 5);

  const Appetite(this.label, this.level);

  final String label;
  final int level;
}

/// GI 증상. "없음"은 선택지로만 보이고 서버에는 빈 목록으로 보낸다.
///
/// 목록·순서는 BE `GiSymptomCode`(`backend/app/schemas/user_state.py`) 7종과
/// 같다. Figma 는 6칸(트림·가스, 구토 없음)이라 BE 에 맞춰 바꿨다.
enum GiSymptom {
  nausea('메스꺼움', 'NAUSEA'),
  vomiting('구토', 'VOMITING'),
  heartburn('속 쓰림', 'HEARTBURN'),
  constipation('변비', 'CONSTIPATION'),
  diarrhea('설사', 'DIARRHEA'),
  bloating('더부룩함·가스', 'BLOATING'),
  abdominalPain('복통', 'ABDOMINAL_PAIN');

  const GiSymptom(this.label, this.code);

  final String label;
  final String code;
}

/// 증상 강도. Figma 는 강도 선택이 하나라 고른 증상 전체에 같은 값이 붙는다.
enum SymptomSeverity {
  mild('약함', 'MILD'),
  moderate('보통', 'MODERATE'),
  severe('심함', 'SEVERE');

  const SymptomSeverity(this.label, this.code);

  final String label;
  final String code;
}

/// 팝업 헤더·체중 초기값. [MedicationState] + `GET /user-states/latest`
/// (+ 컨디션 기록이 없으면 [ProfileState] 의 체중) 조합.
///
/// 투약 미등록이면 투약 필드 넷이 모두 null 이고 헤더는 날짜만 보인다.
class ConditionPrefill {
  const ConditionPrefill({
    this.drugName,
    this.doseMg,
    this.doseCount,
    this.stage,
    required this.weightKg,
    required this.weeklyWeightDeltaKg,
  });

  final String? drugName;
  final double? doseMg;
  final int? doseCount;
  final String? stage;

  /// 최근 기록 체중. 기록이 없으면 null — 스테퍼는 기본값에서 시작한다.
  final double? weightKg;

  /// 지난주 대비 변화. 모르면 null — 뱃지를 숨긴다.
  final double? weeklyWeightDeltaKg;
}

// ── API 서비스 ─────────────────────────────────────────────

/// 컨디션 팝업이 쓰는 엔드포인트.
class ConditionApiService {
  ConditionApiService(this._client);

  final ApiClient _client;

  /// 투약·프로필은 [medicationState] · [profileState] 에 받아 둔 값을 쓴다
  /// (없을 때만 서버에 묻는다).
  Future<ConditionPrefill> fetchPrefill(
    MedicationState medicationState,
    ProfileState profileState,
  ) async {
    final results = await Future.wait<Object?>([
      medicationState.ensureLoaded(),
      _client.get('/user-states/latest'),
    ]);
    final medication = results[0] as MedicationCurrent?;
    // 컨디션 기록이 없으면 200 + data: null 이다.
    final latest = (results[1] as ApiResult).data as Map<String, dynamic>?;

    // 첫 기록이면 프로필에 적은 체중에서 시작한다.
    final weightKg =
        (latest?['weightKg'] as num?)?.toDouble() ??
        (await profileState.ensureLoaded()).weightKg;
    return ConditionPrefill(
      drugName: medication?.drugName,
      doseMg: medication?.doseMg,
      doseCount: medication?.doseCount,
      stage: medication?.stage,
      weightKg: weightKg,
      // 지난주 기록이 없으면 null 이다(0 이 아니다). 뱃지를 숨긴다.
      weeklyWeightDeltaKg: (latest?['weightChangeKg'] as num?)?.toDouble(),
    );
  }

  /// `POST /user-states` — 오늘 컨디션 저장.
  ///
  /// Figma 는 강도 선택이 하나라 고른 증상 전체에 같은 강도를 붙인다.
  /// "증상 없음"은 빈 목록이다(null 은 422).
  Future<void> save({
    required double weightKg,
    required Appetite appetite,
    required Set<GiSymptom> symptoms,
    required SymptomSeverity? severity,
  }) async {
    await _client.post(
      '/user-states',
      body: {
        'weightKg': weightKg,
        'appetiteLevel': appetite.level,
        'giSymptoms': [
          for (final s in symptoms)
            {'code': s.code, 'severity': severity!.code},
        ],
      },
    );
  }
}

// ── 팝업 ────────────────────────────────────────────────────

/// 오늘 컨디션 기록 — Figma `hOxrHBitBpjwIBBg2GO49y` node `74:8` (2-a).
///
/// 하루 한 번, 오늘 첫 접속 때 `PopupGate.showDailyPopupsIfDue` 로 뜬다.
/// 직접 `showDialog` 하지 않는다.
///
/// NOTE: 체중 뱃지(질 초록)·식욕(포만감 주황)·GI(양 갈색)의 Q·Q·S 색
/// 재사용은 Figma 그대로다(§0 메타 규칙). 팝업 틀은 `popup_widgets.dart`.
class DailyConditionPopup extends StatefulWidget {
  const DailyConditionPopup({super.key});

  @override
  State<DailyConditionPopup> createState() => _DailyConditionPopupState();
}

class _DailyConditionPopupState extends State<DailyConditionPopup> {
  /// 체중 스테퍼 한 칸(0.1kg). 소수 오차를 피하려고 0.1kg 단위 정수로 들고 있다.
  static const _defaultWeightTenths = 700;
  static const _minWeightTenths = 200;
  static const _maxWeightTenths = 3000;

  late final ConditionApiService _api = ConditionApiService(
    context.read<ApiClient>(),
  );

  ConditionPrefill? _prefill;
  int _weightTenths = _defaultWeightTenths;
  bool _weightTouched = false;

  /// 체중 숫자 칸. ± 버튼과 키보드 입력이 같은 값을 쓴다.
  late final TextEditingController _weightController = TextEditingController(
    text: _formatWeight(_weightTenths),
  );
  final FocusNode _weightFocus = FocusNode();

  /// 입력 중인 값이 저장할 수 있는 체중인지(빈칸·범위 밖이면 false).
  bool _weightValid = true;

  static String _formatWeight(int tenths) => (tenths / 10).toStringAsFixed(1);

  Appetite? _appetite;

  /// GI "없음"을 골랐는지. 증상을 하나라도 고르면 풀린다.
  bool _noSymptom = false;
  final Set<GiSymptom> _symptoms = {};
  SymptomSeverity? _severity;

  bool _saving = false;
  String? _saveError;

  @override
  void initState() {
    super.initState();
    // 입력을 마치면(포커스가 빠지면) 소수 한 자리로 다시 적는다.
    _weightFocus.addListener(() {
      if (!_weightFocus.hasFocus && _weightValid) _syncWeightText();
    });
    _loadPrefill();
  }

  @override
  void dispose() {
    _weightController.dispose();
    _weightFocus.dispose();
    super.dispose();
  }

  void _syncWeightText() {
    _weightController.text = _formatWeight(_weightTenths);
  }

  /// 프리필은 스켈레톤 없이 폼을 먼저 그리고 응답이 오면 채운다.
  /// 사용자가 이미 체중을 만졌으면 덮어쓰지 않는다.
  Future<void> _loadPrefill() async {
    try {
      final prefill = await _api.fetchPrefill(
        context.read<MedicationState>(),
        context.read<ProfileState>(),
      );
      if (!mounted) return;
      setState(() {
        _prefill = prefill;
        final weight = prefill.weightKg;
        if (weight != null && !_weightTouched) {
          _weightTenths = (weight * 10).round();
          _syncWeightText();
        }
      });
    } catch (_) {
      // 프리필 실패는 입력을 막지 않는다. 헤더 부제와 뱃지만 빠진다.
    }
  }

  void _changeWeight(int deltaTenths) {
    setState(() {
      _weightTouched = true;
      _weightValid = true;
      _weightTenths = (_weightTenths + deltaTenths).clamp(
        _minWeightTenths,
        _maxWeightTenths,
      );
    });
    _syncWeightText();
  }

  /// 키보드 입력. 치는 도중(`7` → `78`)에는 범위를 강제하지 않고, 저장할 수
  /// 있는 값일 때만 체중으로 받는다. 범위 밖이면 저장 버튼을 막는다.
  void _onWeightTyped(String text) {
    final kg = double.tryParse(text);
    final tenths = kg == null ? null : (kg * 10).round();
    setState(() {
      _weightTouched = true;
      _weightValid =
          tenths != null &&
          tenths >= _minWeightTenths &&
          tenths <= _maxWeightTenths;
      if (_weightValid) _weightTenths = tenths!;
    });
  }

  void _selectNoSymptom() {
    setState(() {
      _noSymptom = true;
      _symptoms.clear();
      _severity = null;
    });
  }

  void _toggleSymptom(GiSymptom symptom) {
    setState(() {
      _noSymptom = false;
      if (!_symptoms.remove(symptom)) _symptoms.add(symptom);
      if (_symptoms.isEmpty) _severity = null;
    });
  }

  /// 식욕을 골랐고, GI 는 "없음" 이거나 증상+강도까지 골라야 저장할 수 있다.
  bool get _canSave =>
      !_saving &&
      _weightValid &&
      _appetite != null &&
      (_noSymptom || (_symptoms.isNotEmpty && _severity != null));

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _saveError = null;
    });
    try {
      await _api.save(
        weightKg: _weightTenths / 10,
        appetite: _appetite!,
        symptoms: Set.of(_symptoms),
        severity: _severity,
      );
      if (!mounted) return;
      // 서버가 최근 체중을 이 기록으로 바꾼다. 마이 탭 체중도 맞춘다.
      context.read<ProfileState>().refreshInBackground();
      Navigator.of(context).pop(true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saveError = '저장하지 못했어요: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _later() => Navigator.of(context).pop(false);

  String get _subtitle {
    final now = DateTime.now();
    final date = '${now.month}월 ${now.day}일';
    final p = _prefill;
    final (drug, doseMg, doseCount, stage) = (
      p?.drugName,
      p?.doseMg,
      p?.doseCount,
      p?.stage,
    );
    if (drug == null || doseMg == null || doseCount == null || stage == null) {
      return date;
    }
    // 1.0 → "1.0", 0.25 → "0.25", 1.7 → "1.7"
    final dose = doseMg % 1 == 0
        ? doseMg.toStringAsFixed(1)
        : doseMg.toString();
    return '$date · $drug ${dose}mg · $doseCount회차 · ${stageLabel(stage)}';
  }

  @override
  Widget build(BuildContext context) {
    return PopupDialog(
      children: [
        PopupHeader(title: '오늘 컨디션 기록', subtitle: _subtitle, onClose: _later),
        const SizedBox(height: AppSpacing.lg),
        _buildWeight(),
        const SizedBox(height: AppSpacing.lg),
        _buildAppetite(),
        const SizedBox(height: AppSpacing.lg),
        _buildGiSymptoms(),
        const SizedBox(height: AppSpacing.lg),
        const _InfoNote(
          text:
              '체중·식욕·GI는 투약 단계 판정과 다음 끼니 제안에 함께 쓰여요. '
              '증상이 심하면 담당 의사와 상의해 주세요.',
        ),
        if (_saveError != null) ...[
          const SizedBox(height: AppSpacing.md),
          PopupErrorText(message: _saveError!),
        ],
        const SizedBox(height: AppSpacing.lg),
        PopupActions(
          onLater: _later,
          onSave: _canSave ? _save : null,
          saving: _saving,
        ),
      ],
    );
  }

  Widget _buildWeight() {
    final delta = _prefill?.weeklyWeightDeltaKg;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        PopupSectionLabel(
          title: '체중',
          trailing: delta == null ? null : _WeightDeltaBadge(deltaKg: delta),
        ),
        const SizedBox(height: AppSpacing.sm),
        Container(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.md,
            vertical: AppSpacing.md,
          ),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: AppRadius.lgRadius,
            border: Border.all(color: AppColors.border),
          ),
          child: Row(
            children: [
              _RoundStepButton(
                label: '−',
                onTap: _weightTenths > _minWeightTenths
                    ? () => _changeWeight(-1)
                    : null,
              ),
              Expanded(
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.baseline,
                  textBaseline: TextBaseline.alphabetic,
                  children: [
                    SizedBox(
                      width: 88,
                      child: TextField(
                        controller: _weightController,
                        focusNode: _weightFocus,
                        textAlign: TextAlign.end,
                        keyboardType: const TextInputType.numberWithOptions(
                          decimal: true,
                        ),
                        textInputAction: TextInputAction.done,
                        // 정수 세 자리 + 소수 한 자리까지만.
                        inputFormatters: [
                          FilteringTextInputFormatter.allow(
                            RegExp(r'^\d{0,3}(\.\d?)?'),
                          ),
                        ],
                        style: AppTypography.emphasis.copyWith(fontSize: 24),
                        decoration: const InputDecoration(
                          isDense: true,
                          border: InputBorder.none,
                          contentPadding: EdgeInsets.zero,
                        ),
                        onChanged: _onWeightTyped,
                      ),
                    ),
                    const SizedBox(width: AppSpacing.xs),
                    Text(
                      'kg',
                      style: AppTypography.body.copyWith(
                        color: AppColors.textTertiary,
                      ),
                    ),
                  ],
                ),
              ),
              _RoundStepButton(
                label: '＋',
                onTap: _weightTenths < _maxWeightTenths
                    ? () => _changeWeight(1)
                    : null,
              ),
            ],
          ),
        ),
        if (!_weightValid) ...[
          const SizedBox(height: AppSpacing.xs),
          Text(
            '${_formatWeight(_minWeightTenths)}~${_formatWeight(_maxWeightTenths)}kg 사이로 적어 주세요',
            style: AppTypography.caption.copyWith(color: AppColors.primary),
          ),
        ],
      ],
    );
  }

  Widget _buildAppetite() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const PopupSectionLabel(title: '식욕', hint: '지금 배고픔이 어느 정도예요?'),
        const SizedBox(height: AppSpacing.sm),
        Row(
          children: [
            for (final a in Appetite.values) ...[
              if (a != Appetite.values.first)
                const SizedBox(width: AppSpacing.xs),
              Expanded(
                child: PopupChoiceTile(
                  label: a.label,
                  selected: _appetite == a,
                  onTap: () => setState(() => _appetite = a),
                ),
              ),
            ],
          ],
        ),
      ],
    );
  }

  Widget _buildGiSymptoms() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const PopupSectionLabel(title: 'GI 증상', hint: '여러 개 선택 가능'),
        const SizedBox(height: AppSpacing.sm),
        Wrap(
          spacing: AppSpacing.xs,
          runSpacing: AppSpacing.xs,
          children: [
            _SymptomChip(
              label: '없음',
              selected: _noSymptom,
              onTap: _selectNoSymptom,
            ),
            for (final s in GiSymptom.values)
              _SymptomChip(
                label: s.label,
                selected: _symptoms.contains(s),
                onTap: () => _toggleSymptom(s),
              ),
          ],
        ),
        if (_symptoms.isNotEmpty) ...[
          const SizedBox(height: AppSpacing.sm),
          _SeverityRow(
            selected: _severity,
            onSelect: (s) => setState(() => _severity = s),
          ),
        ],
      ],
    );
  }
}

// ── 조각 위젯 ───────────────────────────────────────────────

class _WeightDeltaBadge extends StatelessWidget {
  const _WeightDeltaBadge({required this.deltaKg});

  final double deltaKg;

  @override
  Widget build(BuildContext context) {
    final sign = deltaKg > 0 ? '+' : (deltaKg < 0 ? '−' : '±');
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.sm,
        vertical: 3,
      ),
      decoration: const BoxDecoration(
        color: AppColors.qualityBg,
        borderRadius: AppRadius.smRadius,
      ),
      child: Text(
        '지난주 대비 $sign${deltaKg.abs().toStringAsFixed(1)}kg',
        style: AppTypography.emphasis.copyWith(
          fontSize: 10,
          color: AppColors.quality,
        ),
      ),
    );
  }
}

class _RoundStepButton extends StatelessWidget {
  const _RoundStepButton({required this.label, this.onTap});

  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: AppColors.surfaceMuted,
      shape: const CircleBorder(side: BorderSide(color: AppColors.border)),
      child: InkWell(
        onTap: onTap,
        customBorder: const CircleBorder(),
        child: SizedBox.square(
          dimension: 32,
          child: Center(
            child: Text(
              label,
              style: AppTypography.sectionHead.copyWith(
                fontSize: 15,
                color: onTap == null
                    ? AppColors.inactive
                    : AppColors.textSecondary,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// GI 증상 칩. 선택 시 양 계열 + ✓ (Figma 그대로).
class _SymptomChip extends StatelessWidget {
  const _SymptomChip({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadius.pillRadius,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
        decoration: BoxDecoration(
          color: selected ? AppColors.quantityBg : AppColors.surface,
          borderRadius: AppRadius.pillRadius,
          border: Border.all(
            color: selected ? AppColors.quantity : AppColors.border,
            width: selected ? 1.5 : 1,
          ),
        ),
        child: Text(
          selected ? '✓ $label' : label,
          style: selected
              ? AppTypography.caption.copyWith(
                  fontWeight: FontWeight.w700,
                  color: AppColors.quantity,
                )
              : AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      ),
    );
  }
}

/// 증상 강도 3단계. 증상을 하나 이상 골랐을 때만 보인다.
class _SeverityRow extends StatelessWidget {
  const _SeverityRow({required this.selected, required this.onSelect});

  final SymptomSeverity? selected;
  final ValueChanged<SymptomSeverity> onSelect;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.md,
        vertical: AppSpacing.sm,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surfaceSage,
        borderRadius: AppRadius.mdRadius,
      ),
      child: Row(
        children: [
          Text(
            '증상 강도',
            style: AppTypography.caption.copyWith(
              fontWeight: FontWeight.w700,
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          for (final s in SymptomSeverity.values) ...[
            if (s != SymptomSeverity.values.first)
              const SizedBox(width: AppSpacing.xs),
            Expanded(child: _severityButton(s)),
          ],
        ],
      ),
    );
  }

  Widget _severityButton(SymptomSeverity s) {
    final isSelected = selected == s;
    return InkWell(
      onTap: () => onSelect(s),
      borderRadius: AppRadius.smRadius,
      child: Container(
        height: 26,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: isSelected ? AppColors.quantity : AppColors.surface,
          borderRadius: AppRadius.smRadius,
          border: Border.all(
            color: isSelected ? AppColors.quantity : AppColors.border,
          ),
        ),
        child: Text(
          s.label,
          style: isSelected
              ? AppTypography.caption.copyWith(
                  fontWeight: FontWeight.w700,
                  color: AppColors.textInverse,
                )
              : AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      ),
    );
  }
}

class _InfoNote extends StatelessWidget {
  const _InfoNote({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(AppSpacing.md),
      decoration: BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.mdRadius,
        border: Border.all(color: AppColors.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 18,
            height: 18,
            alignment: Alignment.center,
            decoration: const BoxDecoration(
              color: AppColors.borderStrong,
              shape: BoxShape.circle,
            ),
            child: Text(
              'i',
              style: AppTypography.caption.copyWith(
                fontWeight: FontWeight.w700,
                color: AppColors.textInverse,
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              text,
              style: AppTypography.caption.copyWith(
                color: AppColors.textSecondary,
                height: 1.5,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
