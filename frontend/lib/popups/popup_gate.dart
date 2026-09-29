import 'package:shared_preferences/shared_preferences.dart';

/// 팝업별로 "오늘 완료했는지"를 기기에 기억한다.
///
/// 팝업을 띄울지는 `RootShell` 한 곳에서만 판단한다. 팝업이 늘어나면
/// 키를 추가하고 `RootShell._maybeShowPopups` 에 순서대로 넣는다.
///
/// **완료 = 사용자가 끝까지 처리한 경우만.** "나중에"·닫기는 완료가 아니라서
/// 다음 접속(앱 시작·복귀) 때 다시 뜬다.
///
/// 날짜는 기기 로컬 날짜의 `YYYY-MM-DD` 문자열로 비교한다. UTC 로 바꾸면
/// 한국 시간 자정~오전 9시 사이에 날짜가 하루 어긋난다.
class PopupGate {
  /// 하루 한 번, 오늘 첫 접속 때 뜨는 컨디션 기록 팝업.
  static const dailyCondition = 'dailyCondition';

  static String _storageKey(String key) => 'popup.$key.completedOn';

  Future<bool> isCompletedToday(String key, {DateTime? now}) async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_storageKey(key)) == _dateKey(now ?? DateTime.now());
  }

  Future<void> markCompletedToday(String key, {DateTime? now}) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_storageKey(key), _dateKey(now ?? DateTime.now()));
  }

  static String _dateKey(DateTime d) {
    final local = d.toLocal();
    return '${local.year.toString().padLeft(4, '0')}-'
        '${local.month.toString().padLeft(2, '0')}-'
        '${local.day.toString().padLeft(2, '0')}';
  }
}
