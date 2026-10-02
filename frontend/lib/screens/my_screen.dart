import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/user_api.dart';
import '../state/profile_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'profile_input_screen.dart';
import 'shared_meal_widgets.dart';

/// 마이 탭 — 회원 정보 조회·수정. Figma 노드가 없어 간단히 구성했다.
///
/// 프로필은 [ProfileState] 에서 읽는다(처음 열 때만 `GET /users/me`). 수정은
/// 프로필 입력 화면을 수정 모드로 띄워 `PATCH /users/me` 하고, 그 응답으로
/// [ProfileState] 가 바뀌어 여기도 같이 바뀐다.
class MyScreen extends StatefulWidget {
  const MyScreen({super.key});

  @override
  State<MyScreen> createState() => _MyScreenState();
}

class _MyScreenState extends State<MyScreen> {
  LoadState _state = LoadState.loading;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// [refresh] 면 서버에서 다시 받는다(당겨서 새로고침·재시도).
  Future<void> _load({bool refresh = false}) async {
    final profileState = context.read<ProfileState>();
    if (_state != LoadState.loading) {
      setState(() {
        _state = LoadState.loading;
        _errorMessage = null;
      });
    }
    try {
      await (refresh ? profileState.refresh() : profileState.ensureLoaded());
      if (!mounted) return;
      setState(() => _state = LoadState.ready);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = '불러오는 데 실패했어요: $e';
        _state = LoadState.failed;
      });
    }
  }

  void _openEdit(UserProfile profile) {
    Navigator.of(context).push<UserProfile>(
      MaterialPageRoute(builder: (_) => ProfileInputScreen(initial: profile)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final profile = context.watch<ProfileState>().current;
    // 사용자가 바뀌어 값이 비는 순간(세션 삭제)에도 카드를 그리지 않는다.
    final state = _state == LoadState.ready && profile == null
        ? LoadState.loading
        : _state;
    return SafeArea(
      child: RefreshIndicator(
        onRefresh: () => _load(refresh: true),
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
            switch (state) {
              LoadState.loading => const Center(
                child: CircularProgressIndicator(color: AppColors.primary),
              ),
              LoadState.failed => RetryBlock(
                message: _errorMessage ?? '잠시 후 다시 시도해 주세요',
                onRetry: () => _load(refresh: true),
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
