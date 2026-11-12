"""TRX5 — 용어 불일치 검출 luna span 판. 짝·용어 풀은 TRX1 `glossary_check.inconsistency` 와 같은 규칙(그 함수를 부르지 않고 다시 씀).

풀: 짝 = official_pairs(key)(정답 용어집 근거 짝 뺌), KR 용어 = kr_terms 끝말 ≥ 3짝 ∪ 정답 용어집 KR, 긴 용어가 짧은 용어를 가림,
    짝 3개 미만 용어는 안 셈 → 항목 (짝 id, 쪽, KR 용어).
묶음: 책 안 쪽 차례로 짝을 모아 KO+EN 글자 합 ≤ max_chars(한 짝이 넘으면 혼자).
span: luna 에게 「이 KR 용어가 이 짝 EN 에서 어느 글자로 옮겨졌나」 — kind exact | variant | pronoun | omitted.
      코드 검증: span 이 그 짝 EN 안에 없으면 nospan, 답에 빠진 항목은 missing.
판(코드만): 정규화 키 = _lenient_norm(span) 앞 the 떼고 끝 s 떼기. 용어마다 exact|variant 키 —
      S1 서로 다른 키 ≥ 2 · S2 S1 + 품는 표현 합치기(inconsistency:155-161 규칙) · S3 S2 + 키마다 출현 ≥ 2 ·
      S4(선택) S2 걸림마다 sol 「같은 공식 명칭인가」 false 만 남김(판의 한 열, 라벨 대체 아님).
실호출·장부·상한은 tx5_pick 에(여기는 네트워크 0).
"""

import json
import re
from collections import Counter, defaultdict

from trlab.glossary_check import _lenient_norm, load_gloss, normalize_space, official_pairs

MIN_OCC = 3
KINDS = ("exact", "variant", "pronoun", "omitted", "nospan", "missing")

# sam 으로 짜고 얼림(out/trx5/first-run.txt) — cos·kb 첫 호출 뒤에는 고치지 않는다.
INSTR = """You are checking how Korean organization/office/program names were rendered in an official English translation of a Korean sustainability report.
You get numbered pairs. Each pair has a Korean passage (KO), its English translation (EN), and a list of Korean terms that occur in KO.
For each (pair, term), copy the EXACT English words from that pair's EN text that render the Korean term — verbatim, same spelling, capitalization and punctuation, no paraphrase. Never copy words from another pair. Copy only the words that correspond to the term itself (e.g. the name of the committee), not the surrounding sentence.
kind:
- "exact": the term is rendered as a name of an organization, office, committee, program, system, site or similar (a proper-name rendering, e.g. "Sustainability Committee", "ESG Management Council").
- "variant": the term is rendered by a different but equivalent expression that is not a proper-name rendering (a shortened form, a description, a common-noun paraphrase such as "the committee", "our plants", "the division"). Still copy it verbatim.
- "pronoun": the term is rendered only by a pronoun such as "we", "our", "it", "its", "the Company", "the Group" — copy that pronoun.
- "omitted": the term is not rendered at all in EN — span must be the empty string.
If the term occurs several times in the pair, give the first rendering. Answer every (pair, term) listed, exactly once, in order.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "term": {"type": "string"},
                    "span": {"type": "string"},
                    "kind": {"type": "string", "enum": ["exact", "variant", "pronoun", "omitted"]},
                },
                "required": ["pair", "term", "span", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

JUDGE_INSTR = """You are checking terminology consistency in an official English translation of a Korean sustainability report.
For each Korean term you get the different English renderings found in the translation, each with one example sentence fragment.
Decide for each term whether the renderings are the SAME official name written in different ways (inconsistent naming — e.g. "Board of Directors" vs "BOD", "Representative Director" vs "CEO", a company name with and without "Inc.", "Sustainability Committee" vs "Sustainable Management Committee"), or whether they are legitimately different (a pronoun or descriptive paraphrase used in context, references to genuinely different bodies, a partial match or extraction noise).
same_official_name: true when the renderings are variant spellings/forms of one official name and a glossary would want them unified; false otherwise. Give a short reason.
Answer every term listed, exactly once, in order.
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "same_official_name": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["term", "same_official_name", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


# ---------- 풀 ----------
def occ_by_term(pairs, kr, min_occ=MIN_OCC):
    """inconsistency:123-134 와 같은 셈 — 긴 용어가 그 자리에 있으면 짧은 용어는 그 짝에서 세지 않는다. {용어: [짝 번호]} (≥ min_occ 만)."""
    kr_sorted = sorted(kr, key=len, reverse=True)
    occ = defaultdict(list)
    for i, p in enumerate(pairs):
        masked = p["ko"]
        for t in kr_sorted:
            if t in masked:
                occ[t].append(i)
                masked = masked.replace(t, "□" * len(t))
    return {t: idx for t, idx in occ.items() if len(idx) >= min_occ}


def pool_from(pairs, gloss, min_occ=MIN_OCC):
    from trlab.glossary_cands import kr_terms
    terms = Counter()
    for p in pairs:
        terms.update(kr_terms(p["ko"]))
    gloss_kr = {it["kr"] for it in gloss}
    kr = {t for t, c in terms.items() if c >= min_occ} | gloss_kr
    occ = occ_by_term(pairs, kr, min_occ)
    ids = [p["id"] for p in pairs]
    assert len(ids) == len(set(ids)), "짝 id 겹침"
    items = [{"pair": pairs[i]["id"], "page": pairs[i]["page"], "term": t} for t in sorted(occ) for i in occ[t]]
    return {"pairs": pairs, "terms": sorted(occ), "gloss_kr": gloss_kr, "gloss": {it["kr"]: it["en"] for it in gloss},
            "items": items, "occ": occ}


def pool(key):
    pairs, dropped = official_pairs(key)
    P = pool_from(pairs, load_gloss(key)["items"])
    P["dropped"] = dropped
    return P


# ---------- 묶음 · 지시문 ----------
def batches(items, pairs, max_chars=12000):
    """책 안 쪽 차례(= pairs 차례)로 짝을 모아 KO+EN 글자 합 ≤ max_chars. 한 짝이 넘으면 혼자. [{pairs:[{id, page, ko, en, terms}]}]."""
    by_pair = defaultdict(list)
    for it in items:
        if it["term"] not in by_pair[it["pair"]]:
            by_pair[it["pair"]].append(it["term"])
    out, cur, size = [], [], 0
    for p in pairs:
        if p["id"] not in by_pair:
            continue
        n = len(p["ko"]) + len(p["en"])
        if cur and size + n > max_chars:
            out.append({"pairs": cur})
            cur, size = [], 0
        cur.append({"id": p["id"], "page": p["page"], "ko": p["ko"], "en": p["en"], "terms": by_pair[p["id"]]})
        size += n
    if cur:
        out.append({"pairs": cur})
    return out


def prompt(batch):
    lines = []
    for p in batch["pairs"]:
        lines.append(f"### pair {p['id']} (page {p['page']})")
        lines.append("terms: " + " | ".join(p["terms"]))
        lines.append("KO: " + normalize_space(p["ko"]))
        lines.append("EN: " + normalize_space(p["en"]))
        lines.append("")
    return "\n".join(lines)


def parse_answer(raw):
    d = json.loads(raw) if isinstance(raw, str) else raw
    return list(d.get("items", []))


def norm_key(span):
    k = _lenient_norm(span)
    k = re.sub(r"^the ", "", k)
    k = re.sub(r"s$", "", k)
    return k.strip()


def check(batch, answer):
    """답을 (짝, 용어)마다 하나로 맞추고 span 이 그 짝 EN 안에 있는지 본다(공백 접고 대소문자 무시). → [{pair, page, term, span, kind, key}]."""
    en = {p["id"]: normalize_space(p["en"]).casefold() for p in batch["pairs"]}
    page = {p["id"]: p["page"] for p in batch["pairs"]}
    got = {}
    for a in answer:
        got.setdefault((a.get("pair"), a.get("term")), a)
    rows = []
    for p in batch["pairs"]:
        for t in p["terms"]:
            a = got.get((p["id"], t))
            if a is None:
                rows.append({"pair": p["id"], "page": page[p["id"]], "term": t, "span": "", "kind": "missing", "key": ""})
                continue
            span = normalize_space(a.get("span", ""))
            kind = a.get("kind", "")
            if kind == "omitted":
                kind = "omitted" if not span else ("nospan" if span.casefold() not in en[p["id"]] else "variant")
            elif kind not in ("exact", "variant", "pronoun") or not span or span.casefold() not in en[p["id"]]:
                kind = "nospan"
            key = norm_key(span) if kind in ("exact", "variant") else ""
            rows.append({"pair": p["id"], "page": page[p["id"]], "term": t, "span": span, "kind": kind, "key": key})
    return rows


# ---------- 판 S1~S3 ----------
def merge_contained(rend, where, examples):
    """서로 품는 표현은 하나로(긴 쪽) — inconsistency:155-161 규칙 그대로."""
    keys = sorted(rend, key=len, reverse=True)
    merged, mwhere, mex = Counter(), defaultdict(list), {}
    for k in keys:
        host = next((m for m in merged if re.search(r"\b" + re.escape(k) + r"\b", m)), None)
        merged[host or k] += rend[k]
        mwhere[host or k] += where[k]
        mex.setdefault(host or k, examples[k])
    return merged, mwhere, mex


def judge_code(rows, stage, gloss_kr=()):
    """rows(check 산출)로 용어마다 exact|variant 키를 모아 S1·S2·S3 걸림을 낸다."""
    assert stage in ("S1", "S2", "S3")
    by = defaultdict(list)
    for r in rows:
        by[r["term"]].append(r)
    flags = []
    for t in sorted(by):
        rend, where, ex = Counter(), defaultdict(list), {}
        for r in by[t]:
            if r["kind"] in ("exact", "variant") and r["key"]:
                rend[r["key"]] += 1
                where[r["key"]].append(r["page"])
                ex.setdefault(r["key"], {"pair": r["pair"], "span": r["span"]})
        if stage != "S1":
            rend, where, ex = merge_contained(rend, where, ex)
        if stage == "S3":
            rend = Counter({k: c for k, c in rend.items() if c >= 2})
        if len(rend) >= 2:
            flags.append({"kr": t, "occ": len(by[t]), "stage": stage,
                          "renderings": [[k, c, sorted(set(where[k]))[:8]] for k, c in rend.most_common()],
                          "examples": {k: ex[k] for k in rend}, "in_glossary": t in set(gloss_kr),
                          "kinds": dict(Counter(r["kind"] for r in by[t]))})
    return flags


# ---------- S4 되묻기(sol) ----------
def _context(en, span, width=80):
    e = normalize_space(en)
    i = e.casefold().find(span.casefold())
    if i < 0:
        return e[:2 * width]
    return e[max(0, i - width): i + len(span) + width]


def judge_prompt(flags, pairs, width=80):
    """S2 걸림 용어마다 「KR 용어 + 서로 다른 span 들(각 1예문 ±80자)」. → (글, 항목 수)."""
    en = {p["id"]: p["en"] for p in pairs}
    lines = []
    for i, f in enumerate(flags, 1):
        lines.append(f"### term {i}: {f['kr']}")
        for k, c, _ in f["renderings"]:
            ex = f["examples"][k]
            lines.append(f"- rendering: \"{ex['span']}\" (x{c}) — …{_context(en.get(ex['pair'], ''), ex['span'], width)}…")
        lines.append("")
    return "\n".join(lines), len(flags)


def apply_judge(flags, answer):
    """같은 공식 명칭(true)이라 한 것만 남긴다 — S4 열. 답이 빠진 용어는 judge None 으로 남긴다(걸림 유지)."""
    got = {}
    for a in answer:
        got.setdefault(normalize_space(a.get("term", "")), a)
    out = []
    for f in flags:
        a = got.get(f["kr"])
        g = dict(f, stage="S4", judge=None if a is None else bool(a["same_official_name"]),
                 judge_reason=None if a is None else a.get("reason", ""))
        if a is None or a["same_official_name"]:
            out.append(g)
    return out
