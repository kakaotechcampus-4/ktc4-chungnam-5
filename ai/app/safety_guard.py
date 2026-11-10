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
_APPROX = (
    r"(?:\d+(?:[.,/]\d+)*(?:\s*[~\-–]\s*\d+(?:\.\d+)?)?|½|"
    r"하루|이틀|사흘|열흘|보름|일주일|한두|두세|서너|두어|몇|절반|반|"
    r"(?:열|스무)?(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)|열|스무)"
)
# "정도" 의 정은 알약 단위가 아니다
_DOSE_UNIT = r"(?:알|정(?!도)|캡슐|배|번|회|단위|포|봉(?!지)|(?i:mg)|㎎|밀리그램)"
_YAK = (
    r"(?<![가-힣])약(?!간|과|속|점|불|하|해|한|했|함|할|식|밥|수|국|사|고추장|재|선|콩)"
    r"(?:(?=\s*" + _APPROX + r"\s*" + _DOSE_UNIT + r")|(?!\s*" + _APPROX + r"))"
)
_ENGLISH_DRUG = (
    r"wegovy|mounjaro|ozempic|saxenda|zepbound|semaglutide|tirzepatide|liraglutide|"
    r"dose|dosage|injections?|shots?|medications?|medicines?|meds|pills?"
)
_MEDICATION = re.compile(
    _YAK
    # "약과 영양제를" 은 약 + 과 다. "약과는 당이 높으니" 는 음식이다
    + r"|약과(?=\s+[가-힣]+(?:을|를|은|는)|\s*(?:함께|같이|식사|사이|복용|간격))"
    + r"|\d+(?:[.,]\d+)*\s*(?:(?i:mg)|㎎|밀리그램)"
    # "위 용량"(위의 크기)은 약이 아니다
    + r"|(?:혈압|혈당|당뇨|비만|위장|처방|먹는|변비|감기|두통|진통|소화|수면|멀미)약|알약|진통제|해열제|소화제|수면제|약물|투약|복약|복용|투여|(?<!(?<![가-힣])위\s)(?<!(?<![가-힣])위의\s)(?<!(?<![가-힣])위)용량|주사(?!위)|주입|니들|"
    r"인슐린|처방|(?i:GLP-?1)|위고비|마운자로|삭센다|오젬픽|젭바운드|메트포르민|"
    r"세마글루타이드|티르제파타이드|터제파타이드|리라글루타이드|"
    r"(?i:(?<![a-z])(?:" + _ENGLISH_DRUG + r")(?![a-z]))"
)
_DIAGNOSIS = re.compile(r"(?:당뇨|췌장염|위마비|질병)[^.!?\n]{0,30}(?:진단|확실|입니다|이에요)")
# 영양소 곁의 mg 는 용량이 아니다 — "나트륨이 1950mg까지", "국물 하나만 960mg이라 나트륨이". 숫자 + mg 만 바꾸고 곁의 낱말은 남긴다
_NUTRIENTS = re.compile(r"나트륨|칼륨|칼슘|철분|콜레스테롤|카페인|당류|비타민|오메가|마그네슘|아연|엽산|식이섬유")
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_MG = re.compile(_NUMBER + r"\s*(?:(?i:mg)|㎎|밀리그램)")


def _without_nutrient_mg(sentence: str) -> str:
    """영양소 곁의 숫자 + mg 를 "0" 으로 바꾼다. 같은 절 안에서 영양소 이름이 바로 붙어 있을 때만이다 —
    "나트륨 얘기는 그만하고 약 1mg", "0.5mg로 올리고 나트륨은" 처럼 다른 절의 영양소면 용량으로 남긴다."""

    def keep(m: re.Match[str]) -> str:
        before = sentence[max(0, m.start() - 40) : m.start()]
        after = sentence[m.end() : m.end() + 15]
        # 앞쪽 영양소는 연결 어미(-고 · -며 · -서 · 쉼표) 없이 이어져야 한다
        # 가장 가까운(마지막) 영양소 이름부터 본다 — "식이섬유는 충분했지만 나트륨이 1950mg"
        names = list(_NUTRIENTS.finditer(before))
        lead = before[names[-1].start() :] if names else None
        # 쉼표 뒤가 짧은 명사뿐이면 같은 나열이다 — "나트륨이 점심 960mg, 저녁 1,200mg". 약 낱말이 있으면 아니다
        if lead and "," in lead:
            tail = lead.rsplit(",", 1)[1]
            lead = lead if len(tail) <= 8 and not re.search(r"약|[가-힣](?:고|며|서|면|니|요)\s", tail) else None
        # 연결 어미 — "-고 · -며 · -서 · -으면 · -으니 · -는데 · -지만". "끼니 · 라면 · 국물에서" 는 낱말이다
        if lead and not re.search(r"[가-힣](?:고|며|(?<!에)서|으면|하면|다면|으니|니까|는데|지만|요)\s", lead):
            return "0"
        # 뒤쪽 영양소는 "-이라 · -인데 · -으로" 정도만 사이에 둔다 — "960mg이라 나트륨이"
        if _NUTRIENTS.match(re.sub(r"^\s*(?:정도의|정도|이라|라|인데|이고|으로|로|이|가|은|는|의)?\s*", "", after)):
            return "0"
        return m.group(0)

    return _MG.sub(keep, sentence)


def _medication(sentence: str) -> bool:
    return bool(_MEDICATION.search(_without_nutrient_mg(sentence)) or _DIAGNOSIS.search(sentence))


# 의료진께 확인하라는 안내. 정해진 문장 꼴만 통과한다 — 꼴이 다르면 위험하지 않아도 막는다(새는 것보다 낫다).
#   (앞 절,) [주제]은/는 (이유 절 "-어서 · -라서 · -없어서",) (처방해 준 · 담당) 의료진께 · 의사와 …
#   (따로 · 직접 · 먼저 · 꼭) 확인 · 상의 · 상담 · 문의 · 이야기 + 정해진 끝맺음
# 앞 절 · 주제 · 이유 절에 끊기 · 바꾸기 · 복용 낱말이 하나라도 있으면 막는다. 앞 절과 이유 절에는 약 낱말도 없어야 한다
_STOP_WORDS = re.compile(
    r"끊|중단|중지|그만|미루|미뤄|미룬|연기|건너|거르|걸러|생략|쉬|빼|뺀|멈|올리|올려|올린|늘리|늘려|늘린|줄이|줄여|줄인|"
    r"바꾸|바꿔|바꾼|낮추|낮춰|낮춘|높이|높여|높인|증량|감량|조절|조정|변경|배로|알씩|덜|"
    # 복용 동사. "맞는 중 · 드시는 동안"(지금 쓰고 있다는 서술)은 지시가 아니다
    r"맞(?!는\s*(?:중|동안|날))|드시(?!는\s*(?:중|동안))|드셔|먹"
)
_REFERRAL_SENTENCE = re.compile(
    # 앞 절 없는 해석을 먼저 본다 — 이유 절 뒤의 쉼표를 앞 절로 잘못 자르지 않게
    r"\s*(?:(?P<lead>[^.!?\n]{1,60}?),\s*)??"
    r"(?P<topic>[^.!?\n,]{1,24}?)(?:은|는)\s+"
    r"(?:(?P<reason>[^.!?\n,]{1,30}?(?:어서|아서|라서|없어서|어려워서))\s*,?\s+)?"
    r"(?:(?:처방해\s*(?:준|주신)|처방한|처방받은|담당)\s+)?"
    r"(?P<target>의료진|의사|병원(?:이나\s*약사)?|약사|주치의|담당\s*선생님)(?:에게|께|과|와|에)?\s+"
    r"(?:(?:따로|직접|먼저|꼭)\s+)?"
    r"(?:확인|상의|상담|문의|이야기|얘기)"
    r"(?:하세요|해\s*보세요|해\s*보면\s*좋아요|해\s*보시면\s*좋아요|해\s*보는\s*게\s*좋아요|"
    r"하는\s*(?:게|쪽이)\s*(?:좋아요|안전해요)|해\s*주세요)"
    r"\s*[.!?]?\s*"
)


def _referral(part: str) -> bool:
    """의료진께 확인하라는 안내 문장 꼴인가."""
    m = _REFERRAL_SENTENCE.fullmatch(part)
    if m is None:
        return False
    # 의료진 앞까지의 원문 전체에서 끊기 · 바꾸기 · 복용 낱말을 찾는다(정규식이 주제를 어디서 자르든 같게)
    if _STOP_WORDS.search(part[: m.start("target")]):
        return False
    return not (_medication(m.group("lead") or "") or _medication(m.group("reason") or ""))


# 소수점("766.7mg")에서는 문장을 나누지 않는다
_SENTENCE = re.compile(r"(?:[^.!?\n]|(?<=\d)\.(?=\d))+[.!?]?")


# 이보다 긴 글은 검사하지 않고 막는다. 모델 출력은 몇 문장이고, 정규식 검사 시간은 길이에 따라 커진다
_MAX_LENGTH = 2000


def screen_unsafe(text: str) -> bool:
    """화면 문장(body · 추천 카드)에 약 이야기가 있는가. 의료진께 확인하라는 안내만 통과한다."""
    if len(text) > _MAX_LENGTH:
        return True
    return any(_medication(sentence) and not _referral(sentence) for sentence in _SENTENCE.findall(text))


# ── reasoning: 사용자에게 하는 약 지시 ──

# 사용자에게 하는 지시 · 허용 어미. reasoning 은 "-한다" 체라 이 어미가 나오면 사용자에게 말을 건 것이다
_USER_DIRECTIVE = re.compile(
    r"세요|셔요|십시오|하시길|셔야|무방|시는\s*게|시면\s*(?:돼|됩)|도\s*(?:돼(?!지)|괜찮아요|괜찮습니다|됩니다)"
    r"|는\s*게\s*좋아요|면\s*돼요|면\s*됩니다"
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
    if len(text) > _MAX_LENGTH:
        return True
    if _ENGLISH_DIRECTIVE.search(text):
        return True
    for sentence in _SENTENCE.findall(_QUOTED.sub("", text)):
        if _medication(sentence) and _USER_DIRECTIVE.search(sentence) and not _referral(sentence):
            return True
    return False
