import 'package:flutter/material.dart';

/// "이번 끼니 안 먹었어요" 기록 확인. 기록하면 `true`, 취소·바깥 탭이면 `false`.
///
/// 식사 안 함은 기록하면 음식을 고칠 수 없어서(BE 409) 바꾸려면 지우고 다시 기록해야 한다 —
/// 잘못 눌러 곧바로 등록되지 않게 한 번 묻는다. 직접 `showDialog` 하지 말고
/// `PopupGate.confirmMealSkip` 으로 띄운다.
class MealSkipConfirmPopup extends StatelessWidget {
  const MealSkipConfirmPopup({super.key, required this.description});

  /// 기록할 끼니 한 줄(`점심 · 12:40`).
  final String description;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('이번 끼니를 안 먹었다고 기록할까요?'),
      content: Text(
        '$description\n기록한 뒤에는 고칠 수 없어요. 바꾸려면 지우고 다시 기록해 주세요.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('취소'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('기록하기'),
        ),
      ],
    );
  }
}
