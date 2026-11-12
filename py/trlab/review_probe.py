"""TRX7 — 영문번역 2차 검수 단계 실험: 검수 판 V0r·V0e·V1·V2(·V3) (MARC-TICK-510, 이슈 382).

(1) 단위: 세 책 공식 영문판(사람 번역)의 문단 짝 하나(EN 300~1,500자) 또는 「제목 짝 + 문단 짝」 둘을 이은 쪽(heading 단위).
    깨끗한 단위는 그대로, 심은 단위는 EN 한 자리를 손으로 바꾼다(갈래 일곱 CATS — before → after 를 units.json 에 기록).
    실제 오역 단위는 TRX1 엔진 산출(out/trpdf/res, sam)의 용어집 위반 블록을 다시 라벨한 것(real.json, 틀린 자리는 글자 offset).
    정답 파일(prework/gold-trx7)에는 글(국문·영문)을 넣지 않고 sha16·id·offset 만 둔다 — 글은 out/trx1 parse·엔진 산출에서 다시 읽는다
    (materialize). 실제 회사·사람 이름이 정답 파일에 들어가지 않게 하려는 것(발주서 §1 4).
(2) 판: V0r = 1차 엔진 검수(QA_SYSTEM_PROMPT + review_user) 그대로 + 거름 셋(종류 허용·span 이 번역문 안·중복) ·
    V0e = 윤문 검사(GENERIC_EDITORIAL_QA_PROMPT + editorial_qa_user) + must_apply 문턱 · V1 = V0e 답에서 이슈 ≥ 1 이면 걸림(호출 0) ·
    V2 = 블록 단위 체크리스트(일곱 갈래, 점수 없음, 확실한 것만 — 개발 셋으로 짜고 first-run.txt 에 얼림) · V3 = V2 를 sol 로.
    아래 「1차 엔진 글 그대로」 절은 esg-app colab_158 의 prompts.py·gates.py·review_rules.py 줄을 읽어서 베낀 것 — 글자를 고치지 않는다.
(3) 채점: flagged(이슈 ≥ 1 · V0e 는 must_apply) · hit ∈ span | block_only | miss(심은·실제 단위) · 깨끗 단위는 flagged = 헛걸림.
    span 맞음 = 이슈 original_span(정규화)이 심은 조각을 품거나 낱말 겹침 ≥ 짧은 쪽 50%, omit 은 종류 OMISSION 또는 OMIT_RE,
    heading 은 중복 제목 EN 과 겹치거나 HEAD_RE.
"""

import json
import random
import re
from pathlib import Path

from trlab import common

ROOT = common.ROOT
RES = ROOT / "esg-lab" / "out" / "trpdf" / "res"  # 엔진 산출(읽기만)
CATS = ("num", "unit", "neg", "term", "omit", "heading", "year")
PLAN_TEST = {"num": 4, "unit": 3, "neg": 3, "term": 4, "omit": 4, "heading": 3, "year": 3}  # 심은 24
CLEAN_TEST = {"paragraph": 11, "heading": 3}  # 깨끗 14
PLAN_DEV = {"num": 1, "term": 1, "omit": 1}  # 개발 셋(sam) 심은 3 + 깨끗 문단 3
CLEAN_DEV = {"paragraph": 3}
SEED = 510
EN_MIN, EN_MAX = 300, 1500
HEAD_PARA_MIN, HEAD_EN_MAX = 40, 2000  # 제목 단위(제목+문단 둘): 풀이 작아서(kb 2쪽) 문단 길이 조건을 느슨하게
CANDS_PER_SLOT = 3  # 자리마다 후보 셋(차례대로) — 손으로 심기 어려우면 다음 후보

UNIT_RE = re.compile(r"\b(?:tCO2e|tCO2-eq|tCO2|tons?|tonnes?|kWh|MWh|GWh|GJ|TJ|km|KRW|million|billion|㎥|m3|ha)\b")
NEG_RE = re.compile(r"\b(?:not(?! only\b)|no|never|without)\b")
YEAR_RE = re.compile(r"\b(?:19|20)\d\d\b")
NUM_RE = re.compile(r"\d")
NUM_TOKEN_RE = re.compile(r"(?<![\d.,])(?!(?:19|20)\d\d(?![\d.,]))\d[\d,]*(?:\.\d+)?")  # 연도가 아닌 수(두 자리 이상만 심는다 — 각주 번호 1) 2) 제외)
UNIT_NUM_RE = re.compile(r"\d[\d,.]*\s*(?:tCO2e|tCO2-eq|tCO2eq|tCO2|tons?|tonnes?|kWh|MWh|GWh|GJ|TJ|km|million|billion|trillion|㎥|m3|ha)\b|KRW\s*\d")
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")
OMIT_RE = re.compile(r"omit|missing|absent|left out|not translated|dropped|누락|빠", re.I)
HEAD_RE = re.compile(r"duplicate|same heading|same title|twice|repeat|identical|중복", re.I)
WORD_RE = re.compile(r"[a-z0-9]+")
# ---- 1차 엔진 글 그대로(esg-app colab_158, 읽어서 베낌 — 글자를 고치지 않는다) ----
# prompts.py:272-322
GENERIC_EDITORIAL_QA_PROMPT = """
Generic editorial QA gate — MANDATORY:

You are a senior English editor for published annual reports, sustainability reports,
integrated reports, ESG reports, corporate reports, policy documents, and executive messages.

You will receive:
1. Korean source text
2. Draft English translation

Your task is to review and, only if needed, revise the Draft English so that it reads
as natural, publication-ready report English.

This is a generic quality gate. Do not apply company-specific terminology unless it is
provided in GLOSSARY_all. Do not force fixed phrase replacements. Do not rewrite merely
for preference if the sentence is already natural and accurate.

Check for:
1. Grammar errors
2. Awkward or unnatural English collocations
3. Korean-influenced sentence structure
4. Noun-heavy translationese
5. Overly literal abstract phrases
6. Repetitive transitions
7. Inconsistent paragraph flow
8. Heading/body merge problems
9. Glossary term violations
10. Remaining Korean that should have been translated

Rules for revised_text:
- Preserve all source meaning, facts, numbers, names, dates, units, organization names, titles, and reporting boundaries.
- Do not add, omit, exaggerate, soften, dramatize, or add legal weight.
- Preserve GLOSSARY_all terms exactly when provided.
- Preserve structure tags such as [PAGE], [BLOCK], [TABLE], [VISUAL], [HEADER], [FOOTER], [PAGE_NUMBER].
- Preserve paragraph breaks unless restructuring is necessary to fix a real quality problem.
- If the input contains a markdown table or pipe table, do not change the table structure.

Return JSON only:
{
  "quality_score": 0-100,
  "has_critical_issue": true/false,
  "issues": [
    {
      "type": "GRAMMAR | COLLOCATION | TRANSLATIONESE | FLOW | GLOSSARY | STRUCTURE | KOREAN_LEFTOVER",
      "problem": "...",
      "recommendation": "..."
    }
  ],
  "revised_text": "..."
}
"""

# prompts.py:647-694
QA_SYSTEM_PROMPT = """You are a senior U.S. native English ESG report proofreader reviewing an English translation of a Korean ESG report.

## Glossary principle
- Glossary compliance = exact Korean key in source → exact English value in translation.
- Do NOT require a glossary value for partial Korean key matches.
- Accept the glossary value even if it sounds slightly stiff — never replace it with a synonym.
- Python handles most glossary checks. Only flag GLOSSARY_VIOLATION for obvious failures.

## Issue types — check ONLY these
| Type | When to use |
|------|-------------|
| GLOSSARY_VIOLATION | Exact Korean glossary key in source, but wrong English value in translation |
| TERMINOLOGY_INCONSISTENCY | Same repeated non-glossary Korean term translated differently across the page without contextual reason |
| TAG_MODIFIED | A required structural tag ([PAGE n], [BLOCK n], etc.) was changed or deleted |
| OMISSION | Meaningful Korean content is absent from the translation |
| UNTRANSLATED | Korean text remains in the translation output |
| NUMBER_UNIT_ERROR | Wrong number, percentage, unit, date, or scope boundary |
| GRAMMAR | Clear grammatical error that must be corrected |
| UNNATURAL | Grammatically correct but clearly Korean-influenced, machine-translated, formulaic, or using unnatural ESG/finance collocations |
| TABLE_CONTENT | Table cell content is wrong, missing, or untranslated |
| VISUAL_CONTENT | Caption, chart label, or visual annotation is wrong or untranslated |

## UNNATURAL detection guidance
Flag machine-translation patterns such as:
- literal noun stacks: "precise metrics management", "data accumulation", "sustainable direction"
- unnatural responsibility phrases: "bear the responsibility to" when the source only says role expanded
- awkward finance terms: "financial accessibility" where "financial inclusion" or "access to finance" is more natural
- Korean-style sentence rhythm where the English merely mirrors source order
- repeated formulaic transitions that make the prose sound generated rather than authored

## Do NOT flag
- Glossary values that sound slightly stiff (they are mandatory)
- UNNATURAL issues where the exact glossary value must be preserved
- ADDITION issues (content in translation not in source)
- Capitalization-only, singular/plural-only, or article-only differences
- Structural tag numbering mismatches without content error

## Output
Return a JSON array only. Each issue must have:
  - issue_type (from the table above)
  - severity: "HIGH" | "MEDIUM" | "LOW"
  - page_num
  - original_span: the exact English phrase to change (empty string if not applicable)
  - suggestion: the corrected expression
  - reason: concise Korean explanation

If no issues found, return [].
"""


# prompts.py:863-873
def editorial_qa_user(glossary_text: str, source_text: str, translated_text: str, page_num: int | None) -> str:
    return f"""[Mandatory glossary terms from GLOSSARY_all]
{glossary_text}

[Original Korean source{f" - PAGE {page_num}" if page_num else ""}]
{source_text}

[Draft English translation]
{translated_text}

Review the draft and return JSON only."""


# prompts.py:937-991
def review_user(
    variant_label: str,
    glossary_text: str,
    page_num: int,
    source_for_review: str,
    annotation_hint_text: str,
    translated_for_model: str,
) -> str:
    return f"""[Variant]
Style {variant_label}

[Relevant glossary terms for this page only]
{glossary_text}

[Korean source - PAGE {page_num}]
{source_for_review}

[Korean annotations found in the bilingual output - source evidence only, not errors]
{annotation_hint_text}

[English translation for QA - PAGE {page_num}]
{translated_for_model}

Review the English translation against the Korean source as a senior U.S. native English ESG report proofreader.

Return only definite issues as a JSON array.

Check:
1. translation accuracy,
2. non-glossary terminology consistency,
3. numbers, units, dates, names, tables, and visual text,
4. publication-ready American English quality.

Glossary compliance itself is checked separately by Python after your review.

Glossary review instruction:
- The glossary above includes only Korean glossary keys that appear exactly on this page.
- If a Korean glossary key appears exactly in the source, the corresponding English glossary value must appear in the translation.
- The glossary value must be preserved exactly, except capitalization and line-break/spacing differences.
- Do not require a glossary value when only part of the Korean glossary key appears in the source.
- Do not infer glossary violations from partial Korean matches.
- Do not flag capitalization-only differences.
- If the glossary value is already present, do not flag it as unnatural merely because another synonym sounds smoother.
- If you suggest a correction around a glossary value, keep the glossary value exactly.

Non-glossary terminology consistency instruction:
- Still check whether repeated non-glossary Korean nouns and key terms are translated consistently within this page.
- Use TERMINOLOGY_INCONSISTENCY only when the same Korean term is clearly translated into multiple different English terms without contextual reason.
- Do not use TERMINOLOGY_INCONSISTENCY for missing glossary values.
- Do not flag capitalization-only, singular/plural-only, or article-only differences.

Important:
- Do not report standalone 'not in source/source missing/unsupported addition' issues unless there is a clear factual contradiction, number/date/entity error, omission, mistranslation, or untranslated Korean.
- Do not suggest optional rewrites.
- Flag only issues that clearly require correction before publication."""


# gates.py:21-22
GENERIC_EDITORIAL_QA_MIN_SCORE = 96
GENERIC_EDITORIAL_QA_SOFT_SCORE = 95

# gates.py:182-194
CRITICAL_ISSUE_TYPES = frozenset({"GRAMMAR", "GLOSSARY", "STRUCTURE", "KOREAN_LEFTOVER"})
SOFT_ISSUE_TYPES = frozenset({"COLLOCATION", "TRANSLATIONESE", "FLOW"})


def must_apply(score: int, has_critical_issue: bool, issue_types: set[str], issue_count: int) -> bool:
    """R/2715-2733. issue_types 는 dict 이슈의 str(type).upper() 집합, issue_count 는 `len(issues)`(dict 가 아닌 원소도
    센다 — 코랩대로)."""
    return (
        has_critical_issue
        or bool(issue_types & CRITICAL_ISSUE_TYPES)
        or score < GENERIC_EDITORIAL_QA_MIN_SCORE
        or (score < GENERIC_EDITORIAL_QA_SOFT_SCORE and issue_count >= 2 and bool(issue_types & SOFT_ISSUE_TYPES))
    )

# review_rules.py:65-79
ALLOWED_ISSUE_TYPES = frozenset(
    {
        "GLOSSARY_VIOLATION",
        "TERMINOLOGY_INCONSISTENCY",
        "TAG_MODIFIED",
        "OMISSION",
        "UNTRANSLATED",
        "NUMBER_UNIT_ERROR",
        "GRAMMAR",
        "UNNATURAL",
        "TABLE_CONTENT",
        "VISUAL_CONTENT",
    }
)

# ---- 베낀 글 끝 ----


# ---------- 거름(review_rules.sanitize_issues 중 셋만: 종류 허용 목록 · span 이 번역문 안 · 중복 제거. 용어집 보존·「원문에 없음」·일관성 낱말은 뺌) ----------
def sanitize3(issues, translated):
    out, seen = [], set()
    for raw in issues if isinstance(issues, list) else []:
        if not isinstance(raw, dict):
            continue
        itype = str(raw.get("issue_type") or "UNNATURAL").upper()
        if itype not in ALLOWED_ISSUE_TYPES:
            continue
        span = str(raw.get("original_span") or "")
        if span and span not in translated:
            span = ""
        item = {"issue_type": itype, "severity": str(raw.get("severity") or "MEDIUM").upper(), "original_span": span,
                "suggestion": str(raw.get("suggestion") or ""), "reason": str(raw.get("reason") or "")}
        key = (item["issue_type"], item["severity"], item["original_span"], item["suggestion"], item["reason"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_json(raw):
    """review_rules.parse_review_json 꼴 — 못 읽으면 (None, False)."""
    try:
        return json.loads(raw), True
    except (json.JSONDecodeError, TypeError):
        return None, False


# ---------- 답 꼴(json_schema strict — 최상위는 객체여야 해서 V0r 의 배열은 {"issues": [...]} 로 받는다) ----------
def _arr(props, required=None):
    return {"type": "array", "items": {"type": "object", "properties": props, "required": required or list(props),
                                       "additionalProperties": False}}


V0R_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["issues"], "properties": {"issues": _arr({
    "issue_type": {"type": "string"}, "severity": {"type": "string"}, "page_num": {"type": "integer"},
    "original_span": {"type": "string"}, "suggestion": {"type": "string"}, "reason": {"type": "string"}})}}
V0E_SCHEMA = {"type": "object", "additionalProperties": False,
              "required": ["quality_score", "has_critical_issue", "issues", "revised_text"],
              "properties": {"quality_score": {"type": "integer"}, "has_critical_issue": {"type": "boolean"},
                             "issues": _arr({"type": {"type": "string"}, "problem": {"type": "string"}, "recommendation": {"type": "string"}}),
                             "revised_text": {"type": "string"}}}
V2_CATS = ("number", "unit", "negation", "term", "omission", "heading", "year", "other")
V2_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["issues"], "properties": {"issues": _arr({
    "category": {"type": "string", "enum": list(V2_CATS)}, "original_span": {"type": "string"},
    "suggestion": {"type": "string"}, "reason": {"type": "string"}})}}

# V2 지시문 — 개발 셋(sam 6)으로 짜고 out/trx7/first-run.txt 에 얼린다(얼린 뒤 고치지 않는다 — 「고칠 후보」로만). 영문 ≤ 1,500자.
V2_INSTR = (  # 2026-11-05 얼림(01:17) 뒤 개발 셋 sam 6 으로 두 바퀴(1: 심은 3/3·깨끗 2/3 헛걸림 → 2: 표기 차이·용어집 밖 금지, 1,500자 안으로 보기 문구만 줄임) — sha 는 out/trx7/first-run.txt
    "You are checking one block of an English translation of a Korean ESG report against its Korean source. "
    "Compare them in this fixed order and report only definite errors that must be corrected before publication. "
    "Never flag capitalization, articles, singular/plural, paraphrase, style, or wording choices.\n"
    "1. number: every number, percentage, amount, and count in the source must appear with the same value in the translation.\n"
    "2. unit: units and scale words (million/billion/trillion, tons/tonnes, kg, kWh/MWh/GWh, tCO2e, KRW) must match the source.\n"
    "3. negation: a statement must not be affirmed where the source negates it, or negated where the source affirms it.\n"
    "4. term: only for entries listed in [Glossary] - if the Korean key appears in the source, the translation must use that "
    "English value; also flag an organization, committee, program, or document name that differs from its glossary value.\n"
    "5. omission: a whole sentence or a factual clause of the source is missing from the translation.\n"
    "6. heading: if the block has headings, each heading must translate its own Korean heading; flag a heading that repeats "
    "an earlier heading instead of translating its source.\n"
    "7. year: years, dates, and the year or version in a standard's name must match the source.\n"
    "For each error give the category, the exact English span to change (copied verbatim from the translation; empty only "
    "for an omission), a suggested replacement, and a one-line reason. If there is no definite error, return an empty list."
)


def glossary_text(items):
    """esg-app glossary.build_glossary_text 꼴: 「- kr -> en」 KR 길이 내림차순, 없으면 안내 한 줄."""
    if not items:
        return "(No relevant glossary terms for this source text.)"
    return "\n".join(f"- {it['kr']} -> {it['en']}" for it in sorted(items, key=lambda x: len(x["kr"]), reverse=True))


def build_prompt(variant, mat):
    """(instructions, input, schema). mat = materialize(unit)."""
    g = glossary_text(mat["glossary"])
    page = mat["page"]
    if variant == "V0r":
        return QA_SYSTEM_PROMPT, review_user("A", g, page, f"[PAGE {page}]\n{mat['ko']}", "", mat["en"]), V0R_SCHEMA
    if variant == "V0e":
        return GENERIC_EDITORIAL_QA_PROMPT, editorial_qa_user(g, mat["ko"], mat["en"], page), V0E_SCHEMA
    if variant in ("V2", "V3"):
        if not V2_INSTR:
            raise RuntimeError("V2_INSTR 가 비어 있다 — 얼림 뒤 개발 셋으로 채운다")
        return V2_INSTR, v2_user(g, mat["ko"], mat["en"]), V2_SCHEMA
    raise ValueError(variant)


def v2_user(g, ko, en):
    return f"[Glossary]\n{g}\n\n[Korean source]\n{ko}\n\n[English translation]\n{en}"


# ---------- 답 읽기 → 이슈 목록 + 걸림 ----------
def read_answer(variant, raw, mat):
    """→ {parsed, issues, flagged, extra}. V1 은 V0e 답을 다시 읽는다(호출 0)."""
    d, ok = parse_json(raw)
    if not ok or not isinstance(d, dict):
        return {"parsed": False, "issues": [], "flagged": False, "extra": {}}
    if variant == "V0r":
        issues = sanitize3(d.get("issues"), mat["en"])
        return {"parsed": True, "issues": issues, "flagged": len(issues) >= 1, "extra": {"raw_n": len(d.get("issues") or [])}}
    if variant in ("V0e", "V1"):
        issues = [i for i in (d.get("issues") or []) if isinstance(i, dict)]
        score = int(d.get("quality_score", 0))
        crit = bool(d.get("has_critical_issue"))
        types = {str(i.get("type", "")).upper() for i in issues}
        applied = must_apply(score, crit, types, len(d.get("issues") or []))
        flagged = applied if variant == "V0e" else len(issues) >= 1
        return {"parsed": True, "issues": issues, "flagged": flagged,
                "extra": {"score": score, "critical": crit, "applied": applied, "noted_not_applied": len(issues) >= 1 and not applied}}
    issues = [i for i in (d.get("issues") or []) if isinstance(i, dict)]
    return {"parsed": True, "issues": issues, "flagged": len(issues) >= 1, "extra": {}}


# ---------- 채점 ----------
def norm_words(s):
    return WORD_RE.findall((s or "").lower().replace("’", "'"))


def overlap(a, b):
    """정규화한 a·b 가 서로 품거나, 낱말 겹침 ≥ 짧은 쪽 낱말 수의 50%."""
    wa, wb = norm_words(a), norm_words(b)
    if not wa or not wb:
        return False
    sa, sb = " ".join(wa), " ".join(wb)
    if sa in sb or sb in sa:
        return True
    short = min(len(wa), len(wb))
    return len(set(wa) & set(wb)) >= 0.5 * short


def issue_text(issue):
    """span 이 없는 답(V0e)은 문제·권고 글을 span 대신 쓴다."""
    span = issue.get("original_span") or ""
    return span if span else " ".join(str(issue.get(k) or "") for k in ("problem", "recommendation"))


def issue_kind(issue):
    return str(issue.get("issue_type") or issue.get("category") or issue.get("type") or "").upper()


def targets_of(mat):
    """맞음을 재는 조각들: 심은 단위는 after(omit 은 before), 실제 단위는 틀린 자리 글."""
    p = mat.get("planted")
    if p:
        return [p["before"] if p["cat"] == "omit" else p["after"]], p["cat"]
    if mat.get("wrong"):
        return list(mat["wrong"]), "real"
    return [], "clean"


def hit_of(mat, issues):
    """→ span | block_only | miss (깨끗 단위는 None)."""
    targets, cat = targets_of(mat)
    if not targets:
        return None
    if not issues:
        return "miss"
    for it in issues:
        txt = issue_text(it)
        allt = " ".join(str(it.get(k) or "") for k in ("original_span", "suggestion", "reason", "problem", "recommendation"))
        if cat == "omit" and (issue_kind(it) in ("OMISSION",) or OMIT_RE.search(allt)):
            return "span"
        if cat == "heading" and HEAD_RE.search(allt):
            return "span"
        if any(overlap(txt, t) for t in targets):
            return "span"
    return "block_only"


def score_unit(mat, ans):
    """단위 하나의 채점 줄."""
    h = hit_of(mat, ans["issues"]) if ans["flagged"] else ("miss" if targets_of(mat)[0] else None)
    return {"id": mat["id"], "cat": mat["cat"], "split": mat["split"], "kind": mat["kind"], "flagged": ans["flagged"],
            "parsed": ans["parsed"], "hit": h, "false_alarm": (mat["cat"] == "clean" and ans["flagged"]),
            "n_issues": len(ans["issues"]), "kinds": sorted({issue_kind(i) for i in ans["issues"]}), **ans["extra"]}


# ---------- 단위: 뽑기·심기·되살리기 ----------
def sha16(s):
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def book_pairs(key):
    """공식판 짝(용어집 근거 짝 뺌 — glossary_check.official_pairs 와 같은 규칙) + ko/en id 와 블록 꼴."""
    from trlab.glossary_cands import pair_texts
    from trlab.glossary_check import load_gloss
    ev = set()
    for it in load_gloss(key)["items"]:
        for e in it["evidence"]:
            ev.update(e["ko"])
            ev.update(e["en"])
    out = []
    for p, kt, et, ko, en in pair_texts(key):
        if set(p["ko"]) & ev or set(p["en"]) & ev:
            continue
        kinds = {ko[i].kind for i in p["ko"]} | {en[i].kind for i in p["en"]}
        out.append({"ko": list(p["ko"]), "en": list(p["en"]), "page": p["page"], "ko_text": kt, "en_text": et,
                    "kind": kinds.pop() if len(kinds) == 1 else "mixed"})
    return out


def _sents(en):
    return SENT_RE.split(en)


def eligible(cat, pr, gloss):
    """심을 수 있는 짝: 수·연도는 KO 에도 같은 수가 있어야 하고(번역자가 더한 수를 바꾸지 않게), 용어는 기업 이름 갈래를 뺀다."""
    en, ko = pr["en_text"], pr["ko_text"]
    if cat == "num":
        return any(t in ko for t in NUM_TOKEN_RE.findall(en) if len(t) >= 2)
    if cat == "unit":
        return bool(UNIT_NUM_RE.search(en))
    if cat == "neg":
        return bool(NEG_RE.search(en))
    if cat == "year":
        return any(y in ko for y in YEAR_RE.findall(en))
    if cat == "omit":
        return len(_sents(en)) >= 3
    if cat == "term":
        return any(it.get("cat") != "기업" and it["kr"] in ko and it["en"] in en for it in gloss)
    return True


def heading_units(pairs):
    """같은 쪽에서 「제목 짝 → 문단 짝」이 두 번 나오는 차례 → [(h1, p1, h2, p2)] (문단 둘 합 EN ≤ 1,500자)."""
    by = {}
    for pr in pairs:
        by.setdefault(pr["page"], []).append(pr)
    out = []
    for page, ps in sorted(by.items()):
        hp = [(ps[i], ps[i + 1]) for i in range(len(ps) - 1) if ps[i]["kind"] == "heading" and ps[i + 1]["kind"] == "paragraph"
              and HEAD_PARA_MIN <= len(ps[i + 1]["en_text"])]
        for a in range(len(hp)):
            for b in range(a + 1, len(hp)):
                (h1, p1), (h2, p2) = hp[a], hp[b]
                if h1["en_text"].strip() != h2["en_text"].strip() and len(p1["en_text"]) + len(p2["en_text"]) <= HEAD_EN_MAX:
                    out.append((h1, p1, h2, p2))
                    break
            else:
                continue
            break
    return out


def draw(seed=SEED):
    """시드 510 로 자리마다 후보 CANDS_PER_SLOT(차례대로). 세 책 돌아가며. → {slots:[{slot, cat, split, kind, book, cands:[...]}]}"""
    from trlab.glossary_check import load_gloss
    rnd = random.Random(seed)
    books = ("sam", "cos", "kb")
    P = {k: book_pairs(k) for k in books}
    G = {k: load_gloss(k)["items"] for k in books}
    paras = {k: [pr for pr in P[k] if pr["kind"] == "paragraph" and EN_MIN <= len(pr["en_text"]) <= EN_MAX] for k in books}
    heads = {k: heading_units(P[k]) for k in books}
    used = set()
    slots = []
    turn = 0

    def pid(pr):
        return "+".join(pr["ko"])

    def take(cat, split, book, n_slot):
        nonlocal turn
        if cat == "heading" or (cat == "clean" and n_slot == "heading"):
            pool = [h for h in heads[book] if pid(h[0]) not in used]
            if not pool:  # 그 책에 남은 제목 단위가 없으면 다음 책
                book = books[(books.index(book) + 1) % 3]
                pool = [h for h in heads[book] if pid(h[0]) not in used]
            picks = rnd.sample(pool, min(CANDS_PER_SLOT, len(pool)))
            cands = [{"pairs": [{"ko": x["ko"], "en": x["en"]} for x in h], "page": h[0]["page"],
                      "ko": "\n\n".join(x["ko_text"] for x in h), "en": "\n\n".join(x["en_text"] for x in h)} for h in picks]
            used.add(pid(picks[0][0]))  # 제목 풀은 작아서(kb 3쪽) 첫 후보만 쓴 것으로 — 고른 단위의 겹침은 build 때 검사
            kind = "heading"
        else:
            pool = [pr for pr in paras[book] if pid(pr) not in used and (cat == "clean" or eligible(cat, pr, G[book]))]
            picks = rnd.sample(pool, min(CANDS_PER_SLOT, len(pool)))
            cands = [{"pairs": [{"ko": pr["ko"], "en": pr["en"]}], "page": pr["page"], "ko": pr["ko_text"], "en": pr["en_text"]}
                     for pr in picks]
            for pr in picks:
                used.add(pid(pr))
            kind = "paragraph"
        slots.append({"slot": f"{split}-{cat}-{len(slots):02d}", "cat": cat, "split": split, "kind": kind, "book": book, "cands": cands})

    for cat in CATS:
        for _ in range(PLAN_TEST[cat]):
            take(cat, "test", books[turn % 3], None)
            turn += 1
    for _ in range(CLEAN_TEST["paragraph"]):
        take("clean", "test", books[turn % 3], "paragraph")
        turn += 1
    for b in books[: CLEAN_TEST["heading"]]:
        take("clean", "test", b, "heading")
    for cat in PLAN_DEV:
        take(cat, "dev", "sam", None)
    for _ in range(CLEAN_DEV["paragraph"]):
        take("clean", "dev", "sam", "paragraph")
    return {"seed": seed, "slots": slots}


def apply_plant(en, planted):
    """before 가 EN 에 꼭 한 번 있어야 한다. omit 은 after '' (문장 하나 지움)."""
    if planted is None:
        return en
    if en.count(planted["before"]) != 1:
        raise ValueError(f"before 가 EN 에 {en.count(planted['before'])}번: {planted['before'][:40]!r}")
    out = en.replace(planted["before"], planted["after"], 1)
    return re.sub(r"[ \t]{2,}", " ", out).strip() if planted["cat"] == "omit" else out


def _texts_official(key, pairs):
    ko = {b.id: b for b in common.load(key, "ko")}
    en = {b.id: b for b in common.load(key, "en")}
    kt = "\n\n".join(" ".join(ko[i].text for i in p["ko"]) for p in pairs)
    et = "\n\n".join(" ".join(en[i].text for i in p["en"]) for p in pairs)
    return kt, et


def _texts_engine(model, pair):
    page, uid = pair.split(":")
    d = json.load(open(RES / model / f"p{page}.json"))
    ko = next(u.get("text", "") for u in d["units"] if u["id"] == uid)
    t = d["translations"][uid]
    return ko, " ".join(t) if isinstance(t, list) else str(t)


def materialize(unit, gloss_items=None):
    """정답 단위(글 없음) → 글 있는 단위. sha 가 정답과 다르면 ValueError."""
    from trlab.glossary_check import load_gloss
    gloss = gloss_items if gloss_items is not None else load_gloss(unit["book"])["items"]
    if unit["cat"] == "real":
        ko, en_clean = _texts_engine(unit["engine"]["model"], unit["engine"]["pair"])
    else:
        ko, en_clean = _texts_official(unit["book"], unit["pairs"])
    if sha16(ko) != unit["ko_sha"] or sha16(en_clean) != unit["en_sha"]:
        raise ValueError(f"{unit['id']}: 글 sha 가 정답과 다르다")
    en = apply_plant(en_clean, unit.get("planted"))
    if unit.get("planted") and sha16(en) != unit["en_planted_sha"]:
        raise ValueError(f"{unit['id']}: 심은 EN sha 가 정답과 다르다")
    wrong = [en_clean[s:e] for s, e in (r["wrong_at"] for r in unit.get("real", []) if r["label"] == "real")]
    return {"id": unit["id"], "book": unit["book"], "split": unit["split"], "kind": unit["kind"], "cat": unit["cat"],
            "page": unit["page"], "ko": ko, "en": en, "en_clean": en_clean, "planted": unit.get("planted"), "wrong": wrong,
            "glossary": [{"kr": gloss[i]["kr"], "en": gloss[i]["en"]} for i in unit["glossary"]]}


def gloss_idx(ko, gloss):
    return [i for i, it in enumerate(gloss) if it["kr"] in ko]


def make_unit(uid, book, split, kind, cat, pairs, page, ko, en_clean, planted, gloss, engine=None, real=None):
    en = apply_plant(en_clean, planted)
    u = {"id": uid, "book": book, "split": split, "kind": kind, "cat": cat, "pairs": pairs, "page": page,
         "ko_sha": sha16(ko), "en_sha": sha16(en_clean), "en_planted_sha": sha16(en) if planted else None,
         "ko_len": len(ko), "en_len": len(en), "planted": planted, "glossary": gloss_idx(ko, gloss)}
    if engine:
        u["engine"] = engine
    if real is not None:
        u["real"] = real
    return u
