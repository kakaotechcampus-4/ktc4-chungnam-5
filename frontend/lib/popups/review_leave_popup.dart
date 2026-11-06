import 'package:flutter/material.dart';

/// 음식 확인 화면(5번)을 확정 전에 떠날 때 고르는 것.
enum ReviewLeaveChoice {
  /// 화면에 남아 계속 고친다.
  keepEditing,

  /// 기록을 남겨 두고 나간다. 홈·기록에서 이어서 확인할 수 있다.
  later,

  /// 기록을 지우고 나간다.
  discard,
}

/// 확정 전에 음식 확인 화면을 나가려 할 때의 확인창. 고른 [ReviewLeaveChoice] 를,
/// 바깥을 누르면 null 을 돌려준다.
///
/// 직접 `showDialog` 하지 말고 `PopupGate.confirmReviewLeave` 로 띄운다.
class ReviewLeavePopup extends StatelessWidget {
  const ReviewLeavePopup({super.key});

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('음식 확인을 마치지 않았어요'),
      content: const Text('기록을 지우면 되돌릴 수 없어요.\n나중에 이어서 하려면 "나중에"를 눌러 주세요.'),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(ReviewLeaveChoice.discard),
          child: const Text('기록 삭제'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(ReviewLeaveChoice.later),
          child: const Text('나중에'),
        ),
        TextButton(
          onPressed: () =>
              Navigator.of(context).pop(ReviewLeaveChoice.keepEditing),
          child: const Text('계속하기'),
        ),
      ],
    );
  }
}
