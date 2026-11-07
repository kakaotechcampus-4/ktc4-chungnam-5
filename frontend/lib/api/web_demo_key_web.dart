import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web/web.dart' as web;

import 'api_client.dart';

/// 저장소 키. `shared_preferences` 가 웹에서 `flutter.` 를 앞에 붙여 localStorage 에 둔다.
const _storageKey = 'demo.key';

/// 링크의 `?k=` 를 저장하고 주소창에서 지운 뒤, 저장된 키를 [ApiConfig.runtimeDemoKey] 에 둔다.
///
/// 링크에 키가 없으면(저장해 둔 브라우저로 `/web/` 만 다시 연 경우) 저장된 값을 그대로 쓴다.
/// 키가 아예 없으면 비워 둔다 — API 가 403 을 주고 화면은 "평가용 링크로 접속해 주세요" 를 보인다.
Future<void> setupWebDemoKey() async {
  final prefs = await SharedPreferences.getInstance();
  final fromLink = Uri.base.queryParameters['k'];
  if (fromLink != null && fromLink.isNotEmpty) {
    await prefs.setString(_storageKey, fromLink);
  }
  ApiConfig.runtimeDemoKey = prefs.getString(_storageKey) ?? '';

  // 엔진이 시작하면서 주소창을 원래 주소로 되돌려 놓기도 해서(`main` 안에서 한 번 지우면
  // 다시 나타난다) 첫 프레임 뒤와 잠시 뒤에도 지운다. 현재 주소를 읽어 하므로 여러 번
  // 불러도 안전하다.
  _removeKeyFromAddressBar();
  WidgetsBinding.instance.addPostFrameCallback(
    (_) => _removeKeyFromAddressBar(),
  );
  Future<void>.delayed(const Duration(seconds: 1), _removeKeyFromAddressBar);
}

/// 주소창에서 `k` 만 지운다. 나머지 쿼리와 해시(`/web/#/…`), 엔진이 맡긴 history state 는 그대로 둔다.
void _removeKeyFromAddressBar() {
  final location = web.window.location;
  final search = location.search;
  final params = Uri.splitQueryString(
    search.startsWith('?') ? search.substring(1) : search,
  );
  if (!params.containsKey('k')) return;
  params.remove('k');
  final query = params.isEmpty ? '' : '?${Uri(queryParameters: params).query}';
  web.window.history.replaceState(
    web.window.history.state,
    '',
    '${location.pathname}$query${location.hash}',
  );
}
