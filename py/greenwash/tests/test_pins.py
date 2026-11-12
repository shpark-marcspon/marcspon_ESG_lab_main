"""노트북에서 옮긴 프롬프트·낱말 목록·정규식이 바뀌지 않았음을 게이트 안에서 지문으로 잰다.

원본 대조(`notebook_probe.py`)는 노트북이 있어야 돌고 게이트 밖이다. 노트북이 새 판으로 바뀌어 다시 옮겼을 때만
`python -m greenwash.tests.pins` 로 값을 새로 낸다. 그 밖에 이 테스트가 빨개지면 옮긴 글이 바뀐 것이다(고치지 않는다).
"""
from greenwash.pdftext import _loader_text
from greenwash.tests.pins import fingerprints

PINNED = {
    "detect_system_prompt": "6d5e9b3d518034e8",
    "eval_system_prompt": "1eb626446da6942b",
    "repair_fix_prompt": "b622ac61aebceacd",
    "low_only_prompt": "f660d9d9f355b0a3",
    "GREEN_TERMS": "20824fead0120d61",
    "DEFAULT_EXCLUDE_PATTERNS": "20ca0c9c56dd6c45",
    "DEFAULT_ENV_KEYWORDS": "c578f85514001762",
    "_LABEL_PATTERNS": "cc4505a7c8655e8e",
    "_FACT_TENSE_RE": "11210acbce59c7fb",
    "_PROMO_PHRASES_RE": "1c45e0568808b142",
    "_HIGH_RISK_KEYWORDS_RE": "7ef8f82bbdf21ce0",
    "SAFE_LIMITS_INT": "d1f3bdbe32227348",
}


def test_ported_constants_keep_their_fingerprints():
    assert fingerprints() == PINNED


def test_page_text_drops_outer_whitespace_only():
    # `PyPDFLoader` 의 뒤처리(★1): 양끝 공백만 떼고 가운데는 그대로 둔다.
    assert _loader_text(" \n \n가 나\n다  \n") == "가 나\n다"
    assert _loader_text("   ") == ""
