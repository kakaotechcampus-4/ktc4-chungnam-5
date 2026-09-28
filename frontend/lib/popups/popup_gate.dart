import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../theme/app_colors.dart';
import 'daily_condition_popup.dart';
import 'satiety_checkin_popup.dart';

/// 앱의 팝업. [priority] 가 작을수록 먼저 띄운다.
enum AppPopup {
  /// 사후 포만감 체크인. 게이지 탭·알림처럼 사용자가 특정 끼니를 골라 연
  /// 요청이라 하루 한 번 팝업보다 먼저 띄운다.
  satietyCheckin(0),

  /// 하루 한 번, 오늘 첫 접속 때 뜨는 컨디션 기록 팝업.
  dailyCondition(1);

  const AppPopup(this.priority);

  final int priority;
}

/// 팝업을 띄우는 유일한 통로. FE-10 멘토 리뷰 반영 — 팝업을 띄우는 일,
/// 우선순위, "이미 떠 있는지" 판단을 여기 한 곳에서 한다.
///
/// - **한 번에 하나만 띄운다.** 떠 있는 동안 들어온 요청은 기다렸다가 닫히면
///   [AppPopup.priority] 순서로 띄운다. 이미 떠 있는 팝업을 밀어내지는 않는다.
///   같은 프레임에 들어온 요청(앱 시작 때 하루 팝업 + 알림 진입 팝업)은
///   한데 모아 우선순위대로 줄 세운다.
/// - **같은 요청은 겹치지 않는다.** 같은 팝업(포만감은 같은 `mealId`)이 이미
///   떠 있거나 기다리는 중이면 새로 쌓지 않고 그 결과를 같이 받는다.
/// - **하루 한 번 팝업**은 "오늘 완료했는지"를 기기에 기억한다. 완료 =
///   사용자가 끝까지 저장한 경우만. "나중에"·닫기는 다음 접속(앱 시작·복귀)
///   때 다시 뜬다.
/// - **팝업이 실패해도 앱은 멈추지 않는다.** 에러는 보고만 하고 `false` 로
///   끝낸다(띄우지 못함 = 저장 안 함).
///
/// 팝업 위젯 파일에는 `showDialog` 를 두지 않는다 — 새 팝업은 [AppPopup] 에
/// 항목을 추가하고 여기에 `show…` 메서드를 만든다. `main.dart` 에서 Provider
/// 로 하나만 만들어 공유한다(인스턴스가 여러 개면 잠금이 나뉜다).
class PopupGate {
  final List<_PopupRequest> _pending = [];
  _PopupRequest? _current;
  bool _drainScheduled = false;

  @visibleForTesting
  bool get isShowing => _current != null;

  /// 사후 포만감 체크인. 저장했으면 `true`.
  ///
  /// 진입점은 [mealId] 하나뿐이다(멘토 리뷰 PR #9 "알림 진입 화면은 id 로
  /// 생성"). 홈 게이지 탭과 알림(`NotificationRouter`, FE-15) 모두 이걸 부른다.
  Future<bool> showSatietyCheckin(
    BuildContext context, {
    required String mealId,
  }) {
    return _request(
      context,
      AppPopup.satietyCheckin,
      id: mealId,
      show: (c) => _showDialog(c, SatietyCheckinPopup(mealId: mealId)),
    );
  }

  /// 오늘 띄울 하루 한 번 팝업을 요청한다. `RootShell` 이 앱 시작(첫 프레임
  /// 뒤)과 백그라운드 복귀 때 부른다. 하루 한 번 팝업이 늘어나면 여기에
  /// 추가한다.
  void showDailyPopupsIfDue(BuildContext context) {
    _request(
      context,
      AppPopup.dailyCondition,
      show: (c) async {
        if (await _isCompletedToday(AppPopup.dailyCondition)) return false;
        // 식사 기록 흐름 등 다른 화면이 위에 떠 있으면 방해하지 않는다.
        // 다음 복귀 때 다시 확인한다.
        if (!c.mounted || ModalRoute.of(c)?.isCurrent == false) return false;
        final saved = await _showDialog(c, const DailyConditionPopup());
        if (saved) await markCompletedToday(AppPopup.dailyCondition);
        return saved;
      },
    );
  }

  // ── 하루 한 번 완료 기록 ───────────────────────────────────

  static String _storageKey(AppPopup popup) =>
      'popup.${popup.name}.completedOn';

  Future<bool> _isCompletedToday(AppPopup popup) async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_storageKey(popup)) == _todayKey();
  }

  @visibleForTesting
  Future<void> markCompletedToday(AppPopup popup) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_storageKey(popup), _todayKey());
  }

  /// 기기 로컬 날짜의 `YYYY-MM-DD`. UTC 로 바꾸면 한국 시간 자정~오전 9시
  /// 사이에 날짜가 하루 어긋난다.
  static String _todayKey() {
    final now = DateTime.now();
    return '${now.year.toString().padLeft(4, '0')}-'
        '${now.month.toString().padLeft(2, '0')}-'
        '${now.day.toString().padLeft(2, '0')}';
  }

  // ── 줄 세우기 ───────────────────────────────────────────────

  /// [show] 는 띄울 조건까지 스스로 확인하고, 띄우지 않으면 `false` 를 낸다.
  Future<bool> _request(
    BuildContext context,
    AppPopup popup, {
    String? id,
    required Future<bool> Function(BuildContext context) show,
  }) {
    for (final r in [?_current, ..._pending]) {
      if (r.popup == popup && r.id == id) return r.completer.future;
    }
    final request = _PopupRequest(popup, id, context, show);
    _pending.add(request);
    // 같은 프레임의 요청을 모은 뒤 한 번에 줄 세운다.
    if (!_drainScheduled) {
      _drainScheduled = true;
      scheduleMicrotask(_drain);
    }
    return request.completer.future;
  }

  Future<void> _drain() async {
    _drainScheduled = false;
    if (_current != null) return;
    while (_pending.isNotEmpty) {
      final request = _current = _takeHighestPriority();
      var result = false;
      try {
        if (request.context.mounted) {
          result = await request.show(request.context);
        }
      } catch (e, s) {
        FlutterError.reportError(
          FlutterErrorDetails(
            exception: e,
            stack: s,
            library: 'popup_gate',
            context: ErrorDescription('while showing ${request.popup.name}'),
          ),
        );
      }
      _current = null;
      request.completer.complete(result);
    }
  }

  /// 우선순위가 가장 높은 요청을 꺼낸다. 같으면 먼저 온 것.
  _PopupRequest _takeHighestPriority() {
    var best = 0;
    for (var i = 1; i < _pending.length; i++) {
      if (_pending[i].popup.priority < _pending[best].popup.priority) best = i;
    }
    return _pending.removeAt(best);
  }

  /// 팝업 공용 다이얼로그. 저장했으면 `true`, "나중에"·닫기·바깥 탭이면 `false`.
  static Future<bool> _showDialog(BuildContext context, Widget popup) async {
    final saved = await showDialog<bool>(
      context: context,
      barrierColor: AppColors.overlay,
      builder: (_) => popup,
    );
    return saved ?? false;
  }
}

class _PopupRequest {
  _PopupRequest(this.popup, this.id, this.context, this.show);

  final AppPopup popup;
  final String? id;
  final BuildContext context;
  final Future<bool> Function(BuildContext context) show;
  final Completer<bool> completer = Completer<bool>();
}
