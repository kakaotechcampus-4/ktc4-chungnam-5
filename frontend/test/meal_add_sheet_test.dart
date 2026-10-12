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

    test('이름·양·단위가 갖춰져야 목록에 쌓인다', () {
      final c = FoodEntryController();
      addTearDown(c.dispose);

      c.name.text = '현미밥';
      expect(c.addFromForm(), isFalse);
      expect(c.error, '양을 0보다 큰 숫자로 적어 주세요');

      c.amount.text = '0';
      expect(c.addFromForm(), isFalse);

      c.amount.text = '1';
      expect(c.addFromForm(), isFalse);
      expect(c.error, '단위를 골라 주세요');

      c.setUnit('인분');
      expect(c.addFromForm(), isTrue);
      expect(c.entries.single.name, '현미밥');
      expect(c.entries.single.amount, 1);
      expect(c.entries.single.unit, '인분');
      // 이름·양 칸은 비워지고 단위는 남는다.
      expect(c.name.text, isEmpty);
      expect(c.amount.text, isEmpty);
      expect(c.unit, '인분');
    });

    test('폼에 적어 둔 값도 추가하기에서 함께 돌려준다', () {
      final c = FoodEntryController();
      addTearDown(c.dispose);

      c.setUnit('g');
      c.name.text = '미역국';
      c.amount.text = '200';
      expect(c.addFromForm(), isTrue);

      c.name.text = '삶은 계란';
      c.amount.text = '2';
      c.setUnit('개');

      final collected = c.collect();
      expect(collected, isNotNull);
      expect(collected!.map((e) => e.name), ['미역국', '삶은 계란']);
      expect(collected.last.unit, '개');
    });

    test('폼이 덜 채워졌으면 쌓아 둔 것이 있어도 보내지 않는다', () {
      final c = FoodEntryController();
      addTearDown(c.dispose);

      c.setUnit('g');
      c.name.text = '미역국';
      c.amount.text = '200';
      c.addFromForm();

      c.name.text = '삶은 계란';
      expect(c.collect(), isNull);
      expect(c.error, '양을 0보다 큰 숫자로 적어 주세요');
      expect(c.entries, hasLength(1));
    });
  });

  testWidgets('음식 추가 시트에서 적은 음식을 추가하기로 돌려준다', (tester) async {
    List<FoodEntry>? result;
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
    // 아직 쌓인 게 없어서 "추가한 음식" 목록은 없다.
    expect(find.text('추가한 음식'), findsNothing);

    // 비어 있는 채로 추가하기 → 시트가 닫히지 않고 안내가 뜬다.
    await tester.tap(find.text('추가하기'));
    await tester.pumpAndSettle();
    expect(find.text('추가할 음식을 입력해 주세요'), findsOneWidget);

    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), '현미밥');
    await tester.enterText(fields.at(1), '1');
    await tester.tap(find.byType(DropdownButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('인분').last);
    await tester.pumpAndSettle();

    await tester.tap(find.text('목록에 추가'));
    await tester.pumpAndSettle();
    expect(find.text('추가한 음식'), findsOneWidget);
    expect(find.text('현미밥'), findsOneWidget);
    expect(find.text('1인분'), findsOneWidget);

    await tester.tap(find.text('추가하기'));
    await tester.pumpAndSettle();

    expect(result, isNotNull);
    expect(result!.single.name, '현미밥');
    expect(result!.single.amount, 1);
    expect(result!.single.unit, '인분');
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
    List<FoodEntry>? result = const [];
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
