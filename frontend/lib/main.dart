import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'api/medication_api.dart';
import 'api/user_api.dart';
import 'navigation/root_shell.dart';
import 'popups/popup_gate.dart';
import 'screens/profile_input_screen.dart';
import 'state/app_state.dart';
import 'state/medication_state.dart';
import 'state/profile_state.dart';
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
  const MyApp({super.key, required this.session, this.dio});

  final UserSession session;

  /// 테스트가 가짜 응답을 넣을 때만 준다. 앱은 `ApiConfig` 주소로 만든다.
  final Dio? dio;

  @override
  Widget build(BuildContext context) {
    // 여러 화면이 함께 보는 상태는 여기에 Provider 로 등록한다.
    // 기능별 ChangeNotifier 를 추가할 때마다 항목을 늘린다 (lib/state/app_state.dart 참고).
    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: session),
        Provider(create: (_) => ApiClient(session: session, dio: dio)),
        ChangeNotifierProvider(
          create: (context) => ProfileState(
            UserApiService(context.read<ApiClient>()),
            session: session,
          ),
        ),
        ChangeNotifierProvider(
          create: (context) => MedicationState(
            MedicationApiService(context.read<ApiClient>()),
            session: session,
          ),
        ),
        ChangeNotifierProvider(create: (_) => AppState()),
        ChangeNotifierProvider(create: (_) => TabState()),
        // 팝업은 전부 이 하나를 거친다 — 여러 개면 "한 번에 하나" 잠금이 나뉜다.
        Provider(create: (_) => PopupGate()),
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
///
/// 반대로 사용 중에 세션이 지워지면(서버에 사용자가 없음 — `ApiClient` 참고)
/// 이 화면만 바뀌고 그 위에 push 한 화면·팝업은 남는다. 그래서 그때는
/// 첫 화면까지 닫고 이유를 알린다.
class _StartScreen extends StatefulWidget {
  const _StartScreen();

  @override
  State<_StartScreen> createState() => _StartScreenState();
}

class _StartScreenState extends State<_StartScreen> {
  late bool _hadProfile = context.read<UserSession>().hasProfile;

  @override
  Widget build(BuildContext context) {
    final hasProfile = context.select<UserSession, bool>((s) => s.hasProfile);
    if (_hadProfile && !hasProfile) _onSessionLost();
    _hadProfile = hasProfile;
    return hasProfile ? const RootShell() : const ProfileInputScreen();
  }

  void _onSessionLost() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      Navigator.of(context).popUntil((route) => route.isFirst);
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('사용자 정보를 찾지 못해 처음부터 다시 시작해요')),
      );
    });
  }
}
