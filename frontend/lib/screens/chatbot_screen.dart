import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../api/api_exception.dart';
import '../api/chat_api.dart';
import '../api/meal_api.dart';
import '../common/api_format.dart';
import '../common/polling.dart';
import '../popups/popup_gate.dart';
import '../state/tab_state.dart';
import '../theme/app_colors.dart';
import '../theme/app_spacing.dart';
import '../theme/app_typography.dart';
import 'shared_meal_widgets.dart';

/// 빠른 질문 — 입력창 위에 놓는 고정 문구(Figma 화면 12 · AI 코치).
const List<String> _quickQuestions = [
  '오늘 저녁 메뉴 추천해줘',
  '내 QQS 점수 어때?',
  '장기 피드백 보고싶어',
];

/// AI 탭 — AI 코치(챗봇) 화면. Figma 화면 12 · AI 코치(챗봇) 11 · 12.
///
/// 대화는 서버에 있고 사용자당 하나만 열려 있다(챗봇 API A-1 ~ A-4).
/// - AI 탭을 처음 열면 진행 중인 대화를 이어 본다. 없으면 새로 연다.
/// - 끼니 피드백(7번)에서 오면([TabState.chatRequest]) 그 끼니로 새 대화를 연다.
///   서버가 그 끼니의 추천 카드를 첫 말로 넣어 준다. 이미 그 끼니 대화면 이어 본다.
/// - "＋ 새 대화" 는 끼니를 정하지 않고 연다 — 서버가 최근 확정 끼니로 정한다.
/// - 보낸 말의 답은 비동기다(202). 답이 `READY`·`FAILED` 가 될 때까지 대화를 다시
///   조회하고, 그동안은 새 말을 보내지 않는다(서버도 409 로 막는다).
///
/// 화면은 탭 묶음([IndexedStack]) 안에서 앱 시작 때 만들어지므로, 서버는 AI 탭이
/// 처음 보일 때 부른다.
class ChatbotScreen extends StatefulWidget {
  const ChatbotScreen({super.key});

  @override
  State<ChatbotScreen> createState() => _ChatbotScreenState();
}

class _ChatbotScreenState extends State<ChatbotScreen> {
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();

  late final TabState _tabs = context.read<TabState>();
  late final ChatApiService _api = ChatApiService(context.read<ApiClient>());

  /// 지금 보이는 대화. 처음 열기 전이나 열다 실패했으면 null.
  ChatConversation? _conversation;

  /// 대화 열기 상태. null 이면 아직 AI 탭을 연 적이 없다.
  LoadState? _load;

  /// 마지막 대화 열기. 실패하면 이걸 다시 시도하고, 늦게 온 앞 요청은 버린다.
  Future<ChatConversation> Function()? _lastOpen;

  /// 처리한 마지막 [TabState.chatRequest].
  ChatRequest? _handledRequest;

  /// 말을 보내는 중(`POST .../messages`).
  bool _sending = false;

  /// 답을 기다리는 AI 말 id.
  String? _waitingFor;

  /// 기다리다 한도를 넘긴 AI 말 id. 그 말풍선에 "다시 확인" 을 둔다.
  String? _lateReply;

  /// 대화의 기준 끼니(맥락 줄). 못 받아도 대화는 그대로 쓴다.
  MealDetail? _meal;

  /// "이걸로 바꾸기" 로 다음 끼니 추천이 된 카드. 바꿀 때마다 추천 전체가 그 카드
  /// 하나로 바뀌므로 하나만 기억한다.
  String? _chosenCard;

  /// 바꾸는 중인 카드(`PUT .../feedback/suggestions`).
  String? _replacingCard;

  bool get _busy => _sending || _waitingFor != null || _conversation == null;

  @override
  void initState() {
    super.initState();
    _tabs.addListener(_onTabsChanged);
    _onTabsChanged();
  }

  @override
  void dispose() {
    _tabs.removeListener(_onTabsChanged);
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _onTabsChanged() {
    if (_tabs.currentIndex != TabState.ai) return;
    final request = _tabs.chatRequest;
    if (request != null && !identical(request, _handledRequest)) {
      _handledRequest = request;
      if (_conversation?.mealId != request.mealId) {
        _open(() => _api.start(mealId: request.mealId));
        return;
      }
    }
    if (_load == null) _open(_resumeOrStart);
  }

  Future<ChatConversation> _resumeOrStart() async =>
      await _api.fetchLatest() ?? await _api.start();

  /// 대화를 연다.
  Future<void> _open(Future<ChatConversation> Function() open) async {
    _lastOpen = open;
    setState(() {
      _load = LoadState.loading;
      _conversation = null;
      _waitingFor = null;
      _lateReply = null;
      _chosenCard = null;
    });
    _input.clear();
    try {
      final conversation = await open();
      if (!mounted || !identical(open, _lastOpen)) return;
      setState(() {
        _conversation = conversation;
        _load = LoadState.ready;
      });
      _scrollToEnd();
      _loadMeal(conversation.mealId);
      // 이어 본 대화의 마지막 답이 아직 만들어지는 중이면 마저 기다린다.
      final last = conversation.messages.lastOrNull;
      if (last != null && !last.fromUser && last.isGenerating) {
        _waitReply(last.messageId);
      }
    } catch (_) {
      if (mounted && identical(open, _lastOpen)) {
        setState(() => _load = LoadState.failed);
      }
    }
  }

  Future<void> _loadMeal(String? mealId) async {
    if (_meal?.mealId == mealId) return;
    setState(() => _meal = null);
    if (mealId == null) return;
    try {
      final meal = await MealApiService(
        context.read<ApiClient>(),
      ).fetchMeal(mealId);
      if (mounted && _conversation?.mealId == mealId) {
        setState(() => _meal = meal);
      }
    } catch (_) {
      // 맥락 줄 하나일 뿐이다.
    }
  }

  /// 말을 보낸다. 빈 말이나 답을 기다리는 중이면 무시한다.
  Future<void> _send(String raw) async {
    final text = raw.trim();
    final conversation = _conversation;
    if (text.isEmpty || conversation == null || _busy) return;
    setState(() => _sending = true);
    try {
      final sent = await _api.send(conversation.conversationId, text);
      if (!mounted || !identical(conversation, _conversation)) return;
      _input.clear();
      setState(() {
        _sending = false;
        // 다시 조회하기 전까지 보낸 말과 "만들고 있어요" 자리를 먼저 보인다.
        _conversation = ChatConversation(
          conversationId: conversation.conversationId,
          mealId: conversation.mealId,
          messages: [
            ...conversation.messages,
            sent.userMessage,
            ChatMessage(
              messageId: sent.assistantMessageId,
              fromUser: false,
              status: 'GENERATING',
              foodCards: const [],
            ),
          ],
        );
      });
      _scrollToEnd();
      _waitReply(
        sent.assistantMessageId,
        interval: sent.pollInterval,
        timeout: sent.timeout,
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _sending = false);
      final message = e is ApiException && e.code == 'CONFLICT'
          ? '앞 질문의 답을 아직 만들고 있어요.'
          : '보내지 못했어요. 잠시 후 다시 시도해 주세요.';
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(message)));
    }
  }

  /// [messageId] 답이 다 만들어질 때까지 대화를 다시 조회한다. 기본값은 BE 의
  /// `CHAT_REPLY_POLL_INTERVAL_MS` · `CHAT_REPLY_TIMEOUT_MS` 다.
  Future<void> _waitReply(
    String messageId, {
    Duration interval = const Duration(seconds: 1),
    Duration timeout = const Duration(seconds: 30),
  }) async {
    final id = _conversation?.conversationId;
    if (id == null) return;
    bool active() => mounted && _conversation?.conversationId == id;
    setState(() {
      _waitingFor = messageId;
      _lateReply = null;
    });
    final done = await pollUntil<ChatConversation>(
      fetch: () => _api.fetch(id),
      isDone: (c) => !(c.messageById(messageId)?.isGenerating ?? false),
      isActive: active,
      onResult: (c) {
        setState(() => _conversation = c);
        _scrollToEnd();
      },
      first: interval,
      maxInterval: interval,
      growth: 1,
      budget: timeout,
    );
    if (!active()) return;
    setState(() {
      _waitingFor = null;
      if (done == null) _lateReply = messageId;
    });
  }

  /// "이걸로 바꾸기" — 확인을 받고 대화의 기준 끼니 추천을 [card] 로 바꾼다.
  /// 끼니 피드백(7번)을 다시 열면 바뀐 추천이 보인다.
  Future<void> _replace(ChatFoodCard card) async {
    final mealId = _conversation?.mealId;
    if (mealId == null || _replacingCard != null) return;
    final api = MealApiService(context.read<ApiClient>());
    final ok = await context.read<PopupGate>().confirmSuggestionReplace(
      context,
      foodCardId: card.foodCardId,
      foodName: card.foodName,
    );
    if (!ok || !mounted || _conversation?.mealId != mealId) return;
    setState(() => _replacingCard = card.foodCardId);
    String message;
    try {
      await api.replaceSuggestion(mealId, foodCardId: card.foodCardId);
      message = '다음 끼니 추천을 ${card.foodName}(으)로 바꿨어요.';
      if (mounted) setState(() => _chosenCard = card.foodCardId);
    } catch (e) {
      message = switch (e) {
        ApiException(code: 'CONFLICT') =>
          '끼니 기록이 바뀌어 이 추천은 쓸 수 없어요. 새 대화를 열어 주세요.',
        ApiException(code: 'NOT_CONFIRMED') => '확정한 끼니만 추천을 바꿀 수 있어요.',
        ApiException(code: 'NOT_FOUND') => '끼니 기록을 찾을 수 없어요.',
        _ => '바꾸지 못했어요. 잠시 후 다시 시도해 주세요.',
      };
    }
    if (!mounted) return;
    setState(() => _replacingCard = null);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  void _newChat() {
    if (_sending) return;
    _open(() => _api.start());
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final messages = _conversation?.messages ?? const <ChatMessage>[];
    final empty = messages.isEmpty;
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: [
            _buildHeader(),
            ?_buildMealContext(),
            Expanded(
              child: switch (_load) {
                LoadState.failed => _buildOpenFailed(),
                LoadState.loading => const Center(
                  child: CircularProgressIndicator(),
                ),
                // 아직 AI 탭을 열지 않았다. 숨은 탭에서 스피너를 돌리지 않는다.
                null => const SizedBox.shrink(),
                LoadState.ready =>
                  empty ? _buildGreeting() : _buildConversation(messages),
              },
            ),
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

  /// 어느 끼니 이야기인지(`10월 14일 점심 · 참치김밥, 삶은 계란`). 끼니 없이 연
  /// 대화거나 아직 못 받았으면 없다.
  Widget? _buildMealContext() {
    final meal = _meal;
    if (meal == null) return null;
    final when =
        '${meal.eatenAt.month}월 ${meal.eatenAt.day}일 '
        '${mealTypeLabel(meal.mealType)}';
    final foods = meal.displayName;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppSpacing.screenHorizontal,
        AppSpacing.sm,
        AppSpacing.screenHorizontal,
        0,
      ),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: AppColors.surfaceMuted,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text(
          foods.isEmpty ? '$when 기록으로 이야기해요' : '$when 기록으로 이야기해요 · $foods',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: AppTypography.caption.copyWith(color: AppColors.textSecondary),
        ),
      ),
    );
  }

  Widget _buildOpenFailed() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: AppSpacing.screenHorizontal,
        ),
        child: RetryBlock(
          message: '대화를 불러오지 못했어요',
          onRetry: () => _open(_lastOpen ?? _resumeOrStart),
        ),
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
  Widget _buildConversation(List<ChatMessage> messages) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final bubbleMax = math.min(262.0, (constraints.maxWidth - 24) * 0.75);
        final firstQuestion = messages.indexWhere((m) => m.fromUser);
        return ListView.separated(
          controller: _scroll,
          padding: const EdgeInsets.fromLTRB(
            12,
            AppSpacing.lg,
            12,
            AppSpacing.md,
          ),
          itemCount: messages.length,
          separatorBuilder: (_, _) => const SizedBox(height: 10),
          itemBuilder: (context, i) {
            final m = messages[i];
            return m.fromUser
                ? _UserBubble(text: m.text ?? '', maxWidth: bubbleMax)
                : _buildReply(
                    m,
                    bubbleMax,
                    // 첫 질문 전의 카드는 지금 추천을 그대로 옮긴 것이라 바꿀 게 없다.
                    replaceable:
                        _conversation?.mealId != null &&
                        firstQuestion >= 0 &&
                        i > firstQuestion,
                  );
          },
        );
      },
    );
  }

  /// AI 말 하나. 만드는 중 · 늦어짐 · 실패 · 숨김은 안내 문구로 바꿔 보인다.
  /// [replaceable] 이면 카드에 "이걸로 바꾸기" 를 둔다.
  Widget _buildReply(
    ChatMessage m,
    double maxWidth, {
    bool replaceable = false,
  }) {
    if (m.isGenerating) {
      final late = m.messageId == _lateReply;
      return _AiBubble(
        text: late ? '답이 늦어지고 있어요. 잠시 뒤 다시 확인해 주세요.' : '답을 만들고 있어요…',
        muted: true,
        maxWidth: maxWidth,
        actionLabel: late ? '다시 확인' : null,
        onAction: late ? () => _waitReply(m.messageId) : null,
      );
    }
    if (m.isFailed) {
      return _AiBubble(
        text: '답을 만들지 못했어요. 다시 보내 주세요.',
        muted: true,
        maxWidth: maxWidth,
      );
    }
    if (m.isHidden) {
      return _AiBubble(
        text: '의료 판단이 필요한 질문이라 답하기 어려워요. 담당 의사와 상담해 주세요.',
        muted: true,
        maxWidth: maxWidth,
      );
    }
    return _AiBubble(
      text: m.text ?? '',
      cards: [
        for (final c in m.foodCards)
          _FoodCard(
            card: c,
            chosen: c.foodCardId == _chosenCard,
            onReplace: replaceable && _replacingCard == null
                ? () => _replace(c)
                : null,
            showReplace: replaceable,
          ),
      ],
      maxWidth: maxWidth,
    );
  }

  /// 빠른 질문. 처음에는 세로로 쌓이고, 대화가 시작되면 가로 한 줄로 밀린다.
  Widget _buildQuickQuestions(bool vertical) {
    final chips = [
      for (final q in _quickQuestions)
        _QuickChip(label: q, onTap: _busy ? null : () => _send(q)),
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
              // 답을 기다리는 동안은 보낼 수 없다.
              color: _busy ? AppColors.inactive : AppColors.primary,
              shape: const CircleBorder(),
              child: InkWell(
                onTap: _busy ? null : () => _send(_input.text),
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

/// 빠른 질문 칩. [onTap] 이 null 이면(답을 기다리는 중) 누를 수 없다.
class _QuickChip extends StatelessWidget {
  const _QuickChip({required this.label, required this.onTap});

  final String label;
  final VoidCallback? onTap;

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
/// [muted] 는 답 대신 보이는 안내(만드는 중 · 실패 등)다.
class _AiBubble extends StatelessWidget {
  const _AiBubble({
    required this.text,
    required this.maxWidth,
    this.cards = const [],
    this.muted = false,
    this.actionLabel,
    this.onAction,
  });

  final String text;
  final double maxWidth;
  final List<Widget> cards;
  final bool muted;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final label = actionLabel;
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
                text,
                style: AppTypography.body.copyWith(
                  fontSize: 11.5,
                  height: 17 / 11.5,
                  color: muted
                      ? AppColors.textSecondary
                      : AppColors.textPrimary,
                ),
              ),
              if (label != null && onAction != null)
                TextButton(
                  onPressed: onAction,
                  style: TextButton.styleFrom(
                    padding: EdgeInsets.zero,
                    minimumSize: const Size(0, 32),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  child: Text(label),
                ),
              for (final c in cards) ...[const SizedBox(height: 6), c],
            ],
          ),
        ),
      ),
    );
  }
}

/// 추천 음식 카드 — 음식 이름과 그 음식 100g 에 든 영양소 칩.
///
/// [showReplace] 면 아래에 "이걸로 바꾸기" 를 둔다. [onReplace] 가 null 이면(다른 카드를
/// 바꾸는 중) 누를 수 없다. [chosen] 이면 다음 끼니 추천이 된 카드다.
class _FoodCard extends StatelessWidget {
  const _FoodCard({
    required this.card,
    this.showReplace = false,
    this.onReplace,
    this.chosen = false,
  });

  final ChatFoodCard card;
  final bool showReplace;
  final VoidCallback? onReplace;
  final bool chosen;

  @override
  Widget build(BuildContext context) {
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
            card.foodName,
            style: AppTypography.cardTitle.copyWith(fontSize: 11),
          ),
          if (card.effects.isNotEmpty) ...[
            const SizedBox(height: 8),
            NutrientAmountChips(nutrients: card.effects),
          ],
          if (chosen)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                '✓ 다음 끼니 추천으로 정했어요',
                style: AppTypography.emphasis.copyWith(
                  fontSize: 10,
                  color: AppColors.quality,
                ),
              ),
            )
          else if (showReplace)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                onPressed: onReplace,
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  minimumSize: const Size(0, 30),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                child: const Text('이걸로 바꾸기'),
              ),
            ),
        ],
      ),
    );
  }
}
