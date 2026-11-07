// 웹 평가용 창구의 데모 키 처리. 웹에서만 하는 일이라 조건부로 가져온다 — 앱 빌드는
// 빈 함수를 쓰고 `package:web` 을 끌어오지 않는다.
//
// 평가자 링크 `https://<도메인>/web/?k=<키>` 를 열면 시작할 때
// 1. `k` 를 기기(localStorage)에 저장하고,
// 2. 주소창에서 `k` 를 지우고(화면이나 북마크로 키가 새지 않게),
// 3. 이후 요청에는 저장된 키를 쓴다(`ApiConfig.runtimeDemoKey`).
export 'web_demo_key_stub.dart'
    if (dart.library.js_interop) 'web_demo_key_web.dart';
