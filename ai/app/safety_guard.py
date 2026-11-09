"""끼니 피드백의 의료 지시 가드. 의료 안전 전반을 판정하지는 않는다 — 모델이 쓴 뒤의 마지막 방어선이다.

이 앱은 약 · 용량 · 주사를 바꾸거나 끊으라는 말을 절대 하지 않는다. 문장의 뜻(무엇을 바꾸라는지)을 정규식으로
가리지 않고, 쓰는 자리마다 단순한 규칙을 둔다.

- 화면 문장(body · 추천 카드) — `screen_unsafe`. 약 낱말이 나오면 막는다. 화면에서 약 이야기는 의료진께 확인하라는
  안내뿐이라 그 안내 문장만 통과시킨다. 투약 단계는 "시작기 · 조정기 · 유지기" 처럼 단계 이름으로 쓴다(프롬프트 규칙).
- reasoning(화면에 안 나감) — `reasoning_unsafe`. "용량 조정 중이라 …" 처럼 단계를 근거로 쓰는 자리라 약 낱말은
  허용하고, 사용자에게 하는 지시 어미("-세요 · -셔도 돼요")가 약 낱말과 한 문장에 있을 때만 막는다. reasoning 은
  "-한다" 체로 쓴다.
- 입력(음식 이름 · 메모) — `is_unsafe`. 처음 규칙 그대로다.

약 낱말이 없는 지시("이제 증량하셔도 돼요")는 이 규칙이 보지 못한다. 모델의 거절 · 프롬프트 · eval
`no_medical_judgement` 가 지킨다. 그런 문장은 `tests/test_safety_guard.py` 의 `KNOWN_GAPS` 에 고정한다.
"""

import re

BLOCKED_BODY = "약물이나 증상에 관한 판단은 의료진과 상담해 주세요."

# ── 입력: 처음 규칙 ──

_MEDICAL_DIRECTIVE = re.compile(
    r"(?:용량|투약|복용|주사|약물|약을|mg|밀리그램)[^.!?\n]{0,60}"
    r"(?:늘리|줄이|중단|끊|증량|감량|바꾸|변경|복용하|투여하|맞으)"
    r"|(?:당뇨|췌장염|위마비|질병)[^.!?\n]{0,30}(?:진단|확실|입니다|이에요)"
)


def is_unsafe(text: str) -> bool:
    """입력(음식 이름 · 메모)에 명시적인 의료 처방 표현이 있는가."""
    return bool(_MEDICAL_DIRECTIVE.search(text))


# ── 약 낱말 ──

# "약" 한 글자는 낱말 앞뒤로 가린다 — 약간 · 약과 · 약속 · 약불 · 약하다(간이 약한 · 약했고) · 약식 · 약밥 · 약수 · 약국 · 약사,
# 음식 이름(약고추장 · 약재 · 약선), 그리고 숫자 앞의 "약"(대략 — "약 20분", "약 반공기", "약 일주일").
# 숫자 뒤에 알 · 정 · 캡슐 · 배 · 번 · 회 · mg 가 오면 복용량이라 약이다 — "약 두 알", "약 2배로"
_APPROX = r"(?:\d+(?:\.\d+)?(?:\s*[~\-–]\s*\d+(?:\.\d+)?)?|½|한두|두세|서너|몇|절반|일주일|반|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무)"
_DOSE_UNIT = r"(?:알|정|캡슐|배|번|회|mg|밀리그램)"
_YAK = (
    r"(?<![가-힣])약(?!간|과|속|점|불|하|해|한|했|함|할|식|밥|수|국|사|고추장|재|선)"
    r"(?:(?=\s*" + _APPROX + r"\s*" + _DOSE_UNIT + r")|(?!\s*" + _APPROX + r"))"
)
_ENGLISH_DRUG = (
    r"wegovy|mounjaro|ozempic|saxenda|zepbound|semaglutide|tirzepatide|liraglutide|"
    r"dose|dosage|injections?|shots?|medications?|medicines?|meds|pills?"
)
_MEDICATION = re.compile(
    _YAK
    # "약과 영양제를" 은 약 + 과 다. "약과는 당이 높으니" 는 음식이다
    + r"|약과(?=\s+[가-힣]+(?:을|를|은|는))"
    + r"|\d+(?:\.\d+)?\s*(?:mg|밀리그램)"
    # "위 용량"(위의 크기)은 약이 아니다
    + r"|(?:혈압|혈당|당뇨|비만|위장|처방|먹는)약|약물|투약|복약|복용|투여|(?<!위\s)(?<!위의\s)(?<!위)용량|주사(?!위)|주입|니들|"
    r"인슐린|처방|GLP-?1|위고비|마운자로|삭센다|오젬픽|젭바운드|메트포르민|"
    r"세마글루타이드|티르제파타이드|터제파타이드|리라글루타이드|"
    r"(?i:(?<![a-z])(?:" + _ENGLISH_DRUG + r")(?![a-z]))"
)
_DIAGNOSIS = re.compile(r"(?:당뇨|췌장염|위마비|질병)[^.!?\n]{0,30}(?:진단|확실|입니다|이에요)")
# 영양소 곁의 mg 는 용량이 아니다 — "나트륨이 1950mg까지", "국물 하나만 960mg이라 나트륨이". 숫자 + mg 만 바꾸고 곁의 낱말은 남긴다
_NUTRIENTS = re.compile(r"나트륨|칼륨|칼슘|철분|콜레스테롤|카페인|당류|비타민|오메가|마그네슘|아연|엽산|식이섬유")
_MG = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|밀리그램)")


def _without_nutrient_mg(sentence: str) -> str:
    def keep(m: re.Match[str]) -> str:
        near = sentence[max(0, m.start() - 25) : m.start()] + sentence[m.end() : m.end() + 15]
        # 숫자만 남겨 "약 100mg"(대략)이 "약 0" 으로 읽히게 한다
        return "0" if _NUTRIENTS.search(near) else m.group(0)

    return _MG.sub(keep, sentence)


def _medication(sentence: str) -> bool:
    return bool(_MEDICATION.search(_without_nutrient_mg(sentence)) or _DIAGNOSIS.search(sentence))


# 의료진께 확인하라는 안내 — 대상과 확인 동사
_REFERRAL = re.compile(r"(?:의료진|의사|병원|약사|주치의|담당\s*선생님)[^.!?\n]*?(?:확인|상의|상담|문의|여쭤|물어|이야기|얘기|말씀)")
# 안내 뒤에 오면 안내가 아닌 것 — 부정("물어보지 말고"), 조건("확인 후 · 전까지"), 바꾸기 · 끊기 · 복용 동사
_AFTER_REFERRAL = re.compile(
    r"지\s*말|없이|필요\s*없|전까지|후\s|"
    r"그만|중단|중지|끊|미루|미뤄|건너뛰|거르|생략|쉬|맞|드시|드세|먹|빼|멈추|올리|올려|늘리|늘려|줄이|줄여|"
    r"바꾸|바꿔|낮추|낮춰|높이|높여|배로"
)
# 안내 앞의 지시 — "약은 끊으시고 의료진께 확인해 보세요"
_BEFORE_REFERRAL = re.compile(
    r"(?:그만|중단|중지|끊|미루|미뤄|건너뛰|거르|생략|쉬|빼|멈추|올리|올려|늘리|늘려|줄이|줄여|바꾸|바꿔|낮추|낮춰|높이|높여)"
    r"[가-힣]*(?:세요|시고|하고|고|셔도|해도|어도|아도)(?=\s|$)"
)


def _referral(part: str) -> bool:
    """의료진께 확인하라는 안내뿐인가. 안내 앞뒤에 약 낱말이나 다른 지시가 붙으면 안내가 아니다."""
    m = _REFERRAL.search(part)
    if m is None:
        return False
    before, after = part[: m.start()], part[m.end() :]
    return not (_BEFORE_REFERRAL.search(before) or _AFTER_REFERRAL.search(after) or _medication(after))


# 소수점("766.7mg")에서는 문장을 나누지 않는다
_SENTENCE = re.compile(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+")
# 쉼표로 나뉜 절 — "약 질문은 의료진께 확인해 보시고, 다음 끼니는 국물을 줄이세요"
_CLAUSE = re.compile(r"(?:[^.!?\n,]|(?<=\d)\.(?=\d))+")


def screen_unsafe(text: str) -> bool:
    """화면 문장(body · 추천 카드)에 약 이야기가 있는가. 의료진께 확인하라는 안내만 통과한다."""
    for sentence in _SENTENCE.findall(text):
        if not _medication(sentence) or _referral(sentence):
            continue
        # 안내 절과 다른 절이 쉼표로 이어진 문장은 절마다 본다
        if any(_medication(clause) and not _referral(clause) for clause in _CLAUSE.findall(sentence)):
            return True
    return False


# ── reasoning: 사용자에게 하는 약 지시 ──

# 사용자에게 하는 지시 · 허용 어미. reasoning 은 "-한다" 체라 이 어미가 나오면 사용자에게 말을 건 것이다
_USER_DIRECTIVE = re.compile(
    r"세요|셔요|십시오|하시길|셔야|무방|시는\s*게|시면\s*(?:돼|됩)|도\s*(?:돼(?!지)|괜찮아요|괜찮습니다|됩니다)"
)
# 인용한 금지 · 안내 · 화면 문구 — "임의로 끊지 마세요라는", "'천천히 나눠 드세요'로 안내한다"
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|‘[^’]*’|“[^”]*”|(?:지\s*마세요|확인하세요|상의하세요|상담하세요)(?:라는|라고|라며)")
_ENGLISH_DIRECTIVE = re.compile(
    r"(?<![a-z])(?:increase|decrease|reduce|lower|raise|double|skip|pause|stop|quit|change|cut|hold|take)(?![a-z])"
    r"[^.!?\n]{0,25}(?<![a-z])(?:" + _ENGLISH_DRUG + r")(?![a-z])",
    re.IGNORECASE,
)


def reasoning_unsafe(text: str) -> bool:
    """reasoning 에 약 낱말과 사용자에게 하는 지시 어미가 한 문장에 있는가."""
    if _ENGLISH_DIRECTIVE.search(text):
        return True
    for sentence in _SENTENCE.findall(_QUOTED.sub("", text)):
        if _medication(sentence) and _USER_DIRECTIVE.search(sentence) and not _referral(sentence):
            return True
    return False
