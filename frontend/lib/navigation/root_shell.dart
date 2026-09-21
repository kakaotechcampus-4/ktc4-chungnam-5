import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../popups/daily_condition_popup.dart';
import '../popups/popup_gate.dart';
import '../screens/long_term_feedback_screen.dart';
import '../screens/home_screen.dart';
import '../screens/my_screen.dart';
import '../screens/meal_history_screen.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';

/// 하단 탭 4개(홈·피드백·기록·마이)를 오가는 앱 루트 셸.
/// Figma `hOxrHBitBpjwIBBg2GO49y` node 96:46 (탭바) 기준 구성.
/// 탭 인덱스는 `TabState`(Provider)로 관리한다 — 화면 밖(알림 진입 등)에서도
/// `context.read<TabState>().setIndex(...)` 로 탭을 바꿀 수 있어야 하기 때문.
///
/// 앱 진입 팝업도 여기서만 띄운다. 앱 시작(첫 프레임 뒤)과 백그라운드에서
/// 복귀할 때 [_maybeShowPopups] 가 오늘 띄울 팝업이 있는지 확인한다.
class RootShell extends StatefulWidget {
  const RootShell({super.key});

  @override
  State<RootShell> createState() => _RootShellState();
}

class _RootShellState extends State<RootShell> with WidgetsBindingObserver {
  static const List<Widget> _screens = [
    HomeScreen(),
    LongTermFeedbackScreen(),
    MealHistoryScreen(),
    MyScreen(),
  ];

  final PopupGate _popupGate = PopupGate();

  /// 팝업이 떠 있는 동안 복귀 이벤트가 또 와도 겹쳐 띄우지 않는다.
  bool _popupShowing = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _maybeShowPopups());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _maybeShowPopups();
  }

  /// 오늘 띄울 팝업을 순서대로 하나씩 띄운다. 팝업이 늘어나면 여기에 추가한다.
  Future<void> _maybeShowPopups() async {
    if (!mounted || _popupShowing) return;
    // 식사 기록 흐름 등 다른 화면이 위에 떠 있으면 방해하지 않는다.
    // 다음 복귀 때 다시 확인한다.
    if (ModalRoute.of(context)?.isCurrent == false) return;

    if (!_popupGate.isCompletedToday(PopupGate.dailyCondition)) {
      _popupShowing = true;
      final saved = await showDailyConditionPopup(context);
      _popupShowing = false;
      // "나중에"·닫기는 완료로 치지 않는다 — 다음 접속 때 다시 뜬다.
      if (saved) _popupGate.markCompletedToday(PopupGate.dailyCondition);
    }
  }

  @override
  Widget build(BuildContext context) {
    final selectedIndex = context.watch<TabState>().currentIndex;
    return Scaffold(
      body: IndexedStack(index: selectedIndex, children: _screens),
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
