import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

// ── 모델 ────────────────────────────────────────────────────

/// `POST /medications` · `GET /medications/current` 응답.
class MedicationCurrent {
  const MedicationCurrent({
    required this.medicationId,
    required this.drugName,
    required this.doseMg,
    required this.startedAt,
    required this.doseCount,
    required this.nextDoseDate,
    required this.daysUntilNextDose,
    required this.stage,
    this.stageReason,
  });

  final String medicationId;
  final String drugName;
  final double doseMg;
  final DateTime startedAt;

  /// 서버 계산: `floor((today − startedAt) / 7) + 1`.
  final int doseCount;

  final DateTime nextDoseDate;
  final int daysUntilNextDose;

  /// `INITIAL` · `TITRATION` · `MAINTENANCE`.
  final String stage;

  /// `약효가 줄고 식욕이 돌아오는 구간` 같은 한 줄 설명.
  final String? stageReason;

  factory MedicationCurrent.fromJson(Map<String, dynamic> json) =>
      MedicationCurrent(
        medicationId: json['medicationId'] as String,
        drugName: json['drugName'] as String,
        doseMg: (json['doseMg'] as num).toDouble(),
        startedAt: DateTime.parse(json['startedAt'] as String),
        doseCount: json['doseCount'] as int,
        nextDoseDate: DateTime.parse(json['nextDoseDate'] as String),
        daysUntilNextDose: json['daysUntilNextDose'] as int,
        stage: json['stage'] as String,
        stageReason: json['stageReason'] as String?,
      );
}

String stageLabel(String stage) => switch (stage) {
  'INITIAL' => '도입기',
  'TITRATION' => '증량기',
  'MAINTENANCE' => '유지기',
  _ => stage,
};

/// `2026-06-14` 형식. 요청 body 에 쓴다.
String formatApiDate(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}-'
    '${d.day.toString().padLeft(2, '0')}';

// ── API ─────────────────────────────────────────────────────

/// 투약 화면이 쓰는 엔드포인트.
///
/// 지금은 명세 예시를 그대로 돌려준다. 통신 라이브러리가 정해지면
/// 메서드 본문만 교체하면 되고 화면은 건드리지 않는다.
class MedicationApiService {
  /// `GET /medications/current`
  ///
  /// 미등록이면 `error.code = STAGE_NOT_SET` 이 온다. 이건 실패가 아니라
  /// "아직 입력 안 함" 이므로 null 로 바꿔 돌려주고 화면은 빈 폼을 보여 준다.
  Future<MedicationCurrent?> fetchCurrent() async {
    // TODO(http|dio 결정 후): 실제 GET 요청으로 교체.
    //   error.code == 'STAGE_NOT_SET' 이면 null 을 반환하고,
    //   그 밖의 에러만 throw 한다.
    await Future.delayed(const Duration(milliseconds: 250));
    return MedicationCurrent.fromJson(_sample);
  }

  /// `POST /medications` — 등록·수정 겸용.
  ///
  /// `doseMg` 가 현재 값과 다르면 서버가 `dose_events` 를 자동 기록한다.
  /// 그래서 미리보기 용도로는 절대 부르면 안 된다.
  Future<MedicationCurrent> save({
    required String drugName,
    required double doseMg,
    required DateTime startedAt,
  }) async {
    // TODO(http|dio 결정 후): 실제 POST 요청으로 교체.
    //   body: { drugName, doseMg, startedAt: "YYYY-MM-DD" }
    await Future.delayed(const Duration(milliseconds: 300));
    return MedicationCurrent.fromJson({
      ..._sample,
      'drugName': drugName,
      'doseMg': doseMg,
      'startedAt': formatApiDate(startedAt),
    });
  }

  /// 명세의 `POST /medications` 예시 응답(`data` 안쪽).
  static const Map<String, dynamic> _sample = {
    'medicationId': 'med_01H8',
    'drugName': '위고비',
    'doseMg': 1.0,
    'startedAt': '2026-06-14',
    'doseCount': 10,
    'nextDoseDate': '2026-08-23',
    'daysUntilNextDose': 2,
    'stage': 'MAINTENANCE',
    'stageReason': '약효가 줄고 식욕이 돌아오는 구간',
    'ruleVersion': 'v1',
    'doseChanged': false,
    'doseEvent': null,
    'stageChanged': false,
    'decidedAt': '2026-08-21T09:12:00+09:00',
  };
}

// ── 화면 ────────────────────────────────────────────────────

/// 투약 정보 입력 — 온보딩 1/2 단계.
///
/// Figma `hOxrHBitBpjwIBBg2GO49y` node `60:11`. 탭바 밖 화면이라
/// `Navigator.push` 로 띄운다.
///
/// 프리필(`GET /medications/current`)은 스켈레톤을 쓰지 않는다. 폼을 기본값으로
/// 즉시 렌더하고 응답이 오면 채운다. 단 **사용자가 이미 손댄 입력은 덮어쓰지
/// 않는다**.
class MedicationInfoScreen extends StatefulWidget {
  const MedicationInfoScreen({super.key});

  @override
  State<MedicationInfoScreen> createState() => _MedicationInfoScreenState();
}

class _MedicationInfoScreenState extends State<MedicationInfoScreen> {
  final MedicationApiService _api = MedicationApiService();

  /// 약별 1회 용량 스텝(mg, 주 1회). 약을 바꾸면 값도 개수도 달라진다.
  /// Figma 에는 위고비 5칸만 그려져 있어 마운자로는 가로 스크롤로 받는다.
  ///
  /// 표기를 값과 같이 들고 있는 이유: 위고비는 `1.0`, 마운자로는 `5` 로 적는다.
  /// 숫자에서 규칙으로 만들어 내려 하면 한쪽이 반드시 틀어진다.
  static const Map<String, List<(String, double)>> _dosesByDrug = {
    '위고비': [
      ('0.25', 0.25),
      ('0.5', 0.5),
      ('1.0', 1.0),
      ('1.7', 1.7),
      ('2.4', 2.4),
    ],
    '마운자로': [
      ('2.5', 2.5),
      ('5', 5.0),
      ('7.5', 7.5),
      ('10', 10.0),
      ('12.5', 12.5),
      ('15', 15.0),
    ],
  };

  String _drug = '위고비';
  double _dose = 1.0;
  DateTime _startedAt = DateTime(2026, 6, 14);

  /// 서버가 판정한 현재 투약. 단계 블록을 채우는 데만 쓴다.
  MedicationCurrent? _current;

  /// 사용자가 폼을 건드렸는지. 늦게 도착한 프리필이 입력을 덮지 않게 한다.
  bool _touchedByUser = false;

  bool _saving = false;
  String? _saveError;

  @override
  void initState() {
    super.initState();
    _loadCurrent();
  }

  Future<void> _loadCurrent() async {
    try {
      final current = await _api.fetchCurrent();
      if (!mounted || current == null) return;
      setState(() {
        _current = current;
        // 사용자가 이미 손댔으면 입력값은 건드리지 않는다.
        if (!_touchedByUser) {
          _drug = current.drugName;
          _dose = current.doseMg;
          _startedAt = current.startedAt;
        }
      });
    } catch (_) {
      // 프리필 실패는 조용히 넘긴다. 빈 폼으로도 입력할 수 있고,
      // 여기서 재시도 블록을 띄우면 처음 쓰는 사람에게 실패처럼 보인다.
    }
  }

  static DateTime _dateOnly(DateTime d) => DateTime(d.year, d.month, d.day);

  /// 오늘 기준 투약 회차. 서버 공식과 같다.
  int get _doseCount =>
      _dateOnly(DateTime.now()).difference(_startedAt).inDays ~/ 7 + 1;

  /// 회차를 바꾸면 시작일을 역산한다.
  ///
  /// 명세상 `doseCount` 는 서버가 `startedAt` 으로 계산하고 `POST /medications`
  /// body 에도 회차 자리가 없다. 그래서 Figma 의 회차 스테퍼는 그대로는 보낼
  /// 곳이 없다. 시작일을 거꾸로 맞추면 스테퍼를 살리면서 유효한 body 가 되고,
  /// 바로 옆 `투약 시작일` 필드가 같이 바뀌어서 동작이 눈에 드러난다.
  void _changeDoseCount(int delta) {
    final next = _doseCount + delta;
    if (next < 1) return;
    _edit(() {
      _startedAt = _dateOnly(
        DateTime.now(),
      ).subtract(Duration(days: (next - 1) * 7));
    });
  }

  void _edit(VoidCallback change) {
    setState(() {
      _touchedByUser = true;
      change();
    });
  }

  void _selectDrug(String drug) {
    _edit(() {
      _drug = drug;
      // 약이 바뀌면 이전 용량이 목록에 없을 수 있다.
      _dose = _dosesByDrug[drug]!.first.$2;
    });
  }

  Future<void> _pickStartDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _startedAt,
      firstDate: DateTime(2020),
      lastDate: DateTime.now(),
    );
    if (picked != null) _edit(() => _startedAt = picked);
  }

  Future<void> _submit() async {
    setState(() {
      _saving = true;
      _saveError = null;
    });
    try {
      final saved = await _api.save(
        drugName: _drug,
        doseMg: _dose,
        startedAt: _startedAt,
      );
      if (!mounted) return;
      setState(() => _current = saved);
      // 저장 완료 → 홈으로 돌아간다. 이 화면은 홈 투약 카드에서만 열리고,
      // 홈이 돌아온 뒤 투약 정보를 다시 불러온다.
      // TODO: 온보딩(2/2 프로필 입력)이 생기면 그 흐름에서는 다음 단계로 보낸다.
      Navigator.of(context).pop();
    } catch (e) {
      if (!mounted) return;
      setState(() => _saveError = '저장하지 못했어요: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  String get _formattedStartDate =>
      '${_startedAt.year}. '
      '${_startedAt.month.toString().padLeft(2, '0')}. '
      '${_startedAt.day.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    final doses = _dosesByDrug[_drug]!;

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            const _Header(step: 1, totalSteps: 2),
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(
                  AppSpacing.screenHorizontal,
                  AppSpacing.xl,
                  AppSpacing.screenHorizontal,
                  AppSpacing.xl,
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('투약 정보를 알려주세요', style: AppTypography.screenTitle),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      '같은 식사도 투약 단계에 따라 다르게 평가해요',
                      style: AppTypography.bodySecondary,
                    ),
                    const SizedBox(height: AppSpacing.xl),

                    const _SectionLabel('복용 중인 약'),
                    const SizedBox(height: AppSpacing.md),
                    Row(
                      children: [
                        // Figma 는 주사기 일러스트다. 에셋이 들어오면 교체한다.
                        const Expanded(
                          child: Icon(
                            Icons.vaccines_outlined,
                            size: 72,
                            color: AppColors.borderStrong,
                          ),
                        ),
                        const SizedBox(width: AppSpacing.md),
                        Expanded(
                          child: Column(
                            children: [
                              for (final drug in _dosesByDrug.keys) ...[
                                _SelectableCard(
                                  label: drug,
                                  selected: _drug == drug,
                                  onTap: () => _selectDrug(drug),
                                ),
                                if (drug != _dosesByDrug.keys.last)
                                  const SizedBox(height: AppSpacing.md),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppSpacing.xl),

                    Row(
                      children: [
                        const _SectionLabel('현재 1회 용량'),
                        const Spacer(),
                        Text('mg / 주 1회', style: AppTypography.caption),
                      ],
                    ),
                    const SizedBox(height: AppSpacing.md),
                    SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          for (final dose in doses) ...[
                            _DoseChip(
                              label: dose.$1,
                              selected: _dose == dose.$2,
                              onTap: () => _edit(() => _dose = dose.$2),
                            ),
                            if (dose != doses.last)
                              const SizedBox(width: AppSpacing.sm),
                          ],
                        ],
                      ),
                    ),
                    const SizedBox(height: AppSpacing.xl),

                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const _SectionLabel('투약 시작일'),
                              const SizedBox(height: AppSpacing.md),
                              _FieldBox(
                                onTap: _pickStartDate,
                                child: Row(
                                  children: [
                                    Expanded(
                                      child: Text(
                                        _formattedStartDate,
                                        style: AppTypography.cardTitle,
                                      ),
                                    ),
                                    const Icon(
                                      Icons.calendar_today_outlined,
                                      size: AppLayout.tabIconSize,
                                      color: AppColors.textSecondary,
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(width: AppSpacing.md),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const _SectionLabel('투약 회차'),
                              const SizedBox(height: AppSpacing.md),
                              _FieldBox(
                                child: Row(
                                  children: [
                                    _StepperButton(
                                      icon: Icons.remove,
                                      onTap: _doseCount > 1
                                          ? () => _changeDoseCount(-1)
                                          : null,
                                    ),
                                    Expanded(
                                      child: Text(
                                        '$_doseCount회차',
                                        textAlign: TextAlign.center,
                                        style: AppTypography.cardTitle,
                                      ),
                                    ),
                                    _StepperButton(
                                      icon: Icons.add,
                                      onTap: () => _changeDoseCount(1),
                                    ),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: AppSpacing.xl),

                    _StageResultBlock(current: _current),
                  ],
                ),
              ),
            ),

            Padding(
              padding: const EdgeInsets.all(AppSpacing.screenHorizontal),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_saveError != null) ...[
                    RetryBlock(message: _saveError!, onRetry: _submit),
                    const SizedBox(height: AppSpacing.md),
                  ],
                  FilledButton(
                    // 상단 진행바가 이미 코랄이라, 디자인 가이드 §2
                    // "강조색은 화면당 한 군데만" 에 따라 버튼을 textPrimary 로 내린다.
                    // Figma 도 같은 이유로 진한 갈색 버튼이다.
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.textPrimary,
                    ),
                    // 저장 중에는 잠근다. 비활성 색은 테마가 이미 갖고 있다.
                    onPressed: _saving ? null : _submit,
                    child: Text(_saving ? '저장 중' : '저장'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 뒤로가기 + `1 / 2` + 진행바.
class _Header extends StatelessWidget {
  const _Header({required this.step, required this.totalSteps});

  final int step;
  final int totalSteps;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Row(
          children: [
            IconButton(
              onPressed: () => Navigator.maybePop(context),
              icon: const Icon(Icons.arrow_back_ios_new, size: 18),
              color: AppColors.textPrimary,
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.only(
                right: AppSpacing.screenHorizontal,
              ),
              child: Text('$step / $totalSteps', style: AppTypography.caption),
            ),
          ],
        ),
        const SizedBox(height: AppSpacing.sm),
        LinearProgressIndicator(
          value: step / totalSteps,
          minHeight: 4,
          backgroundColor: AppColors.surfaceMuted,
          color: AppColors.primary,
        ),
      ],
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) =>
      Text(text, style: AppTypography.cardTitle);
}

/// 선택 카드 — 디자인 가이드 §5. 테마 기본 `Card` 와 테두리색이 달라 직접 그린다.
class _SelectableCard extends StatelessWidget {
  const _SelectableCard({
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
      borderRadius: AppRadius.lgRadius,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(AppSpacing.lg),
        decoration: BoxDecoration(
          color: selected ? AppColors.primaryTint : AppColors.surface,
          borderRadius: AppRadius.lgRadius,
          border: Border.all(
            color: selected ? AppColors.primary : AppColors.borderStrong,
          ),
        ),
        child: Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: AppTypography.cardTitle.copyWith(
                  color: selected ? AppColors.primary : AppColors.textPrimary,
                ),
              ),
            ),
            if (selected)
              const Icon(
                Icons.check_circle,
                size: AppLayout.tabIconSize,
                color: AppColors.primary,
              ),
          ],
        ),
      ),
    );
  }
}

/// 용량 칩. 선택 시 진한 갈색 — 버튼과 같은 이유(§2)로 코랄을 쓰지 않는다.
class _DoseChip extends StatelessWidget {
  const _DoseChip({
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
      borderRadius: AppRadius.mdRadius,
      child: Container(
        width: 56,
        height: 44,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: selected ? AppColors.textPrimary : AppColors.surface,
          borderRadius: AppRadius.mdRadius,
          border: Border.all(
            color: selected ? AppColors.textPrimary : AppColors.border,
          ),
        ),
        child: Text(
          label,
          style: AppTypography.cardTitle.copyWith(
            color: selected ? AppColors.textInverse : AppColors.textSecondary,
          ),
        ),
      ),
    );
  }
}

class _FieldBox extends StatelessWidget {
  const _FieldBox({required this.child, this.onTap});

  final Widget child;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: AppRadius.mdRadius,
      child: Container(
        height: AppLayout.primaryButtonHeight,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.md),
        decoration: BoxDecoration(
          color: AppColors.surface,
          borderRadius: AppRadius.mdRadius,
          border: Border.all(color: AppColors.border),
        ),
        child: Center(child: child),
      ),
    );
  }
}

class _StepperButton extends StatelessWidget {
  const _StepperButton({required this.icon, this.onTap});

  final IconData icon;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      customBorder: const CircleBorder(),
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xs),
        child: Icon(
          icon,
          size: AppLayout.tabIconSize,
          color: onTap == null ? AppColors.inactive : AppColors.textPrimary,
        ),
      ),
    );
  }
}

/// 입력 기준 현재 단계.
///
/// 서버가 판정한 값이 있을 때만 채운다. **입력을 바꿀 때마다 미리 보여 주지는
/// 않는다.** 판정은 `POST /medications` 가 하는데 그 호출은 용량이 바뀌면
/// `dose_events` 를 자동 기록해서, 미리보기로 부르면 실제 이력이 아닌 값이
/// 쌓인다. 판정 전용 dry-run 이 생기면 그때 붙인다.
class _StageResultBlock extends StatelessWidget {
  const _StageResultBlock({required this.current});

  final MedicationCurrent? current;

  @override
  Widget build(BuildContext context) {
    final stage = current;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(AppSpacing.cardPadding),
      decoration: const BoxDecoration(
        color: AppColors.surfaceMuted,
        borderRadius: AppRadius.lgRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('입력 기준 현재 단계', style: AppTypography.caption),
          const SizedBox(height: AppSpacing.xs),
          Text(
            stage == null ? '저장을 누르면 알려드려요' : stageLabel(stage.stage),
            style: AppTypography.screenTitle.copyWith(
              color: stage == null ? AppColors.textSecondary : null,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            stage?.stageReason ?? '투약 단계에 따라 같은 식사도 다르게 평가돼요',
            style: AppTypography.bodySecondary,
          ),
        ],
      ),
    );
  }
}
