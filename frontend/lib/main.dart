import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'navigation/root_shell.dart';
import 'state/app_state.dart';
import 'state/tab_state.dart';
import 'theme/app_theme.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    // 여러 화면이 함께 보는 상태는 여기에 Provider 로 등록한다.
    // 기능별 ChangeNotifier 를 추가할 때마다 항목을 늘린다 (lib/state/app_state.dart 참고).
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => AppState()),
        ChangeNotifierProvider(create: (_) => TabState()),
      ],
      child: MaterialApp(
        title: 'GLP-1 식사 코치',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.light,
        home: const RootShell(),
      ),
    );
  }
}
