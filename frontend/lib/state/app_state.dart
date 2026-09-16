import 'package:flutter/foundation.dart';

/// 여러 화면이 함께 보는 앱 전역 상태.
///
/// **Provider 컨벤션**
/// - 화면 하나에서만 쓰는 상태는 그 화면의 `StatefulWidget`/`setState` 로 충분하다.
///   전역 상태로 올리지 않는다.
/// - 화면 2개 이상이 같이 봐야 하는 상태만 이렇게 `ChangeNotifier` 로 만들어
///   `main.dart` 의 `MultiProvider` 에 등록한다.
/// - 각 기능별로 이 파일처럼 별도 `ChangeNotifier` 클래스를 만든다
///   (예: `MealState`, `AuthState`). 하나의 거대한 상태 클래스로 합치지 않는다.
///
/// 지금은 실제 전역 상태가 아직 없어(로그인·오늘의 식사 데이터 등은 API 명세 확정 후)
/// 사용법을 보여주는 예시로만 존재한다. 실제 기능을 추가할 때 이 파일을 참고해
/// 새 `ChangeNotifier` 를 만들고, 이 예시는 지워도 된다.
class AppState extends ChangeNotifier {
  int _exampleCounter = 0;

  int get exampleCounter => _exampleCounter;

  void increment() {
    _exampleCounter++;
    notifyListeners();
  }
}
