import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'navigation/root_shell.dart';
import 'screens/profile_input_screen.dart';
import 'state/app_state.dart';
import 'state/tab_state.dart';
import 'state/user_session.dart';
import 'theme/app_theme.dart';

Future<void> main() async {
  // 첫 화면을 고르려면 저장된 userId 를 먼저 읽어야 한다.
  WidgetsFlutterBinding.ensureInitialized();
  final session = await UserSession.load();
  runApp(MyApp(session: session));
}

class MyApp extends StatelessWidget {
  const MyApp({super.key, required this.session});

  final UserSession session;

  @override
  Widget build(BuildContext context) {
    // 여러 화면이 함께 보는 상태는 여기에 Provider 로 등록한다.
    // 기능별 ChangeNotifier 를 추가할 때마다 항목을 늘린다 (lib/state/app_state.dart 참고).
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: session),
        Provider(create: (_) => ApiClient(session: session)),
        ChangeNotifierProvider(create: (_) => AppState()),
        ChangeNotifierProvider(create: (_) => TabState()),
      ],
      child: MaterialApp(
        // TODO: 알림 기능 착수 시 navigatorKey(GlobalKey<NavigatorState>) 추가.
        // cold-start 알림 딥링크에서 BuildContext 없이 Navigator/TabState에
        // 접근해야 할 때 필요 (멘토 리뷰 PR #9 논의, 지금은 쓰는 곳이 없어 보류).
        title: 'GLP-1 식사 코치',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light,
        home: const _StartScreen(),
      ),
    );
  }
}

/// 저장된 사용자가 없으면 프로필 입력(온보딩), 있으면 탭 화면.
/// 프로필을 저장하면 [UserSession] 이 바뀌어 자동으로 탭 화면으로 넘어간다.
class _StartScreen extends StatelessWidget {
  const _StartScreen();

  @override
  Widget build(BuildContext context) {
    final hasProfile = context.select<UserSession, bool>((s) => s.hasProfile);
    return hasProfile ? const RootShell() : const ProfileInputScreen();
  }
}
