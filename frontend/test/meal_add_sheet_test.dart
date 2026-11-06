import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/screens/food_entry_editor.dart';
import 'package:frontend/screens/meal_add_sheet.dart';

void main() {
  group('FoodEntryController', () {
    test('비어 있으면 추가할 음식이 없다고 알린다', () {
      final c = FoodEntryController();
      addTearDown(c.dispose);

      expect(c.collect(), isNull);
      expect(c.error, '추가할 음식을 입력해 주세요');
    });

    test('이름과 0보다 큰 양이 있어야 음식 한 줄을 돌려준다', () {
      final c = FoodEntryController();
      addTearDown(c.dispose);

      c.amount.text = '100';
      expect(c.collect(), isNull);
      expect(c.error, '음식 이름을 적어 주세요');

      c.name.text = '현미밥';
      c.amount.text = '0';
      expect(c.collect(), isNull);
      expect(c.error, '양을 0보다 큰 숫자로 적어 주세요');

      c.amount.text = '210';
      final entry = c.collect();
      expect(entry, isNotNull);
      expect(entry!.name, '현미밥');
      expect(entry.amount, 210);
      expect(entry.unit, 'g');
    });

    test('입력을 고치면 막힌 이유가 사라진다', () {
      final c = FoodEntryController();
      addTearDown(c.dispose);

      c.collect();
      expect(c.error, isNotNull);
      c.name.text = '미';
      expect(c.error, isNull);
    });
  });

  testWidgets('음식 추가 시트는 단위 선택·목록에 추가 버튼 없이 한 줄을 돌려준다', (tester) async {
    FoodEntry? result;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () async => result = await showMealAddSheet(context),
              child: const Text('열기'),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('열기'));
    await tester.pumpAndSettle();
    expect(find.text('먹은 음식을 추가해 주세요.'), findsOneWidget);
    expect(find.text('양 (g)'), findsOneWidget);
    expect(find.text('목록에 추가'), findsNothing);
    expect(find.byType(DropdownButton<String>), findsNothing);

    // 비어 있는 채로 추가하기 → 시트가 닫히지 않고 안내가 뜬다.
    await tester.tap(find.text('추가하기'));
    await tester.pumpAndSettle();
    expect(find.text('추가할 음식을 입력해 주세요'), findsOneWidget);

    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), '현미밥');
    await tester.enterText(fields.at(1), '210');
    await tester.tap(find.text('추가하기'));
    await tester.pumpAndSettle();

    expect(result, isNotNull);
    expect(result!.name, '현미밥');
    expect(result!.amount, 210);
    expect(result!.unit, 'g');
  });

  testWidgets('닫기(✕)는 오른쪽 끝에 있고 제목과 겹치지 않는다', (tester) async {
    tester.view.physicalSize = const Size(375 * 2, 812 * 2);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => showMealAddSheet(context),
              child: const Text('열기'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('열기'));
    await tester.pumpAndSettle();

    final close = tester.getRect(find.byTooltip('닫기'));
    final title = tester.getRect(find.text('음식 추가').first);
    expect(close.right, greaterThan(300));
    expect(close.left, greaterThan(title.right));
  });

  testWidgets('닫기(✕)로 닫으면 아무것도 돌려주지 않는다', (tester) async {
    FoodEntry? result = const FoodEntry(name: 'x', amount: 1);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () async => result = await showMealAddSheet(context),
              child: const Text('열기'),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('열기'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('닫기'));
    await tester.pumpAndSettle();

    expect(result, isNull);
  });
}
