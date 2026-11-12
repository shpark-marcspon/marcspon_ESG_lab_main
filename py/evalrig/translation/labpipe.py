"""MA2 랩 꼴 — 「싼 모델 번역 → 코드 검사 → 걸린 것만 비싼 모델」(2차 엔진 꼴의 시제품, 2차 엔진 아님) 단계 함수.

`band-handoffs/MA2-order.md` §2 (2) 표의 ①~⑨. 여기는 **순수 몫**(블록·답 → 판정)만 둔다 — 부르는 몫(장부·호출)은
`measure_ma2`. 1차 프롬프트 모듈(esg-app colab_158 `prompts.py`)은 인자 `P` 로 받는다(파일 경로로 읽는 것은 measure_ma2 —
시험은 지은 모듈을 넘긴다). trlab 부품은 import 만(`omit_rule`·`fullrev`·`span_incons`·`glossary_check`·`review_probe`).

① 입력      `input_blocks` — 분해본 body 블록(되풀이 띠·꼬리말 뺌, EVA `cli.ko_pages` 와 같은 거름), 쪽 범위.
② 구획      `sections` — c3 목차 항목의 시작 쪽을 경계로 범위를 자름(항목 겹침은 시작 쪽만 보므로 가장 잘게) ·
            `toc_calls` — 구획 안 블록을 **쪽 경계로만** ≤ CALL_MAX_KO_CHARS 국문 글자씩(한 쪽이 넘으면 그 쪽 혼자) ·
            `page_calls` — 쪽 하나 = 호출 하나(M-T2 page 판).
③ 지시      `system_prompt` = 1차 `page_system_prompt(build_company_style_prompt("general", …), STYLE_A, IS)` ·
            `user_prompt` = 1차 `table_batch_user(용어집 글, items JSON)` · 답 꼴 ITEMS_SCHEMA(`{id, translation}` —
            1차 table_batch_user 글이 「translation」 칸을 말하므로 그 이름, trpdf 선례) · `read_items`.
④ 코드 검사 `code_checks` — (a) 용어집 엄격 위반 (b) 문장 수(문단만, EN < 8자·EN 문장 0 블록 뺌) (c) 숫자 (d) 남은 한글.
⑤ 되묻기    누락 = `omit_rule.build_reask`/`read_reask` 그대로 · 숫자 = NUM_REASK_INSTR(새 글) + `read_num_reask`.
⑥ 체크리스트 `fullrev.bundle_plan`·`build_bundle_prompt`·`split_bundle_answer` 그대로(V2 지시문).
⑦ 모음      `span_incons` S1·S2·S4 그대로 + `split_inside_between`(구획 안/사이 가르기).
⑧ 고침      `fix_targets`(걸린 블록만, 「⑥ 이슈만 · EN < 8자」 뺌, 상한 FIX_CAP) · FIX_INSTR · `fix_input`.
⑨ 사람 확인 `residual` — 고친 뒤 코드 검사를 다시(호출 0) 해 갈래별 남은 블록.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

CALL_MAX_KO_CHARS = 12000  # ② 호출 하나의 국문 글자 상한(쪽 경계로만 자름)
SHORT_EN = 8  # ④(b)·⑧ — EN 이 이보다 짧으면 문장 수 검사·「⑥ 이슈만」 고침에서 뺌(TRX10 「고칠 후보」를 이 밴드 규칙으로 얼림)
FIX_CAP = 60  # ⑧ 고침 블록 상한(감독 답 ④)
CHK_MAX_CHARS = 8000  # ⑥ fullrev.bundle_plan max_chars
CHK_SEED = 514
SPAN_MIN_OCC = 3
IS = "GRI Standards 2021, IFRS S1/S2 (ISSB), SASB, TCFD"  # trpdf 의 IS 글 그대로(발주서 §2 (1))
PART = "general"

_HANGUL = re.compile(r"[가-힣]")


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


# ---------- ① 입력 ----------
def input_blocks(blocks: Iterable[Any], lo: int, hi: int) -> list[dict[str, Any]]:
    """trlab Block 목록 → body 블록(쪽 lo..hi) `{id, page, order, kind, ko}` (쪽·차례 순)."""
    out = [{"id": b.id, "page": b.page, "order": b.order, "kind": b.kind, "ko": b.text}
           for b in blocks if b.region == "body" and lo <= b.page <= hi and (b.text or "").strip()]
    return sorted(out, key=lambda x: (x["page"], x["order"]))


# ---------- ② 구획 ----------
def sections(toc_items: Iterable[dict[str, Any]], lo: int, hi: int) -> list[tuple[int, int]]:
    """목차 항목(start_page 칸) → 범위 lo..hi 를 자른 구획 [(시작, 끝)]. 경계 = 범위 안 항목 시작 쪽(+ lo).
    겹친 항목(대·중)은 시작 쪽만 보므로 가장 잘게 자른 구획이 된다."""
    starts = sorted({lo} | {int(it["start_page"]) for it in toc_items if lo < int(it["start_page"]) <= hi})
    return [(s, (starts[i + 1] - 1) if i + 1 < len(starts) else hi) for i, s in enumerate(starts)]


def section_of(secs: Sequence[tuple[int, int]], page: int) -> int:
    for i, (a, b) in enumerate(secs):
        if a <= page <= b:
            return i
    raise ValueError(f"쪽 {page} 이 구획 밖")


def _by_page(blocks: Sequence[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for b in blocks:
        out[b["page"]].append(b)
    return dict(sorted(out.items()))


def toc_calls(blocks: Sequence[dict[str, Any]], secs: Sequence[tuple[int, int]],
              max_chars: int = CALL_MAX_KO_CHARS) -> list[dict[str, Any]]:
    """구획 안 블록을 쪽 경계로만 ≤ max_chars 국문 글자씩 묶음 → [{call, section, pages, ids}]. 한 쪽이 넘으면 그 쪽 혼자."""
    pages = _by_page(blocks)
    out: list[dict[str, Any]] = []
    for si, (a, b) in enumerate(secs):
        cur: list[int] = []
        size = 0
        for pg in [p for p in pages if a <= p <= b]:
            n = sum(len(x["ko"]) for x in pages[pg])
            if cur and size + n > max_chars:
                out.append({"section": si, "pages": cur})
                cur, size = [], 0
            cur.append(pg)
            size += n
        if cur:
            out.append({"section": si, "pages": cur})
    for i, c in enumerate(out):
        c["call"] = i
        c["ids"] = [x["id"] for p in c["pages"] for x in pages[p]]
        c["ko_chars"] = sum(len(x["ko"]) for p in c["pages"] for x in pages[p])
    return out


def page_calls(blocks: Sequence[dict[str, Any]], secs: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
    """쪽 하나 = 호출 하나(M-T2 page 판 — 1차 꼴의 파트 안 쪽 순차와 같은 문맥 폭)."""
    pages = _by_page(blocks)
    return [{"call": i, "section": section_of(secs, pg), "pages": [pg], "ids": [x["id"] for x in pages[pg]],
             "ko_chars": sum(len(x["ko"]) for x in pages[pg])} for i, pg in enumerate(pages)]


# ---------- ③ 지시 ----------
ITEMS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "translation"],
                               "properties": {"id": {"type": "string"}, "translation": {"type": "string"}}}}}}

SUBJECT_HEAD = "Subject pronoun consistency guidance:\n"
FRAGMENT_HEAD = "\n\n\nFragmentary content (heading/table/chart) translation rules:\n"

# M-T3 third 판 — 「Subject pronoun…」 조각만 갈아 끼움(러너가 영어로 씀, measure_ma2 freeze 가 sha 를 얼림).
THIRD_SUBJECT = """Subject pronouns — MANDATORY:
Primary reference to the company: the company name, then "it" / "its"

Rule:
- First sentence where company is subject in a paragraph → company name
- All subsequent sentences in same paragraph → "it" / "its" (never "we" / "our" / "us")
- New major section → company name may reappear once, then revert to "it" / "its"

WRONG: "ABC Corp expanded green finance. We also strengthened accessibility. Our targets are..."
RIGHT: "ABC Corp expanded green finance. It also strengthened accessibility. Its targets are..."

Exceptions — always company name, never "it":
- Table cells, KPI labels, column/row headers, captions
- Formal certifications or legal disclosures
- Sentences where "it" would be ambiguous (group/subsidiary context)

CEO message: "I" for executive's personal voice; the company name or "it" for company-wide actions."""


def subject_block(P: Any, subject_text: str) -> str:
    """1차 `SUBJECT_FRAGMENT_RULES_BLOCK` 과 같은 틀(머리 둘 + 조각 둘)에 주어 조각만 갈아 끼움.
    `subject_block(P, P.SUBJECT_PRONOUN_PROMPT)` 은 1차 덩어리와 바이트 같다(measure_ma2 freeze 가 단언)."""
    return f"{SUBJECT_HEAD}{subject_text.strip()}{FRAGMENT_HEAD}{P.FRAGMENTARY_CONTENT_PROMPT.strip()}"


def subject_rules(P: Any, variant: str) -> str:
    """지칭 덩어리. we = 1차 `SUBJECT_FRAGMENT_RULES_BLOCK` 바이트 그대로, third = 「Subject pronoun…」 조각만 THIRD_SUBJECT."""
    if variant == "we":
        return P.SUBJECT_FRAGMENT_RULES_BLOCK
    if variant == "third":
        return subject_block(P, THIRD_SUBJECT)
    raise ValueError(variant)


def system_prompt(P: Any, company_name: str, preferred_subject: str, subject: str = "we") -> str:
    style = P.build_company_style_prompt(PART, company_name, preferred_subject, IS, None,
                                         subject_fragment_rules=subject_rules(P, subject))
    return P.page_system_prompt(style, P.STYLE_A_PROMPT, IS)


def call_glossary(gloss: Sequence[dict[str, Any]], ko_text: str) -> list[dict[str, str]]:
    """그 호출 국문에 든 KR 키만(1차 꼴)."""
    return [{"kr": it["kr"], "en": it["en"]} for it in gloss if it["kr"] in ko_text]


def glossary_text(items: Sequence[dict[str, str]]) -> str:
    """1차 `build_glossary_text` 꼴(trlab review_probe.glossary_text 그대로)."""
    from trlab.review_probe import glossary_text as gt
    return gt(list(items))


def user_prompt(P: Any, call_blocks: Sequence[dict[str, Any]], gloss: Sequence[dict[str, Any]]) -> str:
    ko = "\n".join(b["ko"] for b in call_blocks)
    payload = json.dumps({"items": [{"id": b["id"], "text": b["ko"]} for b in call_blocks]}, ensure_ascii=False, indent=1)
    return P.table_batch_user(glossary_text(call_glossary(gloss, ko)), payload)


def read_items(raw: str | None, ids: Sequence[str]) -> dict[str, Any]:
    """items 답 → {parsed, en{id: 글}, missing[id](답 없음·빈 글), extra[id](모르는 id), dup[id]}. 첫 답만 쓴다."""
    try:
        d = json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        return {"parsed": False, "en": {}, "missing": list(ids), "extra": [], "dup": [], "empty": []}
    want = set(ids)
    en: dict[str, str] = {}
    extra, dup, empty = [], [], []
    for it in d["items"]:
        if not isinstance(it, dict):
            continue
        i, t = str(it.get("id", "")), it.get("translation")
        if i not in want:
            extra.append(i)
            continue
        if i in en:
            dup.append(i)
            continue
        if not isinstance(t, str) or not t.strip():
            empty.append(i)
            continue
        en[i] = t.strip()
    return {"parsed": True, "en": en, "missing": [i for i in ids if i not in en], "extra": extra, "dup": dup, "empty": empty}


# ---------- ④ 코드 검사 ----------
# (c) 숫자 — 국문 블록의 수(쉼표 뺀 정수·소수, 두 글자 이상) 중 EN 의 수 모임에 없는 것.
#     뺌: 「만·억·조·천」이 바로 붙은 수(1조 2,345억 → EN 은 billion 꼴로 바뀜) · 연도 범위 꼴(2023~2025, 2023-25).
NUM = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?")
KO_SCALE = re.compile(r"\s*[만억조천]")
YEAR_RANGE = re.compile(r"(?<!\d)(?:19|20)\d\d\s*[~\-–—]\s*(?:(?:19|20)?\d\d)(?!\d)")
NUM_MIN_LEN = 2  # trlab Block.nums 와 같음(각주 번호 1) 2) 류 뺌)


def _clean_num(s: str) -> str:
    s = s.replace(",", "").rstrip(".")
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s


def ko_numbers(ko: str) -> list[str]:
    """국문 수(차례, 겹침 뺌)."""
    masked = YEAR_RANGE.sub(lambda m: " " * len(m.group(0)), ko or "")
    out: list[str] = []
    for m in NUM.finditer(masked):
        if KO_SCALE.match(masked, m.end()):
            continue
        n = _clean_num(m.group(0))
        if len(n.replace(".", "")) >= NUM_MIN_LEN and n not in out:
            out.append(n)
    return out


def en_numbers(en: str) -> set[str]:
    return {_clean_num(m.group(0)) for m in NUM.finditer(en or "")}


def missing_numbers(ko: str, en: str) -> list[str]:
    have = en_numbers(en)
    return [n for n in ko_numbers(ko) if n not in have]


def omit_check(ko: str, en: str, kind: str) -> dict[str, Any]:
    """omit_rule.check — 문단·mixed 만, EN < SHORT_EN 자·EN 문장 0 블록은 뺌(applies False)."""
    from trlab import omit_rule as OR
    r = OR.check(ko, en, kind)
    if r["applies"] and (len((en or "").strip()) < SHORT_EN or r["n_en"] == 0):
        r = dict(r, applies=False, flagged=None, excluded="short_en")
    return r


def code_checks(block: dict[str, Any], en: str | None, gloss: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """블록 하나 → {term[kr], omit{…}, num[수], hangul bool, missing bool}. en 이 None 이면 missing."""
    from trlab.glossary_check import strict_present
    if en is None:
        return {"missing": True, "term": [], "omit": {"applies": False, "flagged": None}, "num": [], "hangul": False}
    ko = block["ko"]
    term = [it["kr"] for it in gloss if it["kr"] in ko and not strict_present(it["en"], en)]
    return {"missing": False, "term": term, "omit": omit_check(ko, en, block["kind"]),
            "num": missing_numbers(ko, en), "hangul": bool(_HANGUL.search(en))}


def flags_of(chk: dict[str, Any]) -> list[str]:
    out = []
    if chk["missing"]:
        out.append("missing")
    if chk["term"]:
        out.append("term")
    if chk["omit"].get("flagged"):
        out.append("omit")
    if chk["num"]:
        out.append("num")
    if chk["hangul"]:
        out.append("hangul")
    return out


# ---------- ⑤ 되묻기 ----------
NUM_REASK_INSTR = (
    "You are given one block of a Korean ESG report, its English translation, and a numbered list of numbers that appear in "
    "the Korean source but were not found written the same way in the English translation. For each listed number, decide "
    "whether its value is present in the English translation in any form (a different format, a word, a unit conversion, a "
    "rounded or recomputed equivalent, or a date written out all count as present). Report only the numbers whose value is "
    "entirely absent from the English translation. If every listed number is present, return an empty list. Give a one-line reason."
)
NUM_REASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["missing", "reason"],
                    "properties": {"missing": {"type": "array", "items": {"type": "integer"}}, "reason": {"type": "string"}}}


def build_num_reask(ko: str, en: str, nums: Sequence[str]) -> tuple[str, str, dict[str, Any]]:
    listed = "\n".join(f"({i + 1}) {n}" for i, n in enumerate(nums))
    return NUM_REASK_INSTR, f"[Numbers to check]\n{listed}\n\n[Korean source]\n{ko}\n\n[English translation]\n{en}", NUM_REASK_SCHEMA


def read_num_reask(raw: str | None, nums: Sequence[str]) -> dict[str, Any]:
    """→ {parsed, missing[수 글](목록 안), oob[int], reason}. omit_rule.read_reask 와 같은 꼴(번호 → 수 글)."""
    from trlab.omit_rule import read_reask
    r = read_reask(raw, len(nums))
    return {"parsed": r["parsed"], "missing": [nums[i - 1] for i in r["missing"]], "oob": r["oob"], "reason": r["reason"]}


# ---------- ⑥ 블록 체크리스트 ----------
def chk_mats(blocks: Sequence[dict[str, Any]], en: dict[str, str], gloss: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """fullrev.bundle_plan 이 받는 꼴(id·ko·en·glossary). EN 이 없는 블록은 뺌."""
    return [{"id": b["id"], "page": b["page"], "kind": b["kind"], "ko": b["ko"], "en": en[b["id"]],
             "glossary": call_glossary(gloss, b["ko"])} for b in blocks if b["id"] in en]


def chk_plan(mats: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    from trlab import fullrev as F
    return F.bundle_plan(mats, max_chars=CHK_MAX_CHARS, seed=CHK_SEED)


# ---------- ⑦ 모음(S1·S2·S4) ----------
def span_pairs(units: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """`{id, page, ko, en}` — 랩은 블록, 1차는 쪽 단위. EN 이 빈 단위는 뺌."""
    return [{"id": u["id"], "page": u["page"], "ko": u["ko"], "en": u["en"]} for u in units if (u.get("en") or "").strip()]


def _host(key: str, hosts: Iterable[str]) -> str:
    for h in sorted(hosts, key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", h):
            return h
    return key


def split_inside_between(flags: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]],
                         secs: Sequence[tuple[int, int]]) -> dict[str, Any]:
    """걸린 용어마다: 같은 구획 안에서 키가 ≥ 2 로 갈리면 inside, 구획마다는 하나뿐이고 구획끼리만 다르면 between.
    키는 걸림의 renderings 키로 접는다(S2·S4 는 품는 표현 합침 — 짧은 키는 그것을 품는 긴 키로)."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["kind"] in ("exact", "variant") and r["key"]:
            by[r["term"]].append(r)
    inside, between = [], []
    for f in flags:
        hosts = [k for k, _, _ in f["renderings"]]
        per_sec: dict[int, set[str]] = defaultdict(set)
        for r in by.get(f["kr"], []):
            per_sec[section_of(secs, r["page"])].add(_host(r["key"], hosts) if f["stage"] != "S1" else r["key"])
        (inside if any(len(v) >= 2 for v in per_sec.values()) else between).append(f["kr"])
    return {"inside": inside, "between": between, "n_inside": len(inside), "n_between": len(between)}


def residual_incons_blocks(flags: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]]) -> dict[str, list[str]]:
    """S4 에 남은 용어마다 최빈 키가 아닌 표현을 쓴 짝(블록) → {블록 id: [용어]} — ⑧ 고침 대상."""
    out: dict[str, list[str]] = defaultdict(list)
    for f in flags:
        hosts = [k for k, _, _ in f["renderings"]]
        top = f["renderings"][0][0]
        for r in rows:
            if r["term"] == f["kr"] and r["kind"] in ("exact", "variant") and r["key"] and _host(r["key"], hosts) != top:
                if f["kr"] not in out[r["pair"]]:
                    out[r["pair"]].append(f["kr"])
    return dict(out)


# ---------- ⑧ 고침 ----------
FIX_INSTR = (
    "You are correcting one block of an English translation of a Korean ESG report. You get the Korean source, the current "
    "English translation, the mandatory glossary entries for this block, and a list of problems that automated checks and a "
    "reviewer found. Fix only the listed problems: keep every other sentence and wording of the current translation unchanged, "
    "keep all numbers, units and names exactly as in the source, and use each glossary English value exactly when its Korean "
    "key appears in the source. If a listed problem is not actually an error, leave that part unchanged. "
    "Return the full corrected English translation of the block."
)
FIX_REASONS = ("term", "omit", "num", "incons", "chk")
FIX_PRIORITY = {"term": 0, "omit": 1, "num": 1, "incons": 2, "chk": 3}


def fix_targets(blocks: Sequence[dict[str, Any]], en: dict[str, str], reasons: dict[str, dict[str, list[Any]]],
                cap: int = FIX_CAP) -> dict[str, Any]:
    """reasons{블록 id: {term|omit|num|incons|chk: [까닭 …]}} → {targets[id](우선·쪽 차례, ≤ cap), excluded{id: 까닭},
    over_cap[id]}. ⑥(chk) 이슈만 있는 블록 중 EN < SHORT_EN 자(그림 라벨·짝 어긋남 꼴)는 고치지 않음.
    omit·num 은 ⑤ 되묻기가 확인한 것만 넘긴다(부르는 쪽 몫)."""
    order = {b["id"]: (b["page"], b["order"]) for b in blocks}
    cand, excluded = [], {}
    for bid, rs in reasons.items():
        kinds = [k for k in FIX_REASONS if rs.get(k)]
        if not kinds or bid not in en:
            continue
        if kinds == ["chk"] and len(en[bid].strip()) < SHORT_EN:
            excluded[bid] = "chk_only_short"
            continue
        cand.append((min(FIX_PRIORITY[k] for k in kinds), order.get(bid, (10 ** 6, 0)), bid))
    cand.sort()
    ids = [c[2] for c in cand]
    return {"targets": ids[:cap], "over_cap": ids[cap:], "excluded": excluded}


def fix_input(block: dict[str, Any], en: str, gloss: Sequence[dict[str, Any]], rs: dict[str, list[Any]]) -> str:
    lines = []
    for t in rs.get("term", []):
        g = next((x["en"] for x in gloss if x["kr"] == t), "")
        lines.append(f"- glossary: the Korean term \"{t}\" must be rendered as \"{g}\"")
    for s in rs.get("omit", []):
        lines.append(f"- omission: this Korean sentence is missing from the translation: {s}")
    for n in rs.get("num", []):
        lines.append(f"- number: the value {n} from the source is missing or changed in the translation")
    for t in rs.get("incons", []):
        lines.append(f"- consistency: the Korean term \"{t}\" is rendered differently here than elsewhere in the document")
    for it in rs.get("chk", []):
        lines.append(f"- {it.get('category', 'other')}: \"{it.get('original_span', '')}\" → \"{it.get('suggestion', '')}\" "
                     f"({it.get('reason', '')})")
    g = glossary_text(call_glossary(gloss, block["ko"]))
    payload = json.dumps({"items": [{"id": block["id"], "text": en}]}, ensure_ascii=False)
    return (f"[Glossary]\n{g}\n\n[Korean source]\n{block['ko']}\n\n[Current English translation]\n{en}\n\n"
            f"[Problems to fix]\n" + "\n".join(lines) + f"\n\n[Item]\n{payload}\n\n"
            "Return ONLY JSON: {\"items\": [{\"id\": \"<same id>\", \"translation\": \"<corrected English>\"}]}")


# ---------- ⑨ 남은 걸림 ----------
def residual(blocks: Sequence[dict[str, Any]], en: dict[str, str], gloss: Sequence[dict[str, Any]],
             unfixed: Iterable[str] = ()) -> dict[str, Any]:
    """고친 EN 에 코드 검사를 다시(호출 0) → 갈래별 남은 블록 수 + 고치지 못한 대상(상한·실패) 수."""
    c: Counter = Counter()
    ids = set()
    for b in blocks:
        fl = flags_of(code_checks(b, en.get(b["id"]), gloss))
        for f in fl:
            c[f] += 1
        if fl:
            ids.add(b["id"])
    un = sorted(set(unfixed))
    return {"by_kind": dict(sorted(c.items())), "blocks": len(ids | set(un)), "unfixed": len(un)}


# ---------- 쪽 단위 잇기 ----------
def page_join(blocks: Sequence[dict[str, Any]], en: dict[str, str]) -> dict[int, str]:
    """블록 EN 을 쪽 안 블록 차례로 이음(공백 하나) — 1차와 같은 쪽 구획. EN 없는 블록은 건너뜀."""
    out: dict[int, list[str]] = defaultdict(list)
    for b in sorted(blocks, key=lambda x: (x["page"], x["order"])):
        if en.get(b["id"]):
            out[b["page"]].append(en[b["id"]])
    return {p: " ".join(v) for p, v in sorted(out.items())}
