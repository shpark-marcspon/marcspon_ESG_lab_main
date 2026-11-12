"""T-c 회사 지칭(AI 0). 회사 이름·별칭은 GOLD/names/<키>.json(git 밖)에서 읽는다 — 코드에 실제 이름 0.

문장마다 회사를 가리키는 꼴을 센다: we(1인칭 we·our·us·ours·ourselves) / name(이름·별칭, 소유격 포함) / generic(the Company 류) / none.
주어 꼴 = 문장 앞 8낱말 안에서 처음 나온 지칭(「In 2025, we …」 도 we 주어).
지킴률(prompts.py:73-77): 문단(블록)의 첫 지칭 주어는 이름, 그 뒤 주어는 we.
바꾸기 스케치: we → the Company(주어·동사 수 일치·소유격·목적격) 규칙 판.

명령: python -m trlab.subject <키> [--site engine] → OUT/subject/<키>-*.json
"""

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from trlab.common import GOLD, OUT

FIRST = re.compile(r"\b(?:[Ww]e|[Oo]ur|[Oo]urs|[Oo]urselves|us|Us)\b")  # 대문자 US(나라) 제외
ABBR = re.compile(r"\b(?:Co|Ltd|Inc|Corp|No|e\.g|i\.e|etc|vs|Mr|Ms|Dr|approx|U\.S)\.$")
# TRX5 it/its(꼴 pronoun, 선택 인자 it_rule — 기본 None 은 지금과 같음). 대문자 IT(정보기술)·ITS 는 제외.
PRON = re.compile(r"\b[Ii]t(?:s|self)?\b")
# 허사 It(문장 첫 「It is/was (also/not) important …」 류 — 회사가 아님). 작은 목록, 보고에 그대로.
EXPLETIVE = re.compile(
    r"^(?:If|When|As|Although|While|Because|Since|Unless|Once|Whether|So that|Where)?\s*[Ii]t\s+"
    r"(?:is|was|has been|will be|would be|may be|can be|could be|should be|seems|appears|remains|becomes|takes|"
    r"turns out|makes|means)\s+"
    r"(?:(?:also|not|now|still|therefore|thus|then|increasingly|particularly|especially|very|quite|more|most|"
    r"highly|generally|often|always|clearly|widely|deemed|considered|expected|believed|anticipated)\s+)*"
    r"(?:important|necessary|essential|critical|crucial|vital|possible|impossible|likely|unlikely|clear|evident|"
    r"obvious|noted|noteworthy|worth|difficult|easy|hard|expected|anticipated|estimated|assumed|believed|confirmed|"
    r"recommended|required|desirable|time|a|an|the|that|to|for|no|not)\b")
IT_RULES = ("R1", "R2", "R3")


def names_for(key):
    d = json.load(open(GOLD / "names" / f"{key}.json"))
    n = sorted(d["names"], key=len, reverse=True)
    g = sorted(d["generic"], key=len, reverse=True)
    name_re = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(x) for x in n) + r")(?:['’]s)?(?![A-Za-z])")
    gen_re = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(x) for x in g) + r")(?:['’]s)?(?![A-Za-z])", re.I)  # 문장 첫 「The Company」
    return name_re, gen_re


def sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    parts, buf = [], ""
    for tok in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"‘'(])", text):
        buf = (buf + " " + tok).strip() if buf else tok
        if ABBR.search(buf):
            continue
        parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return [p for p in parts if len(p) > 3]


def refs(sent, name_re, gen_re, it_rule=None, prev=None):
    """[(위치, 꼴)] 차례대로. it_rule 이 None 이면 TRX1 과 같다(it/its 를 세지 않음).

    it_rule(TRX5, 꼴 pronoun — 허사 It 은 어느 갈래에서도 뺌):
      R1  it/its/itself 전부 pronoun(위끝).
      R2  같은 문장 앞쪽에 name·generic 지칭이 있거나, 앞 문장(prev, 같은 블록) 주어가 name·generic·pronoun 일 때만.
      R3  R2 + 앞 문장 주어가 we 일 때도 + 앞 문장이 none 이어도 그 앞(최대 2문장) 주어가 회사면.
    prev = 같은 블록 앞 문장들의 (all, subj) 목록(앞 문장이 마지막) — 블록 첫 문장이면 None 이나 빈 목록.
    """
    out = [(m.start(), "we") for m in FIRST.finditer(sent)]
    out += [(m.start(), "name") for m in name_re.finditer(sent)]
    out += [(m.start(), "generic") for m in gen_re.finditer(sent)]
    out.sort()
    if it_rule is None:
        return out
    if it_rule not in IT_RULES:
        raise ValueError(f"it_rule: {it_rule}")
    its = [m.start() for m in PRON.finditer(sent)]
    if not its or EXPLETIVE.match(sent):
        return out
    if it_rule != "R1":
        ok = any(k in ("name", "generic") and p < its[0] for p, k in out)
        if not ok and prev:
            sj = [s for _, s in prev]
            if sj[-1] in ("name", "generic", "pronoun"):
                ok = True
            elif it_rule == "R3":
                ok = sj[-1] == "we" or (sj[-1] == "none" and any(x in ("name", "generic", "pronoun") for x in sj[-3:-1]))
        if not ok:
            return out
    return sorted(out + [(p, "pronoun") for p in its])


def classify(sent, name_re, gen_re, it_rule=None, prev=None):
    r = refs(sent, name_re, gen_re, it_rule, prev)
    kinds = {k for _, k in r}
    allk = "none" if not kinds else (kinds.pop() if len(kinds) == 1 else "mixed")
    head = " ".join(sent.split()[:8])
    cut = len(head)
    subj = next((k for p, k in r if p < cut), "none")
    return allk, subj


# ---------- 자리 ----------
def official_units(key):
    from trlab.align import body
    from trlab.common import load
    return [{"id": b.id, "page": b.page, "kind": b.kind, "text": b.text} for b in body(load(key, "en"))]


def engine_units(model, res_dir=None):
    from trlab.common import ROOT
    res = Path(res_dir or ROOT / "esg-lab" / "out" / "trpdf" / "res") / model
    out = []
    for f in sorted(res.glob("p*.json")):
        d = json.load(open(f))
        kinds = {u["id"]: u.get("kind", "paragraph") for u in d["units"]}
        for uid, t in d["translations"].items():
            out.append({"id": f"{d['page']}:{uid}", "page": d["page"], "kind": kinds.get(uid, "paragraph"),
                        "text": " ".join(t) if isinstance(t, list) else str(t)})
    return out


def topics(key):
    """T-a 정답(en)의 중 항목 범위 [(이름, 시작, 끝, CEO 여부)]."""
    p = GOLD / "toc" / f"{key}-en.json"
    if not p.exists():
        return []
    out = []
    for it in json.load(open(p))["items"]:
        if it["level"] == 2 or it["text"].upper().startswith("FOUNDER"):
            ceo = bool(re.search(r"CEO|Chairman|Message", it["text"], re.I))
            out.append((it["text"], it["start_page"], it["end_page"], ceo))
    return out


def analyze(units, name_re, gen_re, tops=(), it_rule=None):
    """it_rule(TRX5) 이 None 이면 rows·res 가 TRX1 과 같다. 블록 안 문장을 차례로 돌며 prev 를 넘긴다(블록이 바뀌면 끊김).
    지킴률(rule_ok)·토픽 섞임(third) 셈은 바꾸지 않는다 — pronoun 은 we 도 third 도 아니다."""
    ceo_pages = {p for t, s, e, c in tops if c for p in range(s, e + 1)}
    dist = defaultdict(Counter)  # 띠(kind/ceo) → 꼴
    subj = defaultdict(Counter)
    para_mixed = para_n = 0
    rule_ok = rule_n = 0
    rows = []
    for u in units:
        band = "ceo" if u["page"] in ceo_pages else u["kind"]
        ss = sentences(u["text"])
        sj = []
        prev = []
        for s in ss:
            a, b = classify(s, name_re, gen_re, it_rule, prev)
            prev.append((a, b))
            dist[band][a] += 1
            subj[band][b] += 1
            rows.append({"unit": u["id"], "page": u["page"], "band": band, "sent": s, "all": a, "subj": b})
            if b != "none":
                sj.append(b)
        if u["kind"] == "paragraph" and len(sj) >= 2 and band != "ceo":
            para_n += 1
            para_mixed += len(set(sj)) > 1
            rule_n += 1
            rule_ok += sj[0] in ("name", "generic") and all(x == "we" for x in sj[1:])
    # 토픽마다 주어 꼴 섞임
    tmix = []
    for t, s, e, c in tops:
        if c:
            continue
        ks = Counter(r["subj"] for r in rows if s <= r["page"] <= e and r["subj"] != "none" and r["band"] == "paragraph")
        if sum(ks.values()) >= 3:
            we = ks["we"]
            third = ks["name"] + ks["generic"]
            tmix.append({"topic": t, "we": we, "third": third, "mixed": we > 0 and third > 0,
                         "minor_share": round(min(we, third) / (we + third), 3)})
    return {"dist": {k: dict(v) for k, v in dist.items()}, "subj": {k: dict(v) for k, v in subj.items()},
            "para_mixed": para_mixed, "para_n": para_n, "rule_ok": rule_ok, "rule_n": rule_n,
            "topics": tmix}, rows


# ---------- 바꾸기 스케치 we → the Company ----------
MODAL = {"will", "can", "may", "must", "should", "shall", "would", "could", "might"}
IRREG = {"are": "is", "have": "has", "were": "was", "do": "does", "go": "goes"}


def _third(verb):
    if verb in IRREG:
        return IRREG[verb]
    if re.search(r"(s|sh|ch|x|z|o)$", verb):
        return verb + "es"
    if re.search(r"[^aeiou]y$", verb):
        return verb[:-1] + "ies"
    return verb + "s"


def to_company(sent, label="the Company"):
    """규칙 판. 돌려줌: (바꾼 문장, 바꾼 자리 [(꼴, 동사 바뀜 여부)])."""
    sites = []

    def subj(m):
        adv, verb = m.group(2) or "", m.group(3)
        L = label[0].upper() + label[1:] if m.start() == 0 else label
        if verb.lower() in MODAL or verb.endswith("ed") or verb.lower() in ("was",):
            sites.append(("we", False))
            return f"{L} {adv}{verb}"
        nv = _third(verb.lower())
        sites.append(("we", nv != verb.lower()))
        return f"{L} {adv}{nv}"

    s = re.sub(r"\b([Ww]e)\s+((?:(?:also|further|still|already|currently|jointly|now|[a-z]+ly)\s+)*)([a-z]+)", subj, sent)
    s, n1 = re.subn(r"\bOur\b", label[0].upper() + label[1:] + "’s", s)
    s, n2 = re.subn(r"\bour\b", "its", s)
    s, n3 = re.subn(r"\b(?<![A-Z])us\b", "it", s)
    s, n4 = re.subn(r"\bourselves\b", "itself", s)
    sites += [("our", False)] * (n1 + n2) + [("us", False)] * n3 + [("ourselves", False)] * n4
    return s, sites


def sketch(rows, n=20, seed=479):
    pool = [r for r in rows if r["band"] == "paragraph" and FIRST.search(r["sent"])]
    cnt = Counter()
    verb_change = 0
    for r in pool:
        _, st = to_company(r["sent"])
        for k, v in st:
            cnt[k] += 1
            verb_change += v
    rnd = random.Random(seed)
    sample = rnd.sample(pool, min(n, len(pool)))
    return {"sentences_with_we": len(pool), "sites": dict(cnt), "subject_verb_changes": verb_change,
            "sample": [{"unit": r["unit"], "before": r["sent"], "after": to_company(r["sent"])[0]} for r in sample]}


def main(argv):
    key = argv[0]
    site = argv[argv.index("--site") + 1] if "--site" in argv else "official"
    name_re, gen_re = names_for(key)
    (OUT / "subject").mkdir(parents=True, exist_ok=True)
    tops = topics(key)
    jobs = [("official", official_units(key), tops)] if site == "official" else \
        [(m, engine_units(m), [("CEO", 4, 4, True)]) for m in ("gpt-6-luna", "gpt-6.1-sol", "engine-4.1-mini")]
    for tag, units, tp in jobs:
        res, rows = analyze(units, name_re, gen_re, tp)
        if tag == "official":
            res["sketch"] = sketch(rows)
        json.dump({"res": res, "rows": rows}, open(OUT / "subject" / f"{key}-{tag}.json", "w"), ensure_ascii=False, indent=1)
        tm = res["topics"]
        print(key, tag, json.dumps({"subj": res["subj"], "para_mixed": f"{res['para_mixed']}/{res['para_n']}",
                                    "rule_ok": f"{res['rule_ok']}/{res['rule_n']}",
                                    "topics_mixed": f"{sum(t['mixed'] for t in tm)}/{len(tm)}"}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
