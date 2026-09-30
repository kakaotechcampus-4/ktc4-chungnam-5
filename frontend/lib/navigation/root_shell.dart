import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/user_api.dart';
import '../popups/popup_gate.dart';
import '../screens/long_term_feedback_screen.dart';
import '../screens/home_screen.dart';
import '../screens/medication_info_screen.dart';
import '../screens/my_screen.dart';
import '../screens/meal_history_screen.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';

/// 하단 탭 4개(홈·피드백·기록·마이)를 오가는 앱 루트 셸.
/// Figma `hOxrHBitBpjwIBBg2GO49y` node 96:46 (탭바) 기준 구성.
/// 탭 인덱스는 `TabState`(Provider)로 관리한다 — 화면 밖(알림 진입 등)에서도
/// `context.read<TabState>().setIndex(...)` 로 탭을 바꿀 수 있어야 하기 때문.
///
/// 앱 시작(첫 프레임 뒤)과 백그라운드 복귀 때 하루 한 번 팝업을 `PopupGate`
/// 에 요청한다. 띄울지·언제 띄울지는 `PopupGate` 가 정한다.
class RootShell extends StatefulWidget {
  const RootShell({super.key});

  @override
  State<RootShell> createState() => _RootShellState();
}

class _RootShellState extends State<RootShell> with WidgetsBindingObserver {
  /// 온보딩에서 투약을 저장하고 돌아오면 바꿔서 홈을 새로 만든다 — 홈은
  /// 온보딩 화면 아래에서 이미 "투약 미등록"으로 불러와 둔 상태다.
  Key _homeKey = UniqueKey();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _onFirstFrame());
  }

  /// 온보딩이 남았으면 먼저 끝내고, 그다음 하루 팝업을 확인한다.
  Future<void> _onFirstFrame() async {
    await _continueOnboarding();
    _requestDailyPopups();
  }

  /// 프로필만 저장하고 투약 정보를 아직 안 넣었으면(`MEDICATION_REQUIRED`)
  /// 투약 입력으로 이어 보낸다. 백엔드 온보딩 순서가 프로필 → 투약이라
  /// 프로필 화면이 아니라 여기서 확인한다(프로필 저장 즉시 이 화면으로 바뀜).
  /// 확인에 실패하면 막지 않는다 — 홈 투약 카드로도 들어갈 수 있다.
  Future<void> _continueOnboarding() async {
    final String status;
    try {
      final me = await UserApiService(context.read<ApiClient>()).fetchMe();
      status = me.onboardingStatus;
    } catch (_) {
      return;
    }
    if (!mounted || status != 'MEDICATION_REQUIRED') return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => const MedicationInfoScreen()),
    );
    if (mounted) setState(() => _homeKey = UniqueKey());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _requestDailyPopups();
  }

  void _requestDailyPopups() {
    if (!mounted) return;
    context.read<PopupGate>().showDailyPopupsIfDue(context);
  }

  @override
  Widget build(BuildContext context) {
    final selectedIndex = context.watch<TabState>().currentIndex;
    return Scaffold(
      body: IndexedStack(
        index: selectedIndex,
        children: [
          HomeScreen(key: _homeKey),
          const LongTermFeedbackScreen(),
          const MealHistoryScreen(),
          const MyScreen(),
        ],
      ),
      bottomNavigationBar: DecoratedBox(
        // 디자인 가이드 §5: 탭바 상단은 그림자가 아니라 1px 테두리로 구분한다.
        decoration: const BoxDecoration(
          border: Border(top: BorderSide(color: AppColors.border)),
        ),
        child: BottomNavigationBar(
          currentIndex: selectedIndex,
          onTap: (index) => context.read<TabState>().setIndex(index),
          items: const [
            BottomNavigationBarItem(
              icon: Icon(Icons.home_outlined),
              activeIcon: Icon(Icons.home),
              label: '홈',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.chat_bubble_outline),
              activeIcon: Icon(Icons.chat_bubble),
              label: '피드백',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.receipt_long_outlined),
              activeIcon: Icon(Icons.receipt_long),
              label: '기록',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.person_outline),
              activeIcon: Icon(Icons.person),
              label: '마이',
            ),
          ],
        ),
      ),
    );
  }
}
