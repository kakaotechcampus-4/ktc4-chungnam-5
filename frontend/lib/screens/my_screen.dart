import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/user_api.dart';
import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'profile_input_screen.dart';
import 'shared_meal_widgets.dart';

/// 마이 탭 — 회원 정보 조회·수정. Figma 노드가 없어 간단히 구성했다.
///
/// 조회는 `GET /users/me`, 수정은 프로필 입력 화면을 수정 모드로 띄워
/// `PATCH /users/me` 한다.
class MyScreen extends StatefulWidget {
  const MyScreen({super.key});

  @override
  State<MyScreen> createState() => _MyScreenState();
}

class _MyScreenState extends State<MyScreen> {
  late final UserApiService _api = UserApiService(context.read<ApiClient>());

  LoadState _state = LoadState.loading;
  UserProfile? _profile;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (_state != LoadState.loading) {
      setState(() {
        _state = LoadState.loading;
        _errorMessage = null;
      });
    }
    try {
      final profile = await _api.fetchMe();
      if (!mounted) return;
      setState(() {
        _profile = profile;
        _state = LoadState.ready;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '불러오는 데 실패했어요: $e';
        _state = LoadState.failed;
      });
    }
  }

  Future<void> _openEdit(UserProfile profile) async {
    final updated = await Navigator.of(context).push<UserProfile>(
      MaterialPageRoute(builder: (_) => ProfileInputScreen(initial: profile)),
    );
    if (!mounted || updated == null) return;
    setState(() => _profile = updated);
  }

  @override
  Widget build(BuildContext context) {
    final profile = _profile;
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: _load,
        color: AppColors.primary,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.fromLTRB(
            AppSpacing.screenHorizontal,
            AppSpacing.titleTop,
            AppSpacing.screenHorizontal,
            AppLayout.scrollBottomPadding,
          ),
          children: [
            Text('마이', style: AppTypography.screenTitle),
            const SizedBox(height: AppSpacing.xl),
            switch (_state) {
              LoadState.loading => const Center(
                child: CircularProgressIndicator(color: AppColors.primary),
              ),
              LoadState.failed => RetryBlock(
                message: _errorMessage ?? '잠시 후 다시 시도해 주세요',
                onRetry: _load,
              ),
              LoadState.ready => _ProfileCard(
                profile: profile!,
                onEdit: () => _openEdit(profile),
              ),
            },
          ],
        ),
      ),
    );
  }
}

class _ProfileCard extends StatelessWidget {
  const _ProfileCard({required this.profile, required this.onEdit});

  final UserProfile profile;
  final VoidCallback onEdit;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.cardPadding),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '${profile.nickname} 님',
                    style: AppTypography.sectionHead,
                  ),
                ),
                TextButton(onPressed: onEdit, child: const Text('수정')),
              ],
            ),
            const SizedBox(height: AppSpacing.md),
            const Divider(height: 1),
            _InfoRow(label: '키', value: _formatNumber(profile.heightCm, 'cm')),
            _InfoRow(label: '체중', value: _formatNumber(profile.weightKg, 'kg')),
            _InfoRow(
              label: '평소 한 끼 열량',
              value: _formatNumber(profile.baselineIntake, 'kcal'),
            ),
          ],
        ),
      ),
    );
  }
}

class _InfoRow extends StatelessWidget {
  const _InfoRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppSpacing.md),
      child: Row(
        children: [
          Expanded(child: Text(label, style: AppTypography.bodySecondary)),
          Text(value, style: AppTypography.cardTitle),
        ],
      ),
    );
  }
}

/// `78.4` → `78.4kg`, `175.0` → `175cm`. 값이 없으면 `-`.
String _formatNumber(double? value, String unit) {
  if (value == null) return '-';
  final text = value % 1 == 0 ? value.toStringAsFixed(0) : '$value';
  return '$text$unit';
}
