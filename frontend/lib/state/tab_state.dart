import 'package:flutter/foundation.dart';

/// [RootShell] 하단 탭 인덱스를 화면 밖(알림 진입 등)에서도 바꿀 수 있도록
/// 분리한 전역 상태. 멘토 리뷰(PR #9) 피드백 반영 — navigatorKey는
/// push/pop만 다루고 RootShell 내부 탭 상태까지는 못 바꾸므로 Provider로 뺐다.
///
/// 소비자가 RootShell 하나뿐이라 `app_state.dart` 의 "화면 2개 이상이 공유할
/// 때만 전역화" 원칙엔 지금 당장은 안 맞지만, 알림 딥링크(향후 두 번째
/// 소비자, `main.dart` 의 navigatorKey TODO 참고)가 BuildContext 없이
/// 탭을 바꿔야 해서 미리 Provider로 뺀 의도적 예외다.
class TabState extends ChangeNotifier {
  /// [RootShell] 하단 탭 개수. 탭이 늘거나 줄면 같이 맞춘다.
  static const tabCount = 4;

  int _currentIndex = 0;

  int get currentIndex => _currentIndex;

  void setIndex(int index) {
    assert(
      index >= 0 && index < tabCount,
      'TabState.setIndex: index($index) must be within 0..${tabCount - 1}',
    );
    if (_currentIndex == index) return;
    _currentIndex = index;
    notifyListeners();
  }
}
