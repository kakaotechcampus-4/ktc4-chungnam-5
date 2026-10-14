import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';

/// 빠른 질문 — 입력창 위에 놓는 고정 문구(Figma 화면 12 · AI 코치).
const List<String> _quickQuestions = [
  '오늘 저녁 메뉴 추천해줘',
  '내 QQS 점수 어때?',
  '장기 피드백 보고싶어',
];

/// 추천 카드 아래 칩의 종류. 채우는 영양은 초록, 줄이는 양은 빨강2 계열이다.
enum _TagKind { more, less }

/// AI 답변 안의 추천 카드 한 장.
class _Suggestion {
  const _Suggestion({
    required this.title,
    required this.tag,
    required this.kind,
  });

  final String title;
  final String tag;
  final _TagKind kind;
}

class _ChatMessage {
  const _ChatMessage.user(this.text) : fromUser = true, suggestions = const [];

  const _ChatMessage.ai(this.text, {this.suggestions = const []})
    : fromUser = false;

  final bool fromUser;
  final String text;
  final List<_Suggestion> suggestions;
}

/// AI 탭 — AI 코치(챗봇) 화면. Figma 화면 12 · AI 코치(챗봇) 11 · 12.
///
/// 아직 화면만 만든 단계라 서버와 연결하지 않는다. 보낸 말은 화면 안에서만 쌓이고,
/// "＋ 새 대화" 는 쌓인 말을 비운다. 답변은 [_tempReply] 의 임시 문구다.
class ChatbotScreen extends StatefulWidget {
  const ChatbotScreen({super.key});

  @override
  State<ChatbotScreen> createState() => _ChatbotScreenState();
}

class _ChatbotScreenState extends State<ChatbotScreen> {
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();
  final List<_ChatMessage> _messages = [];

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  /// 말을 보낸다. 빈 말은 무시한다.
  void _send(String raw) {
    final text = raw.trim();
    if (text.isEmpty) return;
    setState(() {
      _messages
        ..add(_ChatMessage.user(text))
        ..add(_tempReply(text));
    });
    _input.clear();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  void _newChat() {
    _input.clear();
    setState(_messages.clear);
  }

  /// TODO(API): 서버 연결 전 임시 답변. 연결하면 이 함수를 서버 응답으로 바꾼다.
  /// 첫 번째 빠른 질문에만 Figma 의 추천 카드 예시를 보여 준다.
  _ChatMessage _tempReply(String question) {
    if (question == _quickQuestions.first) {
      return const _ChatMessage.ai(
        '저녁엔 이런 음식 어때요?',
        suggestions: [
          _Suggestion(
            title: '쌈채소 한 접시',
            tag: '식이섬유 +5g',
            kind: _TagKind.more,
          ),
          _Suggestion(
            title: '밥 반 공기',
            tag: '양 −120kcal',
            kind: _TagKind.less,
          ),
        ],
      );
    }
    return const _ChatMessage.ai('답변 기능은 아직 연결되지 않았어요.');
  }

  @override
  Widget build(BuildContext context) {
    final empty = _messages.isEmpty;
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(),
            Expanded(child: empty ? _buildGreeting() : _buildConversation()),
            _buildQuickQuestions(empty),
            _buildInput(),
            _buildDisclaimer(),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.md,
        AppSpacing.screenHorizontal - 6,
        0,
      ),
      child: Row(
        children: [
          Text(
            'AI 코치',
            style: AppTypography.screenTitle.copyWith(fontSize: 17),
          ),
          const Spacer(),
          TextButton(
            onPressed: _newChat,
            style: TextButton.styleFrom(
              foregroundColor: AppColors.textTertiary,
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              '＋ 새 대화',
              style: AppTypography.emphasis.copyWith(
                fontSize: 10.5,
                color: AppColors.textTertiary,
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 아직 말이 없을 때: 로고와 첫인사(Figma 11).
  Widget _buildGreeting() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ClipOval(
            child: Image.asset(
              'assets/images/chatbot_logo.png',
              width: 142,
              height: 142,
              fit: BoxFit.cover,
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          Text(
            '안녕하세요 :)',
            style: AppTypography.body.copyWith(
              fontSize: 17,
              height: 1.25,
              color: AppColors.textPrimary,
            ),
          ),
        ],
      ),
    );
  }

  /// 말이 쌓였을 때: 보낸 말은 오른쪽, 답변은 왼쪽(Figma 12).
  Widget _buildConversation() {
    return LayoutBuilder(
      builder: (context, constraints) {
        final bubbleMax = math.min(262.0, (constraints.maxWidth - 24) * 0.75);
        return ListView.separated(
          controller: _scroll,
          padding: const EdgeInsets.fromLTRB(12, AppSpacing.lg, 12, AppSpacing.md),
          itemCount: _messages.length,
          separatorBuilder: (_, _) => const SizedBox(height: 10),
          itemBuilder: (context, i) {
            final m = _messages[i];
            return m.fromUser
                ? _UserBubble(text: m.text, maxWidth: bubbleMax)
                : _AiBubble(message: m, maxWidth: bubbleMax);
          },
        );
      },
    );
  }

  /// 빠른 질문. 처음에는 세로로 쌓이고, 대화가 시작되면 가로 한 줄로 밀린다.
  Widget _buildQuickQuestions(bool vertical) {
    final chips = [
      for (final q in _quickQuestions)
        _QuickChip(label: q, onTap: () => _send(q)),
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: AppSpacing.lg),
      child: vertical
          ? Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.screenHorizontal,
              ),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (var i = 0; i < chips.length; i++) ...[
                      if (i > 0) const SizedBox(height: 6),
                      chips[i],
                    ],
                  ],
                ),
              ),
            )
          : SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.screenHorizontal,
              ),
              child: Row(
                children: [
                  for (var i = 0; i < chips.length; i++) ...[
                    if (i > 0) const SizedBox(width: 6),
                    chips[i],
                  ],
                ],
              ),
            ),
    );
  }

  Widget _buildInput() {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: AppSpacing.screenHorizontal,
      ),
      child: Container(
        height: 52,
        padding: const EdgeInsets.fromLTRB(16, 6, 6, 6),
        decoration: BoxDecoration(
          color: AppColors.surface,
          border: Border.all(color: AppColors.border),
          borderRadius: BorderRadius.circular(26),
        ),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _input,
                textInputAction: TextInputAction.send,
                onSubmitted: _send,
                style: AppTypography.body.copyWith(
                  fontSize: 12,
                  height: 1.25,
                  color: AppColors.textPrimary,
                ),
                decoration: InputDecoration(
                  hintText: '무엇이든 물어보세요',
                  hintStyle: AppTypography.body.copyWith(
                    fontSize: 12,
                    height: 1.25,
                    color: AppColors.textSecondary,
                  ),
                  filled: false,
                  border: InputBorder.none,
                  enabledBorder: InputBorder.none,
                  focusedBorder: InputBorder.none,
                  isDense: true,
                  contentPadding: EdgeInsets.zero,
                ),
              ),
            ),
            const SizedBox(width: AppSpacing.sm),
            Material(
              color: AppColors.primary,
              shape: const CircleBorder(),
              child: InkWell(
                onTap: () => _send(_input.text),
                customBorder: const CircleBorder(),
                child: const SizedBox.square(
                  dimension: 40,
                  child: Icon(
                    Icons.arrow_upward,
                    size: 18,
                    color: AppColors.textInverse,
                    semanticLabel: '보내기',
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildDisclaimer() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        6,
        AppSpacing.screenHorizontal,
        AppSpacing.md,
      ),
      child: Text(
        '용량·중단 등 의료 판단은 답변하지 않고 상담을 안내해요.',
        textAlign: TextAlign.center,
        style: AppTypography.caption.copyWith(
          fontSize: 9.5,
          height: 14 / 9.5,
          color: AppColors.textSecondary,
        ),
      ),
    );
  }
}

/// 빠른 질문 칩.
class _QuickChip extends StatelessWidget {
  const _QuickChip({required this.label, required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final shape = RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(15),
      side: const BorderSide(color: AppColors.border),
    );
    return Material(
      color: AppColors.border,
      shape: shape,
      child: InkWell(
        onTap: onTap,
        customBorder: shape,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
          child: Text(
            label,
            style: AppTypography.body.copyWith(
              fontSize: 10,
              height: 1.25,
              color: AppColors.textBody,
            ),
          ),
        ),
      ),
    );
  }
}

/// 보낸 말(오른쪽, 빨강). 오른쪽 아래 모서리만 각지게 한다.
class _UserBubble extends StatelessWidget {
  const _UserBubble({required this.text, required this.maxWidth});

  final String text;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerRight,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: Container(
          padding: const EdgeInsets.fromLTRB(13, 11, 13, 12),
          decoration: const BoxDecoration(
            color: AppColors.primary,
            borderRadius: BorderRadius.only(
              topLeft: Radius.circular(16),
              topRight: Radius.circular(16),
              bottomLeft: Radius.circular(16),
              bottomRight: Radius.circular(5),
            ),
          ),
          child: Text(
            text,
            style: AppTypography.body.copyWith(
              fontSize: 11.5,
              height: 17 / 11.5,
              color: AppColors.textInverse,
            ),
          ),
        ),
      ),
    );
  }
}

/// AI 답변(왼쪽, 흰 바탕). 왼쪽 아래 모서리만 각지게 하고, 추천 카드가 있으면 아래에 붙인다.
class _AiBubble extends StatelessWidget {
  const _AiBubble({required this.message, required this.maxWidth});

  final _ChatMessage message;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    final cards = message.suggestions;
    return Align(
      alignment: Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: Container(
          // 카드가 있으면 카드가 말풍선 폭을 꽉 채운다.
          width: cards.isEmpty ? null : maxWidth,
          padding: const EdgeInsets.fromLTRB(13, 11, 13, 12),
          decoration: BoxDecoration(
            color: AppColors.surface,
            border: Border.all(color: AppColors.border),
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(16),
              topRight: Radius.circular(16),
              bottomLeft: Radius.circular(5),
              bottomRight: Radius.circular(16),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                message.text,
                style: AppTypography.body.copyWith(
                  fontSize: 11.5,
                  height: 17 / 11.5,
                  color: AppColors.textPrimary,
                ),
              ),
              for (final s in cards) ...[
                const SizedBox(height: 6),
                _SuggestionCard(suggestion: s),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// 추천 카드 — 음식 이름과, 그 음식이 채우거나 줄이는 값을 칩으로 보여 준다.
class _SuggestionCard extends StatelessWidget {
  const _SuggestionCard({required this.suggestion});

  final _Suggestion suggestion;

  @override
  Widget build(BuildContext context) {
    final more = suggestion.kind == _TagKind.more;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.surfaceMuted),
        borderRadius: BorderRadius.circular(11),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            suggestion.title,
            style: AppTypography.cardTitle.copyWith(fontSize: 11),
          ),
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
            decoration: BoxDecoration(
              color: more ? AppColors.qualityBg : AppColors.primaryStrongBg,
              borderRadius: BorderRadius.circular(9),
            ),
            child: Text(
              suggestion.tag,
              style: AppTypography.emphasis.copyWith(
                fontSize: 9,
                color: more ? AppColors.quality : AppColors.primaryStrong,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
