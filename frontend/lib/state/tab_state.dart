import 'package:flutter/foundation.dart';

/// [RootShell] 하단 탭 인덱스를 화면 밖(알림 진입 등)에서도 바꿀 수 있도록
/// 분리한 전역 상태. 멘토 리뷰(PR #9) 피드백 반영 — navigatorKey는
/// push/pop만 다루고 RootShell 내부 탭 상태까지는 못 바꾸므로 Provider로 뺐다.
class TabState extends ChangeNotifier {
  int _currentIndex = 0;

  int get currentIndex => _currentIndex;

  void setIndex(int index) {
    if (_currentIndex == index) return;
    _currentIndex = index;
    notifyListeners();
  }
}
