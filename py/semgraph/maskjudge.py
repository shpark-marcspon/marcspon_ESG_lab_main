"""SG1e — 숫자 가린 짝짓기 판정(데이터 검증 RPT-9 방법 D 꼴을 그린워싱 판정에).

주장 45개(세 권 × G01–G15), 자료 = Q-r 차례(자기 단락 뺌)로 8천 자까지 채운 글(taskeval 과 같은 글).
ⓐ visible: taskeval 판정 지시문 그대로, 두 번.
ⓑ masked: 숫자 있는 주장만(연도만인 것 뺌). 코드가 주장·자료의 숫자를 [C1]·[E1] 표로 가리고(단위·만/억·%·연도는 둠),
   luna 는 같은 지표·범위·기간인 짝만 잇고(same_scope · 단서), 값 대조와 판정은 코드가 정한 규칙으로. 두 번.
하나씩 차례로 부른다(캐시), 받은 즉시 한 줄씩 적는다. 산출 <out>/sg1e/ · py/ 에서:
  python -m semgraph.maskjudge <visible|masked|score> --book …
"""

import argparse
import json
import re
from pathlib import Path

from .sg1d import MODEL, Book
from .taskeval import COARSE, VERDICTS, _budget_text, _gw, _judge, _keep, _kept, _respond

ARM, BUDGET = "Q-r 재순위(P-ts 상위 50)", 8000
NUM = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.,])\d+(?:\.\d+)?")
YEAR = re.compile(r"(19|20)\d\d")
# 지표 이름 안의 숫자(Scope 1, ISO 14001 …)는 가리지 않는다
NAMEY = re.compile(r"((Scope|SCOPE|scope|ISO|IEC|GRI|SASB|TCFD|IFRS|S|P|p\.|Tier|Level|레벨)\s?|(Scope|scope)\s?\d\s?[,&/·]\s?)$")
# 기간·쪽을 가리키는 숫자(5월 · 107~111 페이지)도 둔다
AFTERY = re.compile(r"^\s?(월|페이지|쪽|~\s?\d+\s?(페이지|쪽))")
SCALE = [("조", 1e12), ("억", 1e8), ("만", 1e4), ("천", 1e3)]

MK_INSTR = """너는 ESG 보고서의 그린워싱 점검을 돕는다. [주장]과 [자료]의 숫자 값은 모두 가려져 있다(주장 [C1]…, 자료 [E1]…).
단위·%·만/억·연도는 남아 있다. 값을 짐작하지 말고, 뜻만 보고 짝을 지어라.
- pairs: 주장의 각 [C] 와 같은 지표를 재는 자료의 [E]. same_scope = 지표·대상(회사·부문·사업장·경계)·기간·기준이 모두 같으면 true,
  지표는 같으나 범위·기간·기준이 다르면 false(그래도 적는다). qualifier = 주장보다 좁히거나 다르게 만드는 단서만(경계·일부만·예외·산정 방법 차이). 같은 기준임을 확인하는 설명은 적지 않는다(빈 글).
  passage 는 그 [E] 가 든 단락 id. 같은 [C] 에 여러 [E] 를 이어도 된다. 짝이 없으면 적지 않는다.
  relation = same(그 [E] 가 [C] 와 같은 값을 직접 적은 자리) · derivable([C] 가 증감량·증감률·비율처럼 계산된 값이고
  [E] 는 그 계산에 드는 값 — 예: 두 해의 실적). derivable 이면 계산에 드는 [E] 를 모두 적는다.
- word_contradicts: 숫자 말고 글로 주장과 어긋나는 단락(반대 사례·제재·범위 제외 등). word_qualifies: 주장을 좁히거나 단서를 다는 단락만(같은 기준·같은 범위임을 확인해 주는 단락은 넣지 않는다).
주장 문장이 든 단락은 근거가 아니다."""

MK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pairs", "word_contradicts", "word_qualifies"],
             "properties": {
                 "pairs": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                      "required": ["claim_token", "evidence_token", "passage", "same_scope", "relation", "qualifier"],
                                                      "properties": {"claim_token": {"type": "string"},
                                                                     "evidence_token": {"type": "string"},
                                                                     "passage": {"type": "string"},
                                                                     "same_scope": {"type": "boolean"},
                                                                     "relation": {"type": "string", "enum": ["same", "derivable"]},
                                                                     "qualifier": {"type": "string"}}}},
                 "word_contradicts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                 "required": ["passage", "what"],
                                                                 "properties": {"passage": {"type": "string"}, "what": {"type": "string"}}}},
                 "word_qualifies": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                               "required": ["passage", "what"],
                                                               "properties": {"passage": {"type": "string"}, "what": {"type": "string"}}}}}}


def _d(b):
    d = Path(b.out) / "sg1e"
    d.mkdir(exist_ok=True)
    return d


def _material(b, q):
    return _budget_text(b, json.load(open(b.d / "orders.json"))[q["id"]][ARM], BUDGET)


def mask(text, prefix, table):
    """숫자 → [C1] 꼴. table 에 {표: (값 글자, 바로 뒤 글 12자)}. 연도·지표 이름 숫자는 둔다."""
    out, last = [], 0
    for m in NUM.finditer(text):
        s = m.group()
        if (YEAR.fullmatch(s) or NAMEY.search(text[max(0, m.start() - 10):m.start()])
                or AFTERY.match(text[m.end():m.end() + 12])):
            continue
        tok = f"{prefix}{len(table) + 1}"
        table[tok] = (s, text[m.end():m.end() + 12])
        out.append(text[last:m.start()] + f"[{tok}]")
        last = m.end()
    return "".join(out) + text[last:]


def has_number(text):
    return bool(mask(text, "C", {}) != text)


def _masked_material(material, table):
    lines = []
    for line in material.split("\n"):
        m = re.match(r"(\[P\d+\] )(.*)", line)
        lines.append(m.group(1) + mask(m.group(2), "E", table) if m else mask(line, "E", table))
    return "\n".join(lines)


def value(s, after):
    """(원값, 배수 적용값, % 여부) — 바로 뒤 만/억/조/천과 % 만 본다."""
    v = float(s.replace(",", ""))
    a = after.lstrip()
    pct = a.startswith("%")
    mult = next((k for w, k in SCALE if a.startswith(w)), 1.0)
    return v, v * mult, pct


def same(x, y):
    tol = max(abs(x), abs(y)) * 0.01
    return abs(x - y) <= max(tol, 0.051 if max(abs(x), abs(y)) < 100 else tol)


LOOSE = False  # True = 「% 와 아닌 것」도 원값이 같으면 일치로(표 머리에 % 가 있어 값 옆에 없는 꼴) — 채점 뒤 손질, 따로 적음


def compare(cv, ev):
    """'match' | 'diff' | 'incomparable'(% 와 % 아닌 것)."""
    (c0, c1, cp), (e0, e1, ep) = cv, ev
    if cp != ep:
        return "match" if LOOSE and same(c0, e0) else "incomparable"
    return "match" if (same(c0, e0) or same(c1, e1) or same(c1, e0) or same(c0, e1)) else "diff"


def _derived(c, evs):
    """증감량·증감률·비율 — 짝지은 값 둘로 c 를 만들 수 있으면 match, 아니면 None(못 셈, 어긋남으로 치지 않음)."""
    c0, _, cp = c
    for i, (a0, a1, _) in enumerate(evs):
        for b0, b1, _ in evs[i + 1:]:
            for x, y in ((a0, b0), (a1, b1)):
                if not y or not x:
                    continue
                cands = [abs(x - y), abs(x - y) / max(x, y) * 100, abs(x - y) / min(x, y) * 100, min(x, y) / max(x, y) * 100]
                if any(same(c0, v) for v in cands):
                    return "match"
    return None


def decide(r, ctab, etab, fails):
    """고정 규칙. relation=same 짝만 값이 다르면 어긋남, derivable 은 코드가 계산해 맞으면 일치·못 맞히면 셈 밖.
    fails 에 맞추지 못한 짝·표를 적는다."""
    res, der = [], {}
    for p in r["pairs"]:
        c, e = p["claim_token"].strip("[]"), p["evidence_token"].strip("[]")
        if c not in ctab or e not in etab:
            fails.append({"why": "없는 표", "pair": p})
            continue
        if p["relation"] == "derivable":
            der.setdefault((c, p["same_scope"]), []).append((value(*etab[e]), bool(p["qualifier"].strip())))
            continue
        k = compare(value(*ctab[c]), value(*etab[e]))
        if k == "incomparable":
            fails.append({"why": "% 와 아닌 것", "pair": p, "c": ctab[c], "e": etab[e]})
            continue
        res.append((k, p["same_scope"], bool(p["qualifier"].strip())))
    for (c, sc), evs in der.items():
        if _derived(value(*ctab[c]), [v for v, _ in evs]) == "match":
            res.append(("match", sc, any(qf for _, qf in evs)))
        else:
            fails.append({"why": "계산으로 못 맞힘", "claim": c, "n": len(evs)})
    if r["word_contradicts"] or any(k == "diff" and s for k, s, _ in res):
        return "contradicted"
    matched = [(s, qf) for k, s, qf in res if k == "match"]
    if matched and (r["word_qualifies"] or any(qf for _, qf in matched) or not any(s for s, _ in matched)):
        return "partially_supported"
    if any(s for s, _ in matched):
        return "supported"
    if r["word_qualifies"]:
        return "partially_supported"
    return "unsupported" if not res else "partially_supported"


def cmd_visible(b):
    for run in (1, 2):
        part = _d(b) / f"visible{run}.part.jsonl"
        done = _kept(part)
        for q in _gw(b):
            if q["id"] in done:
                continue
            r = _judge(b, _material(b, q), q, f"sg1e:visible{run}:{q['id']}")
            _keep(part, q["id"], r)
            print(b.name, "visible", run, q["id"], r["verdict"], f"${r['usd']:.4f}", flush=True)


def cmd_masked(b):
    for run in (1, 2):
        part = _d(b) / f"masked{run}.part.jsonl"
        done = _kept(part)
        for q in _gw(b):
            if q["id"] in done or not has_number(q["text"]):
                continue
            ctab, etab = {}, {}
            claim = mask(q["text"], "C", ctab)
            mat = _masked_material(_material(b, q), etab)
            note = f"\n(이 주장은 단락 [{q['self_passage']}] 에서 나왔다 — 그 단락은 근거로 쓰지 않는다)"
            raw, row = _respond(MODEL, MK_INSTR, f"[자료]\n{mat}\n\n[주장]\n{claim}{note}", MK_SCHEMA, b.out,
                                f"sg1e:masked{run}:{q['id']}")
            r = json.loads(raw)
            fails = []
            r["verdict"] = decide(r, ctab, etab, fails)
            r.update({"usd": row["costUsd"], "fails": fails, "claim_masked": claim, "ctab": ctab,
                      "n_etok": len(etab), "etab_used": {p["evidence_token"].strip("[]"): etab.get(p["evidence_token"].strip("[]"))
                                                         for p in r["pairs"]}})
            _keep(part, q["id"], r)
            print(b.name, "masked", run, q["id"], r["verdict"], len(r["pairs"]), "짝", len(fails), "실패",
                  f"${row['costUsd']:.4f}", flush=True)


def load_runs(b):
    return {f"{k}{run}": _kept(_d(b) / f"{k}{run}.part.jsonl") for k in ("visible", "masked") for run in (1, 2)}


def expected(b):
    """check/claim-expected.json 이 있으면 그것({id: {verdict, contrary_ids, has_number}}),
    없으면 임시: SG1d contrary.json(정답 핵심만 준 luna 한 번) — 반대 근거 있음 = 「좁혀지거나 어긋남」, 없음 = 「성립」."""
    f = Path(b.out) / "check" / "claim-expected.json"
    if f.exists():
        return json.load(open(f)), "claim-expected.json"
    ct = json.load(open(b.d / "task" / "contrary.json"))
    return {k: {"verdict": None, "contrary": v["contrary"], "contrary_ids": v["ids"]} for k, v in ct.items()}, "임시(contrary.json)"


def _agree(x, y, keys, coarse=False):
    f = (lambda v: COARSE[v]) if coarse else (lambda v: v)
    ks = [k for k in keys if k in x and k in y]
    return sum(f(x[k]["verdict"]) == f(y[k]["verdict"]) for k in ks), len(ks)


def rescore(runs):
    """저장한 짝으로 판정을 코드 규칙만 다시 돌림(LLM 호출 없음)."""
    for run in ("masked1", "masked2"):
        for r in runs[run].values():
            etab = {k: tuple(v) for k, v in r["etab_used"].items() if v}
            fails = []
            r["verdict"] = decide(r, {k: tuple(v) for k, v in r["ctab"].items()}, etab, fails)
            r["fails"] = fails
    return runs


def cmd_score(b):
    global LOOSE
    LOOSE = "--loose" in __import__("sys").argv
    runs, (exp, src) = rescore(load_runs(b)), expected(b)
    allq = [q["id"] for q in _gw(b)]
    num = [q["id"] for q in _gw(b) if has_number(q["text"])]
    out = {"src": src, "n": len(allq), "n_num": len(num),
           "has_number_mismatch": [q for q in allq if q in exp and "has_number" in exp[q] and exp[q]["has_number"] != (q in num)]}
    for name, k, ks in (("visible 전체", "visible", allq), ("visible 숫자", "visible", num), ("masked 숫자", "masked", num)):
        x, y = runs[f"{k}1"], runs[f"{k}2"]
        row = {"self_exact": _agree(x, y, ks), "self_coarse": _agree(x, y, ks, True)}
        vs = [r for run in (x, y) for q, r in run.items() if q in ks]
        row["partially"] = (sum(r["verdict"] == "partially_supported" for r in vs), len(vs))
        if exp and next(iter(exp.values())).get("verdict"):
            row["exp_exact"] = tuple(map(sum, zip(*[_agree(run, exp, ks) for run in (x, y)])))
            row["exp_coarse"] = tuple(map(sum, zip(*[_agree(run, exp, ks, True) for run in (x, y)])))
        # 반대 근거 잡기 = 기대 partially 이면서 contrary 가 빈 목록이 아닌 것(기대 contradicted 는 0 — 조정자 11-01)
        contra = [q for q in ks if exp.get(q, {}).get("verdict") == "contradicted"
                  or (exp.get(q, {}).get("verdict") == "partially_supported" and exp[q].get("contrary"))]
        holds = [q for q in ks if q not in contra]
        row["contra_n"] = len(contra)
        row["catch_strict"] = (sum(run[q]["verdict"] == "contradicted" for run in (x, y) for q in contra if q in run), 2 * len(contra))
        row["catch_lenient"] = (sum(run[q]["verdict"] in ("contradicted", "partially_supported") for run in (x, y) for q in contra if q in run), 2 * len(contra))
        row["false_contra"] = (sum(run[q]["verdict"] == "contradicted" for run in (x, y) for q in holds if q in run), 2 * len(holds))
        row["contradicted_out"] = (sum(r["verdict"] == "contradicted" for r in vs), len(vs))
        out[name] = row
    fails = [f["why"] for run in ("masked1", "masked2") for r in runs[run].values() for f in r["fails"]]
    out["fails"] = {w: fails.count(w) for w in set(fails)}
    out["pairs"] = sum(len(r["pairs"]) for run in ("masked1", "masked2") for r in runs[run].values())
    out["usd"] = sum(r["usd"] for run in runs.values() for r in run.values())
    json.dump(out, open(_d(b) / ("score-loose.json" if LOOSE else "score.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--book", required=True)
    ap.add_argument("--loose", action="store_true")
    a = ap.parse_args(argv)
    b = Book(a.book, "gold-final.json")
    globals()[f"cmd_{a.cmd}"](b)


if __name__ == "__main__":
    main()
