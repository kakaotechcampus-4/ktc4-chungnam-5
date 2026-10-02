import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/user_api.dart';
import 'user_session.dart';

/// 내 프로필(`GET /users/me`)을 한 곳에 들고 있는 전역 상태.
///
/// 마이 탭 · 컨디션 팝업(체중 초기값) · RootShell(온보딩 확인)이 같은 값을
/// 본다. 화면은 [current] 를 바로 읽고, 서버에 다시 묻는 건 [refresh] 뿐이다.
///
/// 프로필은 다른 API 로도 바뀐다 — 컨디션 기록(`POST /user-states`)은 체중을,
/// 투약 등록(`POST /medications`)은 `onboardingStatus` 를 바꾼다. 그 저장이
/// 끝난 화면이 [refreshInBackground] 를 불러 서버 값으로 맞춘다(FE 가 서버
/// 규칙을 따라 계산하지 않는다).
///
/// 사용자가 바뀌면(세션 삭제 후 재가입) 이전 사용자 값을 버린다.
class ProfileState extends ChangeNotifier {
  ProfileState(this._api, {required UserSession session})
    : _session = session,
      _userId = session.userId {
    session.addListener(_onSessionChanged);
  }

  final UserApiService _api;
  final UserSession _session;
  String? _userId;

  UserProfile? _current;
  Future<UserProfile>? _inFlight;

  /// 아직 받지 않았으면 null.
  UserProfile? get current => _current;

  /// 받은 적 없을 때만 서버에 묻는다. 화면을 열 때 부른다.
  Future<UserProfile> ensureLoaded() {
    final current = _current;
    return current != null ? Future.value(current) : refresh();
  }

  /// 서버에서 다시 받는다. 여러 화면이 동시에 불러도 요청은 하나다.
  Future<UserProfile> refresh() =>
      _inFlight ??= _fetch().whenComplete(() => _inFlight = null);

  /// 결과를 기다리지 않는 [refresh]. 실패해도 지금 값을 그대로 둔다 —
  /// 다른 저장이 이미 끝난 뒤라 그 화면에 오류를 띄울 일이 아니다.
  void refreshInBackground() {
    if (_session.userId == null) return;
    unawaited(refresh().then((_) {}, onError: (_) {}));
  }

  Future<UserProfile> _fetch() async {
    final userId = _session.userId;
    final profile = await _api.fetchMe();
    // 받는 사이에 사용자가 바뀌었으면 이 응답은 버린다.
    if (userId == _session.userId) _set(profile);
    return profile;
  }

  /// `POST /users/profile` — 회원가입. 응답의 `userId` 를 세션에 저장한다.
  /// 응답이 곧 프로필이라 바로 이어지는 화면(온보딩 확인)은 서버에 다시
  /// 묻지 않는다.
  Future<UserProfile> create({
    required String nickname,
    required double heightCm,
    required double weightKg,
    required double baselineIntake,
  }) async {
    final profile = await _api.createProfile(
      nickname: nickname,
      heightCm: heightCm,
      weightKg: weightKg,
      baselineIntake: baselineIntake,
    );
    await _session.setUserId(profile.userId);
    _set(profile);
    return profile;
  }

  /// `PATCH /users/me` 후 응답으로 바로 바꾼다. 규칙은
  /// [UserApiService.updateProfile] 참고.
  Future<UserProfile> update({
    String? nickname,
    double? heightCm,
    double? weightKg,
    double? baselineIntake,
  }) async {
    final profile = await _api.updateProfile(
      nickname: nickname,
      heightCm: heightCm,
      weightKg: weightKg,
      baselineIntake: baselineIntake,
    );
    _set(profile);
    return profile;
  }

  void _set(UserProfile profile) {
    _current = profile;
    notifyListeners();
  }

  void _onSessionChanged() {
    if (_session.userId == _userId) return;
    _userId = _session.userId;
    _current = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _session.removeListener(_onSessionChanged);
    super.dispose();
  }
}
