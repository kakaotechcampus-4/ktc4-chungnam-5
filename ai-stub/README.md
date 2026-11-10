# ai-stub — AI 개발용 더미 서버

AI 기능의 실제 구현과 개발은 [`ai/`](../ai/)로 이전했다.
실제 서비스 실행·환경변수·엔드포인트별 구현 상태는 [AI README](../ai/README.md)를 참고한다.

이 디렉터리에는 모델 호출 없이 고정 응답을 반환하는 개발·테스트용 스텁과
BE ↔ AI 계약 [schemas.py](schemas.py)가 남아 있다.
`X-Stub-Scenario` 헤더로 `SUCCESS` · `BLOCKED` · `ERROR_500` 응답을 선택할 수 있다.

실제 AI 연결 방법은 [AI README의 실행 안내](../ai/README.md#실행),
모델 평가는 [evals README](../ai/evals/README.md)를 따른다.


