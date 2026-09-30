import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/user_api.dart';
import '../state/user_session.dart';
import '../theme/app_colors.dart';
import '../theme/app_radius.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 프로필 입력 — 온보딩 첫 화면. Figma 노드가 없어 간단히 구성했다.
///
/// 저장된 `userId` 가 없으면 `main.dart` 가 이 화면으로 시작한다. 저장에
/// 성공하면 [UserSession] 에 `userId` 가 들어가고, `main.dart` 가 알아서
/// 탭 화면(`RootShell`)으로 바꾼다 — 여기서 직접 이동하지 않는다.
///
/// 백엔드는 프로필 → 투약 정보 순서다(`onboardingStatus`:
/// PROFILE_REQUIRED → MEDICATION_REQUIRED → READY). 투약 정보를 쓰려면
/// 사용자가 먼저 있어야 하기 때문이다. 투약 입력으로 이어 보내는 건
/// `RootShell` 이 `onboardingStatus` 를 보고 한다.
///
/// **[initial] 을 주면 수정 모드다**(마이 탭). 입력칸·검증은 같고, 저장이
/// `PATCH /users/me` 로 바뀌며 바꾼 칸만 보낸다. 저장하면 바뀐 [UserProfile]
/// 을 들고 닫힌다(안 바꿨으면 호출 없이 [initial] 그대로).
class ProfileInputScreen extends StatefulWidget {
  const ProfileInputScreen({super.key, this.initial});

  final UserProfile? initial;

  @override
  State<ProfileInputScreen> createState() => _ProfileInputScreenState();
}

class _ProfileInputScreenState extends State<ProfileInputScreen> {
  final _formKey = GlobalKey<FormState>();
  late final _nickname = TextEditingController(text: widget.initial?.nickname);
  late final _height = TextEditingController(
    text: _numberText(widget.initial?.heightCm),
  );
  late final _weight = TextEditingController(
    text: _numberText(widget.initial?.weightKg),
  );
  late final _baselineIntake = TextEditingController(
    text: _numberText(widget.initial?.baselineIntake),
  );

  late final UserApiService _api = UserApiService(context.read<ApiClient>());

  bool get _editing => widget.initial != null;

  bool _saving = false;
  String? _saveError;

  @override
  void dispose() {
    _nickname.dispose();
    _height.dispose();
    _weight.dispose();
    _baselineIntake.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _saveError = null;
    });
    try {
      final nickname = _nickname.text.trim();
      final heightCm = double.parse(_height.text);
      final weightKg = double.parse(_weight.text);
      final baselineIntake = double.parse(_baselineIntake.text);
      final initial = widget.initial;
      if (initial == null) {
        final profile = await _api.createProfile(
          nickname: nickname,
          heightCm: heightCm,
          weightKg: weightKg,
          baselineIntake: baselineIntake,
        );
        if (!mounted) return;
        await context.read<UserSession>().setUserId(profile.userId);
        return;
      }
      // 바꾼 칸만 보낸다. 체중은 보낼 때마다 새 기록이 되므로 특히 그렇다.
      final changed =
          nickname != initial.nickname ||
          heightCm != initial.heightCm ||
          weightKg != initial.weightKg ||
          baselineIntake != initial.baselineIntake;
      final profile = changed
          ? await _api.updateProfile(
              nickname: nickname != initial.nickname ? nickname : null,
              heightCm: heightCm != initial.heightCm ? heightCm : null,
              weightKg: weightKg != initial.weightKg ? weightKg : null,
              baselineIntake: baselineIntake != initial.baselineIntake
                  ? baselineIntake
                  : null,
            )
          : initial;
      if (!mounted) return;
      Navigator.of(context).pop(profile);
    } catch (e) {
      if (!mounted) return;
      setState(() => _saveError = '저장하지 못했어요: $e');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_editing ? '프로필 수정' : '프로필 입력')),
      body: SafeArea(
        child: Form(
          key: _formKey,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(
              AppSpacing.screenHorizontal,
              AppSpacing.lg,
              AppSpacing.screenHorizontal,
              AppSpacing.xl,
            ),
            children: [
              Text(
                _editing
                    ? '체중을 바꾸면 오늘 체중 기록으로 남아요.'
                    : '맞춤 코칭을 위해 기본 정보를 알려 주세요.',
                style: AppTypography.bodySecondary,
              ),
              const SizedBox(height: AppSpacing.xl),
              _ProfileField(
                label: '닉네임',
                controller: _nickname,
                hint: '앱에서 불릴 이름',
                validator: (v) {
                  final text = v?.trim() ?? '';
                  if (text.isEmpty) return '닉네임을 입력해 주세요';
                  if (text.length > 64) return '64자 이하로 입력해 주세요';
                  return null;
                },
              ),
              const SizedBox(height: AppSpacing.lg),
              _ProfileField(
                label: '키',
                controller: _height,
                unit: 'cm',
                numeric: true,
                validator: (v) => _validateRange(v, max: 300, name: '키'),
              ),
              const SizedBox(height: AppSpacing.lg),
              _ProfileField(
                label: '현재 체중',
                controller: _weight,
                unit: 'kg',
                numeric: true,
                validator: (v) => _validateRange(v, max: 500, name: '체중'),
              ),
              const SizedBox(height: AppSpacing.lg),
              _ProfileField(
                label: '평소 한 끼 열량',
                controller: _baselineIntake,
                unit: 'kcal',
                numeric: true,
                hint: '투약 전 평소 한 끼 기준',
                validator: (v) => _validateRange(v, max: 10000, name: '열량'),
              ),
              if (_saveError != null) ...[
                const SizedBox(height: AppSpacing.lg),
                Text(
                  _saveError!,
                  style: AppTypography.caption.copyWith(
                    color: AppColors.primary,
                  ),
                ),
              ],
              const SizedBox(height: AppSpacing.xl),
              FilledButton(
                onPressed: _saving ? null : _submit,
                child: Text(
                  _saving
                      ? '저장 중'
                      : _editing
                      ? '저장'
                      : '시작하기',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 입력칸 초기값. `175.0` → `175`, `78.4` → `78.4`, 없으면 빈칸.
String _numberText(double? value) {
  if (value == null) return '';
  return value % 1 == 0 ? value.toStringAsFixed(0) : '$value';
}

/// 백엔드 스키마(`backend/app/schemas/user.py`) 범위: 0 초과 ~ [max] 이하.
String? _validateRange(
  String? value, {
  required double max,
  required String name,
}) {
  final parsed = double.tryParse(value ?? '');
  if (parsed == null) return '$name을(를) 숫자로 입력해 주세요';
  if (parsed <= 0 || parsed > max) return '0보다 크고 $max 이하로 입력해 주세요';
  return null;
}

class _ProfileField extends StatelessWidget {
  const _ProfileField({
    required this.label,
    required this.controller,
    required this.validator,
    this.hint,
    this.unit,
    this.numeric = false,
  });

  final String label;
  final TextEditingController controller;
  final FormFieldValidator<String> validator;
  final String? hint;
  final String? unit;
  final bool numeric;

  @override
  Widget build(BuildContext context) {
    const border = OutlineInputBorder(
      borderRadius: AppRadius.mdRadius,
      borderSide: BorderSide(color: AppColors.border),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: AppTypography.cardTitle),
        const SizedBox(height: AppSpacing.sm),
        TextFormField(
          controller: controller,
          validator: validator,
          style: AppTypography.body,
          keyboardType: numeric
              ? const TextInputType.numberWithOptions(decimal: true)
              : TextInputType.text,
          inputFormatters: numeric
              ? [FilteringTextInputFormatter.allow(RegExp(r'[0-9.]'))]
              : null,
          decoration: InputDecoration(
            hintText: hint,
            hintStyle: AppTypography.bodySecondary,
            suffixText: unit,
            suffixStyle: AppTypography.bodySecondary,
            filled: true,
            fillColor: AppColors.surface,
            isDense: true,
            contentPadding: const EdgeInsets.all(AppSpacing.md),
            border: border,
            enabledBorder: border,
            focusedBorder: border.copyWith(
              borderSide: const BorderSide(color: AppColors.primary),
            ),
          ),
        ),
      ],
    );
  }
}
