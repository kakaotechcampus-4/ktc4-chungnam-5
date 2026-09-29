import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 더미 모드(`ApiConfig.useRealApi == false`)에서 서버 대신 값을 기억하는 곳.
///
/// 더미 응답이 고정 예시만 돌려주면 회원가입에서 넣은 값이 어디에도 안
/// 보이고, 온보딩(프로필 → 투약)도 이어지지 않는다. 그래서 프로필·투약
/// 저장만 기기에 남겨 서버처럼 돌려준다. 실제 모드에서는 쓰지 않는다.
///
/// 저장된 게 없으면(회원가입을 거치지 않은 기존 테스트 사용자) 각 서비스의
/// 고정 예시를 그대로 쓴다.
class DummyStore {
  DummyStore._();

  static const _profileKey = 'dummy.profile';
  static const _medicationKey = 'dummy.medication';

  /// 투약 "미등록"을 표시하는 값. 키가 없는 것(= 고정 예시 사용)과 구분한다.
  static const _notRegistered = 'none';

  static Future<Map<String, dynamic>?> readProfile() async {
    final raw = (await SharedPreferences.getInstance()).getString(_profileKey);
    return raw == null ? null : jsonDecode(raw) as Map<String, dynamic>;
  }

  static Future<void> writeProfile(Map<String, dynamic> profile) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_profileKey, jsonEncode(profile));
  }

  /// 새 사용자다 — 투약은 아직 없다.
  static Future<void> markMedicationNotRegistered() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_medicationKey, _notRegistered);
  }

  /// `(저장된 적 있음, 값)`. 값이 null 이면 미등록이다.
  static Future<(bool, Map<String, dynamic>?)> readMedication() async {
    final raw = (await SharedPreferences.getInstance()).getString(
      _medicationKey,
    );
    if (raw == null) return (false, null);
    if (raw == _notRegistered) return (true, null);
    return (true, jsonDecode(raw) as Map<String, dynamic>);
  }

  static Future<void> writeMedication(Map<String, dynamic> medication) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_medicationKey, jsonEncode(medication));
  }
}
