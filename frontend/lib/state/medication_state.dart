import 'package:flutter/foundation.dart';

import '../api/api_exception.dart';
import '../api/medication_api.dart';
import 'user_session.dart';

/// 현재 투약 정보(`GET /medications/current`)를 한 곳에 들고 있는 전역 상태.
///
/// 홈 투약 카드 · 컨디션 팝업 헤더 · 투약 화면이 같은 값을 본다. 투약 화면에서
/// 저장하면 여기 값이 바뀌고, 보고 있던 화면은 다시 조회하지 않아도 같이
/// 바뀐다. 화면은 [current] 를 바로 읽고, 서버에 다시 묻는 건 [refresh] 뿐이다.
///
/// 회차·다음 투약일은 날짜가 지나면 바뀌므로, 받은 날이 오늘이 아니면 다시 받는다
/// (앱을 켜 둔 채 다음 날 돌아온 경우). 사용자가 바뀌면(세션 삭제 후 재가입)
/// 이전 사용자 값을 버린다.
class MedicationState extends ChangeNotifier {
  MedicationState(
    this._api, {
    required UserSession session,
    this.clock = DateTime.now,
  }) : _session = session,
       _userId = session.userId {
    session.addListener(_onSessionChanged);
  }

  final MedicationApiService _api;
  final UserSession _session;

  /// 테스트가 날짜를 넘길 때만 바꾼다.
  @visibleForTesting
  final DateTime Function() clock;
  String? _userId;

  MedicationCurrent? _current;
  bool _loaded = false;

  /// 마지막으로 받은 날(로컬 날짜).
  DateTime? _loadedOn;
  Future<MedicationCurrent?>? _inFlight;

  /// null 이면 미등록이다 — 단 [isLoaded] 가 true 일 때만.
  MedicationCurrent? get current => _current;

  /// 서버에서 한 번이라도 받았는지. false 면 [current] 가 null 이어도
  /// "미등록"이 아니라 "아직 모름"이다.
  bool get isLoaded => _loaded;

  /// 오늘 받은 값이 없을 때만 서버에 묻는다. 화면을 열 때 부른다.
  Future<MedicationCurrent?> ensureLoaded() =>
      _loaded && _loadedOn == _today ? Future.value(_current) : refresh();

  DateTime get _today {
    final now = clock();
    return DateTime(now.year, now.month, now.day);
  }

  /// 서버에서 다시 받는다. 여러 화면이 동시에 불러도 요청은 하나다.
  Future<MedicationCurrent?> refresh() =>
      _inFlight ??= _fetch().whenComplete(() => _inFlight = null);

  Future<MedicationCurrent?> _fetch() async {
    final userId = _session.userId;
    final current = await _api.fetchCurrent();
    // 받는 사이에 사용자가 바뀌었으면 이 응답은 버린다.
    if (userId != _session.userId) return _current;
    _set(current);
    return current;
  }

  /// `POST /medications` 후 응답으로 바로 바꾼다. 규칙은
  /// [MedicationApiService.save] 참고.
  Future<MedicationCurrent> save({
    required String drugName,
    required double doseMg,
    DateTime? startedAt,
  }) async {
    final saved = await _api.save(
      drugName: drugName,
      doseMg: doseMg,
      startedAt: startedAt,
    );
    _set(saved);
    return saved;
  }

  /// 시작일을 정정할 수 있는지. 용량을 바꾼 적이 있으면 현재 기록의
  /// 시작일이 "투약 시작일"이 아니라서 막는다.
  Future<bool> canCorrectStart() async => await _api.fetchDoseEventCount() <= 1;

  /// `PATCH /medications/{id}` 로 시작일을 고치고 다시 받는다.
  Future<MedicationCurrent?> correctStart(DateTime startedAt) async {
    await _api.correct(_requireCurrent().medicationId, startedAt: startedAt);
    return refresh();
  }

  /// 등록 후 약·용량을 바꾼다. 보통은 "오늘부터 이 용량" 변경(`POST`)이다.
  ///
  /// 단, **오늘 연 기록을 오늘 또 바꾸면** 서버가 409 `CONFLICT` 로 막는다 —
  /// 같은 날 들어온 다른 값은 "정말 바꿨다"가 아니라 "잘못 넣은 걸 고친다"로
  /// 봐서다(BE `services/medication.py` `register`). 그때는 같은 값을 정정
  /// (`PATCH`)으로 보낸다. 등록 후에는 시작일을 `POST` 로 보내지 않으므로
  /// 이 409 는 "오늘 연 기록"뿐이다.
  ///
  /// 정정했으면 true.
  Future<bool> changeDose({
    required String drugName,
    required double doseMg,
  }) async {
    try {
      await save(drugName: drugName, doseMg: doseMg);
      return false;
    } on ApiException catch (e) {
      if (e.code != 'CONFLICT') rethrow;
      await _api.correct(
        _requireCurrent().medicationId,
        drugName: drugName,
        doseMg: doseMg,
      );
      await refresh();
      return true;
    }
  }

  MedicationCurrent _requireCurrent() =>
      _current ?? (throw StateError('등록된 투약이 없어요'));

  void _set(MedicationCurrent? current) {
    _current = current;
    _loaded = true;
    _loadedOn = _today;
    notifyListeners();
  }

  void _onSessionChanged() {
    if (_session.userId == _userId) return;
    _userId = _session.userId;
    _current = null;
    _loaded = false;
    _loadedOn = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _session.removeListener(_onSessionChanged);
    super.dispose();
  }
}
