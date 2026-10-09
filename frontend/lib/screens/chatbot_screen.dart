import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// AI 탭 — AI 코치(챗봇) 화면. 아직 구현 전이라 뼈대만 둔다.
class ChatbotScreen extends StatelessWidget {
  const ChatbotScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AppSpacing.screenHorizontal,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SizedBox(height: AppSpacing.md),
              Text('AI 코치', style: AppTypography.screenTitle),
              Expanded(
                child: Center(
                  child: Text('준비 중이에요', style: AppTypography.body),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
