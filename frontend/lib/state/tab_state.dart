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

  /// 탭 순서: 홈 · 피드백 · AI · 마이.
  static const homeTab = 0;
  static const feedbackTab = 1;
  static const aiTab = 2;
  static const myTab = 3;

  int _currentIndex = 0;
  FeedbackKind _feedbackKind = FeedbackKind.short;

  int get currentIndex => _currentIndex;

  /// 피드백 탭이 지금 보여줄 화면. 탭을 누르면 뜨는 분기 팝업에서 고른다.
  FeedbackKind get feedbackKind => _feedbackKind;

  void setIndex(int index) {
    assert(
      index >= 0 && index < tabCount,
      'TabState.setIndex: index($index) must be within 0..${tabCount - 1}',
    );
    if (_currentIndex == index) return;
    _currentIndex = index;
    notifyListeners();
  }

  /// 피드백 탭으로 이동하면서 [kind] 화면을 보여준다.
  void showFeedback(FeedbackKind kind) {
    if (_currentIndex == feedbackTab && _feedbackKind == kind) return;
    _currentIndex = feedbackTab;
    _feedbackKind = kind;
    notifyListeners();
  }
}

/// 피드백 탭의 두 갈래. 단기 = 기록(달력), 장기 = 장기 피드백 대시보드.
enum FeedbackKind { short, long }
