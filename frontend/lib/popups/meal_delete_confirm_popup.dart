import 'package:flutter/material.dart';

/// 식사 기록 삭제 확인. 삭제를 누르면 `true`, 취소·바깥 탭이면 `false`.
///
/// 직접 `showDialog` 하지 말고 `PopupGate.confirmMealDelete` 로 띄운다.
class MealDeleteConfirmPopup extends StatelessWidget {
  const MealDeleteConfirmPopup({super.key, required this.description});

  /// 지울 끼니 한 줄(`점심 · 현미밥, 된장국`).
  final String description;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('식사 기록을 삭제할까요?'),
      content: Text('$description\n삭제하면 되돌릴 수 없어요.'),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('취소'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('삭제'),
        ),
      ],
    );
  }
}
