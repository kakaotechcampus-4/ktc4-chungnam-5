import 'package:flutter/material.dart';

/// 음식 확인 화면에 들어왔는데 영양정보가 없는 음식이 있을 때의 안내.
/// "입력하기" 면 `true`, 닫기·바깥 탭이면 `false`.
///
/// 영양정보가 없는 음식이 남아 있으면 평가받기 버튼이 잠긴다 — 일부만 없는 채로 평가하면
/// 있는 음식만으로 점수가 계산돼 실제보다 낮게 나올 수 있어서다. 직접 `showDialog` 하지 말고
/// `PopupGate.confirmNutritionMissing` 으로 띄운다.
class NutritionMissingPopup extends StatelessWidget {
  const NutritionMissingPopup({super.key, required this.count});

  /// 영양정보가 없는 음식 수.
  final int count;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text('영양정보가 없는 음식이 $count개 있어요'),
      content: const Text(
        '영양정보를 입력하거나 음식을 삭제해 주세요.\n'
        '모두 해결되면 평가받기를 누를 수 있어요.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('닫기'),
        ),
        TextButton(
          onPressed: () => Navigator.of(context).pop(true),
          child: const Text('입력하기'),
        ),
      ],
    );
  }
}
