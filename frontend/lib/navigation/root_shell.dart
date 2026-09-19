import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

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
class RootShell extends StatelessWidget {
  const RootShell({super.key});

  static const List<Widget> _screens = [
    HomeScreen(),
    LongTermFeedbackScreen(),
    MealHistoryScreen(),
    MyScreen(),
  ];

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
