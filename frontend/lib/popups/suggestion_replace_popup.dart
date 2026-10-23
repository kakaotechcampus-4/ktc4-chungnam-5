import 'package:flutter/material.dart';

/// 챗봇 카드의 "이걸로 바꾸기" 확인. 바꾸기를 누르면 `true`, 취소·바깥 탭이면 `false`.
///
/// 직접 `showDialog` 하지 말고 `PopupGate.confirmSuggestionReplace` 로 띄운다.
class SuggestionReplacePopup extends StatelessWidget {
  const SuggestionReplacePopup({super.key, required this.foodName});

  /// 새 추천이 될 음식(`두부 반 모`).
  final String foodName;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('다음 끼니 추천을 바꿀까요?'),
      content: Text('$foodName(으)로 바꿔요.\n지금 끼니 피드백의 추천은 이 음식 하나로 바뀌어요.'),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('취소'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('바꾸기'),
        ),
      ],
    );
  }
}
