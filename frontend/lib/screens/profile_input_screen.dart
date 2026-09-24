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
/// 사용자가 먼저 있어야 하기 때문이다.
// TODO: onboardingStatus 가 MEDICATION_REQUIRED 면 투약 정보 입력으로 이어 보낸다.
class ProfileInputScreen extends StatefulWidget {
  const ProfileInputScreen({super.key});

  @override
  State<ProfileInputScreen> createState() => _ProfileInputScreenState();
}

class _ProfileInputScreenState extends State<ProfileInputScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nickname = TextEditingController();
  final _height = TextEditingController();
  final _weight = TextEditingController();
  final _baselineIntake = TextEditingController();

  late final UserApiService _api = UserApiService(context.read<ApiClient>());

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
      final profile = await _api.createProfile(
        nickname: _nickname.text.trim(),
        heightCm: double.parse(_height.text),
        weightKg: double.parse(_weight.text),
        baselineIntake: double.parse(_baselineIntake.text),
      );
      if (!mounted) return;
      await context.read<UserSession>().setUserId(profile.userId);
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
      appBar: AppBar(title: const Text('프로필 입력')),
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
                '맞춤 코칭을 위해 기본 정보를 알려 주세요.',
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
                child: Text(_saving ? '저장 중' : '시작하기'),
              ),
            ],
          ),
        ),
      ),
    );
  }
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
