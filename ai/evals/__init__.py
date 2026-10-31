"""모델 출력 eval — 실제 모델을 N회 불러 규칙을 지켰는가를 통과율로 잰다.

판정 함수는 `checks.py`, 반복·집계·게이트·CLI 는 `harness.py`, 케이스는 엔드포인트별 `<엔드포인트>_eval.py`.
실행은 `ai/` 에서 `python -m evals.<모듈>` 로 한다. 이 폴더에 `test_*.py` · `tests/` 를 두지 않는다.
"""
