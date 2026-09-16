import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

/// 투약 정보 입력 — 온보딩 1/2 단계.
///
/// Figma `hOxrHBitBpjwIBBg2GO49y` node `60:11`.
///
/// **지금은 레이아웃 뼈대다.** 선택 상태만 로컬 `setState` 로 돌고 저장은 하지 않는다.
/// 연동하면 `POST /medications`(등록·수정 겸용) 으로 보낸다.
///
/// 프리필(`GET /medications/current`)은 스켈레톤을 쓰지 않는다. 폼을 기본값으로
/// 즉시 렌더하고 응답이 오면 채운다. 단, **응답이 늦게 와도 사용자가 이미 손댄
/// 입력은 덮어쓰지 않는다** — 그 시점에 "사용자가 만졌는지" 플래그가 필요해진다.
///
/// 탭바 밖 화면이라 `Navigator.push` 로 띄운다.
class MedicationInfoScreen extends StatefulWidget {
  const MedicationInfoScreen({super.key});

  @override
  State<MedicationInfoScreen> createState() => _MedicationInfoScreenState();
}

class _MedicationInfoScreenState extends State<MedicationInfoScreen> {
  /// 약별 1회 용량 스텝(mg, 주 1회). 약을 바꾸면 값도 개수도 달라진다.
  /// Figma 에는 위고비 5칸만 그려져 있어 마운자로는 가로 스크롤로 받는다.
  static const Map<String, List<String>> _dosesByDrug = {
    '위고비': ['0.25', '0.5', '1.0', '1.7', '2.4'],
    '마운자로': ['2.5', '5', '7.5', '10', '12.5', '15'],
  };

  String _drug = '위고비';
  String _dose = '1.0';
  DateTime _startedAt = DateTime(2026, 6, 14);
  int _doseCount = 12;

  /// 저장(`POST /medications`) 진행 중. 버튼을 잠가 중복 전송을 막는다.
  bool _saving = false;

  /// 저장 실패. 입력값은 그대로 두고 버튼 위에 재시도만 띄운다.
  bool _saveFailed = false;

  void _submit() {
    setState(() {
      _saving = true;
      _saveFailed = false;
    });
    // TODO: POST /medications → 성공 시 2/2(프로필) 단계로 이동.
    //       실패하면 _saving=false, _saveFailed=true 로 되돌린다.
  }

  void _selectDrug(String drug) {
    setState(() {
      _drug = drug;
      // 약이 바뀌면 이전 용량이 존재하지 않을 수 있다.
      _dose = _dosesByDrug[drug]!.first;
    });
  }

  Future<void> _pickStartDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _startedAt,
      firstDate: DateTime(2020),
      lastDate: DateTime.now(),
    );
    if (picked != null) setState(() => _startedAt = picked);
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
            _Header(step: 1, totalSteps: 2),
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
                    Text(
                      '투약 정보를 알려주세요',
                      style: AppTypography.screenTitle,
                    ),
                    const SizedBox(height: AppSpacing.xs),
                    Text(
                      '같은 식사도 투약 단계에 따라 다르게 평가해요',
                      style: AppTypography.bodySecondary,
                    ),
                    const SizedBox(height: AppSpacing.xl),

                    const _SectionLabel('복용 중인 약'),
                    const SizedBox(height: AppSpacing.md),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
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
                              label: dose,
                              selected: _dose == dose,
                              onTap: () => setState(() => _dose = dose),
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
                                          ? () => setState(() => _doseCount--)
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
                                      onTap: () => setState(() => _doseCount++),
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

                    const _StageResultBlock(),
                  ],
                ),
              ),
            ),

            Padding(
              padding: const EdgeInsets.all(AppSpacing.screenHorizontal),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_saveFailed) ...[
                    RetryBlock(
                      message: '저장하지 못했어요. 잠시 후 다시 시도해 주세요',
                      onRetry: _submit,
                    ),
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
                    child: Text(_saving ? '저장 중' : '다음'),
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
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.lg,
          vertical: AppSpacing.lg,
        ),
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
/// **아직 값을 채우지 않는다.** 단계 판정은 서버(`POST /medications`)가 하고
/// 경계값도 미확정이라 FE 가 예측할 수 없다. 입력할 때마다 POST 를 부르면
/// "용량 변경 자동 기록"이 실제 이력이 아닌 값으로 오염된다.
/// BE 에 판정 전용 dry-run 이 생기면 그걸 붙이고, 그전까지는 안내만 둔다.
class _StageResultBlock extends StatelessWidget {
  const _StageResultBlock();

  @override
  Widget build(BuildContext context) {
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
            '다음을 누르면 알려드려요',
            style: AppTypography.screenTitle.copyWith(
              color: AppColors.textSecondary,
            ),
          ),
          const SizedBox(height: AppSpacing.sm),
          Text(
            '투약 단계에 따라 같은 식사도 다르게 평가돼요',
            style: AppTypography.bodySecondary,
          ),
        ],
      ),
    );
  }
}
