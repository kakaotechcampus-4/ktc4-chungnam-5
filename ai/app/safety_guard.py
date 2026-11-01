"""명시적인 의료 처방 표현을 차단하는 최소 규칙. 의료 안전 전반을 판정하지는 않는다."""
import re

_MEDICAL_DIRECTIVE = re.compile(
    r"(?:용량|투약|복용|주사|약물|약을|mg|밀리그램)[^.!?\n]{0,60}"
    r"(?:늘리|줄이|중단|끊|증량|감량|바꾸|변경|복용하|투여하|맞으)"
    r"|(?:당뇨|췌장염|위마비|질병)[^.!?\n]{0,30}(?:진단|확실|입니다|이에요)"
)

BLOCKED_BODY = "약물이나 증상에 관한 판단은 의료진과 상담해 주세요."


def is_unsafe(text: str) -> bool:
    return bool(_MEDICAL_DIRECTIVE.search(text))
