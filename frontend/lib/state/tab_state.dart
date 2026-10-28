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

  // 탭 번호(홈 · 피드백 · AI · 마이). 다시 보일 때 새로 불러오는 화면이 자기 탭을 알아야 한다.
  // 기록(달력)은 피드백 탭 안의 단기 피드백이라 [feedbackKind] 로 구분한다.
  static const home = 0;
  static const feedback = 1;
  static const ai = 2;
  static const my = 3;

  int _currentIndex = 0;
  FeedbackKind _feedbackKind = FeedbackKind.short;
  ChatRequest? _chatRequest;

  int get currentIndex => _currentIndex;

  /// 피드백 탭이 지금 보여줄 화면. 탭을 누르면 뜨는 분기 팝업에서 고른다.
  FeedbackKind get feedbackKind => _feedbackKind;

  /// 끼니 피드백(7번)에서 "챗봇으로 다른 음식 추천받기" 를 누른 마지막 요청.
  /// 챗봇 화면이 한 번 처리하면 같은 요청으로 다시 대화를 열지 않는다.
  /// null 이면 AI 탭을 바로 누른 것뿐이다 — 진행 중인 대화를 이어 본다.
  ChatRequest? get chatRequest => _chatRequest;

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
    if (_currentIndex == feedback && _feedbackKind == kind) return;
    _currentIndex = feedback;
    _feedbackKind = kind;
    notifyListeners();
  }

  /// AI 탭으로 이동하면서 [mealId] 끼니로 대화를 시작하게 한다.
  /// 누를 때마다 새 요청이다 — 그 사이 "＋ 새 대화" 로 다른 끼니가 됐어도 다시 이 끼니로 연다.
  void openChatbot({required String mealId}) {
    _currentIndex = ai;
    _chatRequest = ChatRequest(mealId);
    notifyListeners();
  }
}

/// 특정 끼니로 챗봇을 열어 달라는 요청. 같은 끼니라도 누를 때마다 다른 객체다.
class ChatRequest {
  ChatRequest(this.mealId);

  final String mealId;
}

/// 피드백 탭의 두 갈래. 단기 = 기록(달력), 장기 = 장기 피드백 대시보드.
enum FeedbackKind { short, long }
