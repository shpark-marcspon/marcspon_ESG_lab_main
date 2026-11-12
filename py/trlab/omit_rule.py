"""TRX10 — 짧은 문장 누락 규칙: KR 문장 수 대 EN 문장 수 대조 + 모자라면 그 블록만 되묻기(luna) (MARC-TICK-514, 이슈 382).

(1) 문장 나누기: EN = subject.sentences(import, TRX1 그대로). KR = kr_sentences — 같은 틀에서 대문자 lookahead 만 뺀 정규식
    KR_SPLIT `(?<=[.!?])\\s+` + 잡음 상수 KR_NOISE(「1. 」「(XI. 」 같은 목록·로마 숫자 번호 토큰, subject.ABBR 약어)는 다음 조각에 잇는다.
    sam 문단 전량에서 「다.」가 아닌 자리의 나뉨은 10(목록 번호 6 · 「비율.」 「면적).」 「!」 4 — 뒤 셋은 진짜 문장 끝)이라 상수는 목록 번호만.
    표·제목은 뺀다(문단·mixed 만 — applies(kind)).
(2) 규칙: n_en < n_ko(OMIT_MIN_GAP = 1)이면 걸림. 심은 24 + 깨끗 14(TRX7, 심은 EN)·전량 1,391(sam) 에서 재현·헛걸림·걸린 블록 수.
(3) 되묻기: 규칙에 걸린 블록만 luna 1호출 — REASK_INSTR(새 글, 영문 ≤ 600자, 라벨 전 얼림 — sha 는 out/trx10/first-run.txt)
    입력 = 번호 붙인 KR 문장 + EN, 답 {missing:[int], reason}(strict). 채점(심은 omit 4): missing 에 심은 문장 번호(planted.before 가
    EN 깨끗 문장 중 몇째인지 → 같은 차례의 KR 번호)면 span, 다른 번호만 block_only, 빈 목록 miss. 나머지 34 = missing 비면 맞음.
(4) 라벨 갈래(gold-trx10/omit.json): missing(정말 없음) · merged(합쳐 옮김, 뜻은 있음) · split_noise(나누기 잡음 — 약어·숫자 뒤 점·목록).
V2 지시문·채점(review_probe)은 0줄 — 되묻기는 별도 호출이다.
"""

import re

from trlab.subject import ABBR, sentences

KR_SPLIT = re.compile(r"(?<=[.!?])\s+")  # subject.sentences 의 `(?<=[.!?])\s+(?=[A-Z“"‘'(])` 에서 대문자 lookahead 만 뺌(KR 뒤엔 대문자가 안 온다)
# 잡음 상수 — 조각이 목록 번호 토큰으로 끝나면 다음 조각에 잇는다: 「1.」 「2.」 「(XI.」 「II.」 (sam 문단 실측: 「1) 1.」 「있습니다. 2.」 꼴 6)
KR_NOISE = re.compile(r"(?:^|[\s(])\(?(?:\d{1,2}|[IVX]{1,4})[.)]?\.$")
OMIT_MIN_GAP = 1  # n_ko - n_en ≥ 1 이면 걸림(문턱 1 — 짧은 문장 하나가 과녁, §6 4)
APPLY_KINDS = ("paragraph", "mixed")  # 표·제목은 뺀다
LABELS = ("missing", "merged", "split_noise")
MIN_LEN = 3  # subject.sentences 와 같음(3자 넘는 것만)

# 되묻기 지시문 — 새 글(V2 지시문과 별개), 영문 ≤ 600자. 라벨 전 얼림(out/trx10/first-run.txt). 얼린 뒤 고치지 않는다 — 「고칠 후보」로만.
REASK_INSTR = (
    "You are given the numbered Korean sentences of one block of a Korean ESG report and the English translation of that block. "
    "For each numbered Korean sentence, decide whether its meaning is present anywhere in the English translation - it may be merged "
    "with a neighbouring sentence, reordered, or paraphrased; that still counts as present. "
    "Report only the numbers of Korean sentences whose meaning is entirely absent from the English translation. "
    "If every Korean sentence is covered, return an empty list. Give a one-line reason."
)
REASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["missing", "reason"],
                "properties": {"missing": {"type": "array", "items": {"type": "integer"}}, "reason": {"type": "string"}}}


def kr_sentences(text):
    """KR 문장 목록. KR_SPLIT 로 나눈 뒤 KR_NOISE·ABBR 로 끝나는 조각은 다음에 잇고, MIN_LEN 자 넘는 것만."""
    text = re.sub(r"\s+", " ", text or "").strip()
    parts, buf = [], ""
    for tok in KR_SPLIT.split(text):
        buf = (buf + " " + tok).strip() if buf else tok
        if KR_NOISE.search(buf) or ABBR.search(buf):
            continue
        parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return [p for p in parts if len(p) > MIN_LEN]


def en_sentences(text):
    return sentences(text)


def applies(kind):
    return kind in APPLY_KINDS


def counts(ko, en):
    """→ (n_ko, n_en)."""
    return len(kr_sentences(ko)), len(en_sentences(en))


def flagged(n_ko, n_en):
    return (n_ko - n_en) >= OMIT_MIN_GAP


def check(ko, en, kind="paragraph"):
    """→ {applies, n_ko, n_en, flagged}. applies 가 False 면 flagged 는 None."""
    n_ko, n_en = counts(ko, en)
    a = applies(kind)
    return {"applies": a, "n_ko": n_ko, "n_en": n_en, "flagged": flagged(n_ko, n_en) if a else None}


# ---------- 되묻기 ----------
def reask_input(ko, en):
    """→ (입력 글, KR 문장 목록). 번호는 1 부터, KR 문장과 1:1."""
    ks = kr_sentences(ko)
    numbered = "\n".join(f"({i + 1}) {s}" for i, s in enumerate(ks))
    return f"[Korean sentences]\n{numbered}\n\n[English translation]\n{en}", ks


def build_reask(ko, en):
    """(instructions, input, schema, KR 문장 목록)."""
    text, ks = reask_input(ko, en)
    return REASK_INSTR, text, REASK_SCHEMA, ks


def read_reask(raw, n_ko):
    """→ {parsed, missing:[int](1..n_ko 안, 정렬·중복 제거), oob:[int], reason}."""
    import json
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"parsed": False, "missing": [], "oob": [], "reason": ""}
    if not isinstance(d, dict) or not isinstance(d.get("missing"), list):
        return {"parsed": False, "missing": [], "oob": [], "reason": ""}
    nums = [int(x) for x in d["missing"] if isinstance(x, (int, float)) and not isinstance(x, bool)]
    inside = sorted({x for x in nums if 1 <= x <= n_ko})
    oob = sorted({x for x in nums if not 1 <= x <= n_ko})
    return {"parsed": True, "missing": inside, "oob": oob, "reason": str(d.get("reason") or "")}


def planted_ko_no(ko, en_clean, before):
    """심은 omit 의 KR 문장 번호(1 부터). before(지운 EN 문장)가 EN 깨끗 문장 중 몇째인지 → 같은 차례의 KR 번호.
    KR·EN 문장 수가 다르면 비례 자리(가장 가까운 번호)로 두고 exact=False. before 를 못 찾으면 None."""
    es = en_sentences(en_clean)
    ks = kr_sentences(ko)
    b = re.sub(r"\s+", " ", before or "").strip()
    idx = next((i for i, s in enumerate(es) if b and (b in s or s in b)), None)
    if idx is None or not ks:
        return {"no": None, "exact": False, "en_idx": idx, "n_ko": len(ks), "n_en_clean": len(es)}
    if len(ks) == len(es):
        return {"no": idx + 1, "exact": True, "en_idx": idx, "n_ko": len(ks), "n_en_clean": len(es)}
    no = min(len(ks), max(1, round(idx * len(ks) / len(es)) + 1))
    return {"no": no, "exact": False, "en_idx": idx, "n_ko": len(ks), "n_en_clean": len(es)}


def hit_of(planted_no, missing):
    """심은 omit 단위: missing 에 심은 번호면 span, 다른 번호만 block_only, 빈 목록 miss. planted_no 가 None 이면 block_only/miss 만."""
    if not missing:
        return "miss"
    if planted_no is not None and planted_no in missing:
        return "span"
    return "block_only"


def score_unit(cat, planted_no, rule_flag, reask):
    """단위 하나(TRX7 38): cat omit 은 hit(규칙 안 걸림 = miss), 그 밖은 false_alarm(규칙 걸림 ∧ 되묻기 missing 비지 않음).
    rule_only_false_alarm = 규칙만 걸림(되묻기 전)."""
    missing = (reask or {}).get("missing", []) if reask else []
    if cat == "omit":
        if not rule_flag:
            return {"rule_flag": False, "hit_rule": "miss", "hit": "miss"}
        return {"rule_flag": True, "hit_rule": "rule_only", "hit": hit_of(planted_no, missing) if reask else "no_reask"}
    return {"rule_flag": bool(rule_flag), "false_alarm_rule": bool(rule_flag),
            "false_alarm": bool(rule_flag) and bool(missing)}


def valid_label(x):
    return x in LABELS
