import 'api_client.dart';
import 'meal_api.dart';

// ── 모델 ────────────────────────────────────────────────────
// BE `schemas/chat.py`(챗봇 API A-1 ~ A-4).

/// AI 답변에 붙는 추천 음식 카드. [effects] 는 그 음식 100g 에 든 영양소다.
class ChatFoodCard {
  const ChatFoodCard({
    required this.foodCardId,
    required this.foodName,
    required this.effects,
  });

  final String foodCardId;
  final String foodName;
  final List<NutrientAmount> effects;

  factory ChatFoodCard.fromJson(Map<String, dynamic> json) => ChatFoodCard(
    foodCardId: json['foodCardId'] as String,
    foodName: json['foodName'] as String,
    effects: NutrientAmount.listFrom(json['effects']),
  );
}

/// 대화의 말 하나. 사용자 말은 늘 `READY` 다.
class ChatMessage {
  const ChatMessage({
    required this.messageId,
    required this.fromUser,
    required this.status,
    required this.foodCards,
    this.text,
    this.safetyStatus,
  });

  final String messageId;
  final bool fromUser;

  /// `GENERATING` · `READY` · `FAILED`.
  final String status;

  /// 안전 검사를 통과하지 못한 답이면 서버가 비워 보낸다([isHidden]).
  final String? text;
  final List<ChatFoodCard> foodCards;

  /// `SAFE` · `BLOCKED` · `REVIEW_REQUIRED`. 사용자 말과 생성 중인 답은 null.
  final String? safetyStatus;

  bool get isGenerating => status == 'GENERATING';
  bool get isFailed => status == 'FAILED';

  /// 다 만들었지만 의료 판단이 필요한 질문 등이라 내용을 보여 주지 않는다.
  bool get isHidden =>
      status == 'READY' && safetyStatus != null && safetyStatus != 'SAFE';

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
    messageId: json['messageId'] as String,
    fromUser: json['role'] == 'USER',
    status: json['status'] as String,
    text: json['text'] as String?,
    foodCards: [
      for (final c in json['foodCards'] as List<dynamic>? ?? const [])
        ChatFoodCard.fromJson(c as Map<String, dynamic>),
    ],
    safetyStatus: json['safetyStatus'] as String?,
  );
}

/// 대화 하나. [mealId] 는 이야기의 기준 끼니(없을 수 있다).
class ChatConversation {
  const ChatConversation({
    required this.conversationId,
    required this.messages,
    this.mealId,
  });

  final String conversationId;
  final String? mealId;

  /// 오래된 순.
  final List<ChatMessage> messages;

  ChatMessage? messageById(String id) {
    for (final m in messages) {
      if (m.messageId == id) return m;
    }
    return null;
  }

  factory ChatConversation.fromJson(Map<String, dynamic> json) =>
      ChatConversation(
        conversationId: json['conversationId'] as String,
        mealId: json['mealId'] as String?,
        messages: [
          for (final m in json['messages'] as List<dynamic>? ?? const [])
            ChatMessage.fromJson(m as Map<String, dynamic>),
        ],
      );
}

/// `POST .../messages` 의 202 응답. 답은 [assistantMessageId] 가 `READY`·`FAILED`
/// 가 될 때까지 [pollInterval] 간격으로, 최대 [timeout] 동안 대화를 다시 조회한다.
class ChatSent {
  const ChatSent({
    required this.userMessage,
    required this.assistantMessageId,
    required this.pollInterval,
    required this.timeout,
  });

  final ChatMessage userMessage;
  final String assistantMessageId;
  final Duration pollInterval;
  final Duration timeout;

  factory ChatSent.fromJson(Map<String, dynamic> json) => ChatSent(
    userMessage: ChatMessage.fromJson(
      json['userMessage'] as Map<String, dynamic>,
    ),
    assistantMessageId: json['assistantMessageId'] as String,
    pollInterval: Duration(
      milliseconds: json['pollIntervalMs'] as int? ?? 1000,
    ),
    timeout: Duration(milliseconds: json['timeoutMs'] as int? ?? 30000),
  );
}

// ── API ─────────────────────────────────────────────────────

/// AI 코치(챗봇) 엔드포인트. 진행 중인 대화는 사용자당 하나다.
class ChatApiService {
  ChatApiService(this._client);

  final ApiClient _client;

  /// `POST /chat/conversations` — 진행 중인 대화를 닫고 새로 연다.
  ///
  /// [mealId] 가 없으면 서버가 가장 최근 확정 끼니로 정한다. 그 끼니에 보여 줄
  /// 추천이 있으면 추천 카드를 담은 AI 말 하나로 시작한다.
  Future<ChatConversation> start({String? mealId}) async {
    final result = await _client.post(
      '/chat/conversations',
      body: {'mealId': ?mealId},
    );
    return ChatConversation.fromJson(result.dataMap);
  }

  /// `GET /chat/conversations/latest` — 이어 볼 대화. 없으면 null.
  Future<ChatConversation?> fetchLatest() async {
    final result = await _client.get('/chat/conversations/latest');
    final data = result.data;
    return data == null
        ? null
        : ChatConversation.fromJson(data as Map<String, dynamic>);
  }

  /// `GET /chat/conversations/{id}` — 말 전부. 답을 기다릴 때 다시 부른다.
  Future<ChatConversation> fetch(String conversationId) async {
    final result = await _client.get('/chat/conversations/$conversationId');
    return ChatConversation.fromJson(result.dataMap);
  }

  /// `POST /chat/conversations/{id}/messages` — 말을 보낸다(202).
  ///
  /// 앞 답을 아직 만드는 중이면 409 `CONFLICT` 다.
  Future<ChatSent> send(String conversationId, String text) async {
    final result = await _client.post(
      '/chat/conversations/$conversationId/messages',
      body: {'text': text},
    );
    return ChatSent.fromJson(result.dataMap);
  }
}
