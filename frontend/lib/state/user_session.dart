import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 지금 사용자가 누구인지(`userId`)를 기기에 저장해 두는 전역 상태.
///
/// 백엔드는 JWT 전까지 `X-User-Id` 헤더로 사용자를 식별한다
/// (`backend/app/core/deps.py`). `POST /users/profile` 이 돌려준 `userId` 를
/// 여기 저장하고, `ApiClient` 가 요청마다 헤더로 붙인다.
///
/// `userId` 가 없으면 프로필 입력(온보딩)부터 시작한다 — `main.dart` 참고.
/// JWT 가 붙으면 저장 대상이 토큰으로 바뀐다.
class UserSession extends ChangeNotifier {
  UserSession._(this._prefs) : _userId = _prefs.getString(_userIdKey);

  static const _userIdKey = 'session.userId';

  final SharedPreferences _prefs;
  String? _userId;

  /// 앱 시작 때 한 번 부른다. 저장된 `userId` 를 읽어 온다.
  static Future<UserSession> load() async =>
      UserSession._(await SharedPreferences.getInstance());

  String? get userId => _userId;

  bool get hasProfile => _userId != null;

  Future<void> setUserId(String userId) async {
    await _prefs.setString(_userIdKey, userId);
    _userId = userId;
    notifyListeners();
  }

  /// 저장된 사용자를 지운다. 프로필 입력부터 다시 시작한다.
  Future<void> clear() async {
    await _prefs.remove(_userIdKey);
    _userId = null;
    notifyListeners();
  }
}
