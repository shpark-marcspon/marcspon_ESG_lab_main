"""SG1g — 있는 파서 보정을 붙인 단위 · 섹션째 넓히기. AI 0(바뀐 단락 임베딩만).

바닥: esg-app services/parser 의 layout 2 산출(읽기 전용으로 돌려 <out>/sg1g/app-l2/document.json, 위첨자 문단 잇기·
세로 병합 행 머리·단 넘는 표·region/key·막대·연표 오탐 거름). SG1d 까지는 esg-lab docparse 0.1.0 옛 산출이었다.
그 위에:
 a. 도표 라벨(esg-lab docparse charts, <out>/sg1g/charts-l2/chart-labels.json): 값 글자에 도표 제목·범주·계열이 붙은 도표를
    「도표」 단위 하나로 세우고, 그 값이 든 짧은 조각 블록을 흡수한다.
 b. layout 2 의 region 이 header·footer 인 블록을 뺀다.
 c. 각주(「1)」「*」「※」로 시작하는 문단)를 같은 쪽 가장 가까운 앞 표의 글 뒤에 「주:」로 붙인다.
 d. 요약 쪽: 400자 넘고 글머리표(• ■ ‒ -) 셋 이상인 단락은 글머리마다 쪼갠다(머리 줄은 항목마다).
 e. 쪽의 30% 이상에 되풀이되는 문단 글(머리띠·쪽 메뉴)을 뺀다.
단위는 passages.build 로 다시 세운다. 정답은 옛 단락 → 옛 블록 → 쪽·상자 겹침으로 새 블록 → 새 단락(구성 중 가장 센 것).
2단계: 후보(갈래 상위 15 ∪ P-ts 상위 50, 80개) 상위 20 에 같은 섹션이 셋 이상이면 그 섹션 단락을 다 넣는다(120개까지).
산출 <out>/sg1g/: passages.json|txt · gold-final.json · missed-before.json · emb-*.npy · eval.json|md
py/ 에서: python -m semgraph.fixunits <build|embed|eval|all> --book samsung|cosmax|kb
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from . import lexical, llm, retrieve
from . import passages as P
from .evaluate2 import boot, per_query
from .facets import CAP_B, LIKE, TOP, _alt
from .sg1d import BOOKS, _norm
from .taskeval import _num
from .units import Unit, load

FOOT = re.compile(r"^\s*(?:\d{1,2}\)|\*|※)")
BULLET = re.compile(r"\s*(?:•|■|‒|(?<=\s)-\s)")
SPLIT_MIN, BULLET_MIN, ITEM_MIN = 400, 3, 80
REPEAT = 0.3
SEC_MIN, SEC_TOP, CAP_S = 3, 20, 120
RANK = {"core": 2, "aux": 1}


def _d(out):
    d = Path(out) / "sg1g"
    d.mkdir(exist_ok=True)
    return d


def _boxes(doc):
    """단위 id → (쪽, 상자). 표 단위의 id 는 표 id."""
    out = {t["id"]: (t["page"], t["bbox"]) for t in doc["tables"]}
    for b in doc["blocks"]:
        if b["kind"] != "table":
            out[b["id"]] = (b["page"], b["bbox"])
    return out


def _inter(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _area(a):
    return max(0.0, (a[2] - a[0]) * (a[3] - a[1]))


def _cover(a, b):
    """a 넓이 중 b 와 겹친 몫."""
    return _inter(a, b) / _area(a) if _area(a) > 0 else 0.0


def _iou(a, b):
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u > 0 else 0.0


# ---- 단위 고치기 ----
def fix_units(doc, units, labels):
    region = {}
    for b in doc["blocks"]:
        region[b["table_id"] if b["kind"] == "table" else b["id"]] = b.get("region", "body")
    stat = Counter()
    # b. 머리·꼬리
    keep = [u for u in units if region.get(u.id, "body") == "body"]
    stat["b 머리·꼬리 뺌"] = len(units) - len(keep)
    # e. 되풀이 글
    npages = len(doc["pages"])
    pages_of = defaultdict(set)
    for u in keep:
        if u.kind != "table":
            pages_of[re.sub(r"\s+", " ", u.text.strip())].add(u.page)
    rep = {t for t, ps in pages_of.items() if len(t) >= 8 and len(ps) >= REPEAT * npages}
    n0 = len(keep)
    keep = [u for u in keep if u.kind == "table" or re.sub(r"\s+", " ", u.text.strip()) not in rep]
    stat["e 되풀이 글 뺌"] = n0 - len(keep)
    # a. 도표
    absorbed = defaultdict(list)  # 단위 id → 흡수한 단위 id
    by_chart = defaultdict(list)
    for w in labels["words"]:
        if w["role"] == "value" and (w.get("label") or {}).get("category"):
            by_chart[w["chart"]].append(w)
    charts = {c["id"]: c for c in labels["charts"]}
    U = {u.id: u for u in keep}
    gone, chart_at = set(), {}
    for cid, ws in by_chart.items():
        c = charts[cid]
        conts = []
        for w in ws:
            k = w["container"].split(":")[0]
            if k in U and k not in conts:
                conts.append(k)
        if not conts:
            continue
        lines = [f"{w['label']['category']}" + (f" · {w['label']['series']}" if w["label"].get("series") else "")
                 + f" | {w['text']}" for w in ws]
        title = c.get("title") or (w["label"].get("title") or "")
        eat = [k for k in conts if len(U[k].text) <= 200 or U[k].kind == "table"]
        # 라벨 붙은 값 줄 뒤에 흡수한 조각의 원문을 그대로 둔다(라벨 못 붙은 값·낱말을 잃지 않게)
        text = f"도표: {title}\n" + "\n".join(lines) + "\n원문: " + " / ".join(U[k].text.replace("\n", " / ") for k in eat)
        first = U[conts[0]]
        cu = Unit(f"{cid}", "table", first.page, first.section_id, first.order, text,
                  [[w["label"]["category"], w["label"].get("series") or "", w["text"]] for w in ws])
        chart_at[conts[0]] = cu
        for k in eat:
            gone.add(k)
            absorbed[cid].append(k)
        stat["a 도표 단위"] += 1
    out = []
    for u in keep:
        if u.id in chart_at:
            out.append(chart_at[u.id])
        if u.id not in gone:
            out.append(u)
    stat["a 흡수한 조각"] = len(gone)
    # c. 각주 → 같은 쪽 앞 표
    res, last_table = [], {}
    for u in out:
        if u.kind == "table":
            last_table[u.page] = len(res)
            res.append(u)
            continue
        if u.kind == "paragraph" and FOOT.match(u.text) and len(u.text) < 600 and u.page in last_table:
            t = res[last_table[u.page]]
            res[last_table[u.page]] = Unit(t.id, t.kind, t.page, t.section_id, t.order, t.text + "\n주: " + u.text.strip(), t.rows)
            absorbed[t.id].append(u.id)
            stat["c 표에 붙인 각주"] += 1
            continue
        res.append(u)
    return res, absorbed, stat


TINY = 60


def merge_tiny(ps):
    """layout 2 는 그림 속 낱말(대학 이름·조직도 칸)을 제목으로 세워 단락이 한두 낱말씩 끊긴다 —
    같은 섹션·같은 쪽에 잇단 짧은 단락(머리 줄 뺀 글 60자 미만)을 하나로 묶는다(1200자까지)."""
    out, n = [], 0
    for p in ps:
        body = p.text.partition("\n")[2] if "\n" in p.text else p.text
        prev = out[-1] if out else None
        if (prev is not None and p.kind == prev.kind == "paragraph" and len(body) < TINY and prev.section_id == p.section_id
                and prev.pages[-1] == p.pages[0] and getattr(prev, "_tiny", False) and len(prev.text) + len(body) < P.MAX):
            prev.text += " · " + body
            prev.members = prev.members + p.members
            prev.pages = sorted(set(prev.pages + p.pages))
            n += 1
            continue
        p._tiny = p.kind == "paragraph" and len(body) < TINY
        out.append(p)
    # 표 바로 앞의 짧은 단락(「〉Scope 3 온실가스 배출량」 꼴 표 이름)은 그 표의 첫 줄로 넣는다
    res = []
    for i, p in enumerate(out):
        nxt = out[i + 1] if i + 1 < len(out) else None
        if (getattr(p, "_tiny", False) and nxt is not None and nxt.kind == "table" and nxt.section_id == p.section_id
                and nxt.pages[0] in p.pages):
            body = p.text.partition("\n")[2] or p.text
            head, _, rest = nxt.text.partition("\n")
            nxt.text = f"{head} · {body}\n{rest}"
            nxt.members = p.members + nxt.members
            n += 1
            continue
        res.append(p)
    return res, n


def split_summary(ps):
    out, n = [], 0
    for p in ps:
        head, _, body = p.text.partition("\n") if "\n" in p.text else ("", "", p.text)
        parts = [x.strip() for x in BULLET.split(body) if x.strip()]
        if p.kind == "paragraph" and len(p.text) > SPLIT_MIN and len(parts) >= BULLET_MIN + 1:
            # 짧은 항목(대학 이름 목록 같은 것)은 이어 붙여 ITEM_MIN 자 넘을 때 끊는다
            chunks, cur = [], ""
            for x in parts:
                cur = f"{cur} • {x}" if cur else x
                if len(cur) >= ITEM_MIN:
                    chunks.append(cur)
                    cur = ""
            if cur:
                if chunks and len(cur) < ITEM_MIN:
                    chunks[-1] += " • " + cur
                else:
                    chunks.append(cur)
            if len(chunks) < 2:
                out.append(p)
                continue
            n += 1
            for x in chunks:
                out.append(P.Passage("", "paragraph", p.pages, p.section_id, p.members, (head + "\n" if head else "") + x))
        else:
            out.append(p)
    for i, p in enumerate(out):
        p.id = f"P{i + 1}"
    return out, n


class New:
    def __init__(self, name):
        _, out = BOOKS[name]
        self.name, self.out, self.d = name, out, _d(out)
        self.doc, units = load(self.d / "app-l2" / "document.json")
        labels = json.load(open(self.d / "charts-l2" / "chart-labels.json"))
        self.units, self.absorbed, self.stat = fix_units(self.doc, units, labels)
        ps = P.build(self.doc, self.units)
        for p in ps:  # 흡수한 단위를 구성에 넣는다(정답 옮김용)
            p.members = p.members + [a for m in p.members for a in self.absorbed.get(m, [])]
        ps, self.stat["f 묶은 짧은 단락"] = merge_tiny(ps)
        self.ps, self.stat["d 쪼갠 요약 단락"] = split_summary(ps)
        self.ids = [p.id for p in self.ps]
        self.idx = {p: i for i, p in enumerate(self.ids)}


def remap(nb, ob):
    """옛 단락 정답·이름표 → 새 단락. 옛 단위와 새 단위를 쪽·상자 겹침(작은 쪽 넓이의 50% 이상)으로 잇는다."""
    obox, nbox = _boxes(ob.doc), _boxes(nb.doc)
    for cid in nb.absorbed:  # 도표 단위는 흡수한 단위의 상자로
        if cid not in nbox and nb.absorbed[cid]:
            nbox[cid] = nbox.get(nb.absorbed[cid][0])
    by_page = defaultdict(list)
    for k, v in obox.items():
        by_page[v[0]].append((k, v[1]))
    old2new = defaultdict(list)
    for k, v in nbox.items():  # 새 단위마다 겹침(새 쪽 넓이의 50% 이상)이 가장 큰 옛 단위 하나
        if not v:
            continue
        best = max(((_iou(v[1], b), o) for o, b in by_page.get(v[0], []) if _cover(v[1], b) >= 0.5), default=None)
        if best:
            old2new[best[1]].append(k)
    unit2p = defaultdict(set)
    for p in nb.ps:
        for m in p.members:
            unit2p[m].add(p.id)
    oldp2new = {}
    for p in ob.ps:
        s = set()
        for m in p.members:
            for n in old2new.get(m, []):
                s |= unit2p.get(n, set())
        oldp2new[p.id] = sorted(s, key=lambda x: int(x[1:]))
    return oldp2new


def cmd_build(name):
    from .sg1d import Book
    nb, ob = New(name), Book(name, "gold-final.json")
    m = remap(nb, ob)
    gold, infl, lost = {}, 0, 0
    for q, items in ob.gold.items():
        best = {}
        for x in items:
            new = m.get(x["id"], [])
            if not new:
                lost += x["label"] == "core"
            if x["label"] == "core" and len(new) > 1:
                infl += len(new) - 1
            for n in new:
                if RANK[x["label"]] > RANK.get(best.get(n), 0):
                    best[n] = x["label"]
        gold[q] = [{"id": k, "label": v} for k, v in best.items()]
    qs = []
    for q in ob.queries:
        q = dict(q)
        if q.get("self_passage"):
            q["self_passages"] = m.get(q["self_passage"], [])
        qs.append(q)
    tags_o = ob.tags()
    tags = {}
    for op, nps in m.items():
        t = tags_o.get(op)
        if not t:
            continue
        for n in nps:
            e = tags.setdefault(n, {"metrics": set(), "division": t["division"], "years": set(), "kinds": set()})
            e["metrics"] |= set(t["metrics"])
            e["years"] |= set(t["years"])
            e["kinds"] |= set(t["kinds"])
    tags = {k: {kk: (sorted(vv) if isinstance(vv, set) else vv) for kk, vv in v.items()} for k, v in tags.items()}
    P.write(nb.doc, nb.ps, nb.d)
    json.dump(gold, open(nb.d / "gold-final.json", "w"), ensure_ascii=False, indent=1)
    json.dump(qs, open(nb.d / "queries.json", "w"), ensure_ascii=False, indent=1)
    json.dump(tags, open(nb.d / "tags.json", "w"), ensure_ascii=False)
    json.dump(m, open(nb.d / "oldp2new.json", "w"), ensure_ascii=False)
    oc = sum(1 for v in ob.gold.values() for x in v if x["label"] == "core")
    nc = sum(1 for v in gold.values() for x in v if x["label"] == "core")
    L = [len(p.text) for p in nb.ps]
    OL = [len(p.text) for p in ob.ps]
    info = {"stat": dict(nb.stat), "passages": [len(ob.ps), len(nb.ps)],
            "median": [float(np.median(OL)), float(np.median(L))],
            "under60": [sum(x < 60 for x in OL) / len(OL), sum(x < 60 for x in L) / len(L)],
            "core": [oc, nc], "core_inflation": infl, "core_lost": lost,
            "chars": [sum(OL), sum(L)]}
    json.dump(info, open(nb.d / "build.json", "w"), ensure_ascii=False, indent=1)
    print(name, json.dumps(info, ensure_ascii=False))


# ---- 임베딩(옛 단락과 글이 같으면 다시 쓴다) ----
def _tagtext(ps, tags):
    out = []
    for p in ps:
        t = tags.get(p.id)
        out.append((f"지표: {', '.join(t['metrics'])} / 부문: {t['division'] or '-'} / 연도: {', '.join(t['years'])}"
                    f" / 종류: {', '.join(t['kinds'])}\n" + p.text) if t else p.text)
    return out


def cmd_embed(name):
    from .sg1d import Book
    nb, ob = New(name), Book(name, "gold-final.json")
    tags = json.load(open(nb.d / "tags.json"))
    for tag, new, old in (("pass", [p.text for p in nb.ps], [p.text for p in ob.ps]),
                          ("tagtext", _tagtext(nb.ps, tags), ob.tagtext())):
        f = nb.d / f"emb-{tag}.npy"
        if f.exists() and len(np.load(f)) == len(new):
            continue
        E = np.load(ob.d / f"emb-{tag}.npy")
        have = {t: E[i] for i, t in enumerate(old)}
        need = [t for t in dict.fromkeys(new) if t not in have]
        print(name, tag, "다시 씀", len(new) - len([t for t in new if t not in have]), "새로", len(need),
              f"{sum(len(t) for t in need):,}자")
        if need:
            got = llm.embed(need, nb.out, f"sg1g:{tag}")
            have.update({t: np.array(v, dtype="float32") for t, v in zip(need, got)})
        np.save(f, np.array([have[t] for t in new], dtype="float32"))


# ---- 채점 ----
def _graph(ps):
    edges = []
    for i, p in enumerate(ps):
        if i + 1 < len(ps) and ps[i + 1].section_id == p.section_id:
            edges.append((p.id, ps[i + 1].id, "next_block"))
        edges.append((p.id, f"sec:{p.section_id}", "section_contains"))
        for pg in p.pages:
            edges.append((p.id, f"page:{pg}", "on_page"))
    return retrieve.Graph(edges, dict(retrieve.STRUCT_W))


def _orders(ps, ids, U, UT, bm, bmt, g, qv, qtext, drop):
    va = [i for i in retrieve.ranks(list(U @ qv)) if i not in drop]
    vb = [i for i in retrieve.ranks(bm.scores(qtext)) if i not in drop]
    cs = retrieve.rrf(va, vb)
    ta = [i for i in retrieve.ranks(list(UT @ qv)) if i not in drop]
    tb = [i for i in retrieve.ranks(bmt.scores(qtext)) if i not in drop]
    ct = retrieve.rrf(ta, tb)
    return {"P-a 벡터": va, "P-b BM25": vb, "P-c 벡터+BM25": sorted(cs, key=lambda i: -cs[i]),
            "P-s P-c+구조": [i for i in retrieve.expand(cs, ids, g) if i not in drop],
            "P-ts P-t+구조": [i for i in retrieve.expand(ct, ids, g) if i not in drop]}


def _section_expand(cand, sec_of, members_of):
    c = Counter(sec_of[p] for p in cand[:SEC_TOP])
    out = list(cand)
    for s, n in c.most_common():
        if n < SEC_MIN:
            break
        for p in members_of[s]:
            if p not in out and len(out) < CAP_S:
                out.append(p)
    return out


def per_query_grouped(order, groups, L, total):
    """정답 = 옛 핵심 단락 하나 = 새 단락 묶음 하나. 묶음 안 하나라도 들면 그 정답을 찾은 것(단위가 쪼개져 정답 수가 부푸는 것을 막음)."""
    pos = {}
    for r, p in enumerate(order):
        pos.setdefault(p, r)
    first = [min((pos.get(p, 10 ** 9) for p in g), default=10 ** 9) for g in groups]
    m = {f"재현@{k}": sum(f < k for f in first) / len(groups) for k in (10, 20)}
    used, cum = 0, []
    for p in order:
        used += L[p]
        cum.append(used)
    reach = sorted(cum[f] if f < len(cum) else total + 1 for f in first)
    for x, name in ((4000, "4천자"), (8000, "8천자"), (16000, "1.6만자"), (32000, "3.2만자")):
        m[name] = sum(c <= x for c in reach) / len(groups)
    need = -(-9 * len(groups) // 10)
    m["90%까지 글자"] = reach[need - 1] if reach[need - 1] <= total else total
    return m


def _category(p, core_same_sec):
    body = p.text.partition("\n")[2] or p.text
    digits = sum(ch.isdigit() for ch in body)
    if p.kind == "table" and (len(body) < 120 or digits > 0.3 * max(1, len(re.sub(r"\s", "", body)))):
        return "① 도표 조각"
    if FOOT.match(body) or "FOUNDER" in body or "GRI Topic" in body:
        return "② 각주·쪽 글"
    if len(BULLET.findall(body)) >= BULLET_MIN or re.search(r"Highlights|핵심 성과|주요성과", p.text[:80]):
        return "③ 요약 쪽"
    if core_same_sec >= 3:
        return "④ 한 섹션에 많음"
    return "⑤ 업종·개념"


def cmd_eval(name):
    from .sg1d import Book
    nb, ob = New(name), Book(name, "gold-final.json")
    oldp2new = json.load(open(nb.d / "oldp2new.json"))
    gold = json.load(open(nb.d / "gold-final.json"))
    qs = json.load(open(nb.d / "queries.json"))
    tags = json.load(open(nb.d / "tags.json"))
    U, UT = _norm(np.load(nb.d / "emb-pass.npy")), _norm(np.load(nb.d / "emb-tagtext.npy"))
    Q = _norm(np.load(ob.d / "emb-q.npy"))
    bm, bmt = lexical.BM25([p.text for p in nb.ps]), lexical.BM25(_tagtext(nb.ps, tags))
    g = _graph(nb.ps)
    so = json.load(open(ob.d / "orders.json"))
    fo = json.load(open(Path(ob.out) / "sg1f" / "orders.json"))
    from .facets import _facet_strings, _plans
    plans = _plans(ob)
    ftexts, owner = _facet_strings(ob, plans)
    FV = _norm(np.load(Path(ob.out) / "sg1f" / "emb-facets.npy"))
    OL = {p.id: len(p.text) for p in ob.ps}
    NL = {p.id: len(p.text) for p in nb.ps}
    OP = {p.id: p for p in ob.ps}
    sec_of = {p.id: p.section_id for p in nb.ps}
    members_of = defaultdict(list)
    for p in nb.ps:
        members_of[p.section_id].append(p.id)
    facts = json.load(open(ob.d / "task" / "facts.json"))
    NP = {p.id: p for p in nb.ps}

    def within(order, L, x):
        used, got = 0, []
        for p in order:
            used += L[p]
            if used > x:
                break
            got.append(p)
        return got

    def fact_recall(order, Pmap, L, x, qid):
        fs = facts.get(qid, {}).get("facts") or []
        if not fs:
            return None
        t = _num(" ".join(Pmap[p].text for p in within(order, L, x)))
        return float(np.mean([any(_num(s) in t for s in f["match"]) for f in fs]))

    osec_of = {p.id: p.section_id for p in ob.ps}
    omembers_of = defaultdict(list)
    for p in ob.ps:
        omembers_of[p.section_id].append(p.id)
    missed, rows, recov, pools = [], {}, Counter(), {"old": [], "old+sec": [], "base": [], "sec": []}
    for qi, q in enumerate(qs):
        ocore = {x["id"] for x in ob.gold.get(q["id"], []) if x["label"] == "core"} - {q.get("self_passage")}
        drop = {nb.idx[p] for p in q.get("self_passages", [])}
        o = {k: [nb.ids[i] for i in v] for k, v in _orders(nb.ps, nb.ids, U, UT, bm, bmt, g, Q[qi], q["text"], drop).items()}
        # 갈래 후보(새 단위)
        per = []
        for k_, (t, (qid, k)) in enumerate(zip(ftexts, owner)):
            if qid != q["id"]:
                continue
            va = [i for i in retrieve.ranks(list(U @ FV[k_])) if i not in drop]
            vb = [i for i in retrieve.ranks(bm.scores(t)) if i not in drop]
            cs = retrieve.rrf(va, vb)
            per.append((LIKE.get(plans[qid]["facets"][k]["likelihood"], 2), k,
                        [nb.ids[i] for i in sorted(cs, key=lambda i: -cs[i])[:TOP]]))
        per.sort()
        rr, seen = [], set()
        for r in range(TOP):
            for _, _, top in per:
                if r < len(top) and top[r] not in seen:
                    seen.add(top[r])
                    rr.append(top[r])
        cand = _alt(rr, o["P-ts P-t+구조"][:50], CAP_B)
        cand_s = _section_expand(cand, sec_of, members_of)
        old_cand_s = _section_expand(fo[q["id"]]["_cand_b"], osec_of, omembers_of)
        pools["base"].append(len(cand))
        pools["sec"].append(len(cand_s))
        pools["old"].append(len(fo[q["id"]]["_cand_b"]))
        pools["old+sec"].append(len(old_cand_s))
        # 옛 단위의 못 찾던 근거
        ra = {p: r for r, p in enumerate(so[q["id"]]["P-a 벡터"])}
        rb = {p: r for r, p in enumerate(so[q["id"]]["P-b BM25"])}
        fb16 = set(within(fo[q["id"]]["F-b 갈래∪P-ts → 재순위"], OL, 16000))
        for c in sorted(ocore):
            if ra.get(c, 9e9) >= 50 and rb.get(c, 9e9) >= 50 and c not in fb16:
                same = sum(1 for x in ocore if OP[x].section_id == OP[c].section_id)
                cat = _category(OP[c], same)
                ncore = sorted(set(oldp2new.get(c, [])) - set(q.get("self_passages", [])), key=lambda s: int(s[1:]))
                in_b = bool(set(ncore) & set(cand))
                in_s = bool(set(ncore) & set(cand_s))
                old_in = c in set(fo[q["id"]]["_cand_b"])
                in_2 = c in set(old_cand_s)
                missed.append({"query": q["id"], "old": c, "members": OP[c].members, "category": cat,
                               "new": ncore, "old_in_pool": old_in, "step2": in_2, "step1": in_b, "step1+2": in_s})
                recov[(cat, "1")] += in_b
                recov[(cat, "2")] += in_2
                recov[(cat, "1+2")] += in_s
                recov[(cat, "n")] += 1
        # 지표(새 단위)
        ncore = {x["id"] for x in gold.get(q["id"], []) if x["label"] == "core"} - set(q.get("self_passages", []))
        nrel = {x["id"] for x in gold.get(q["id"], [])} - set(q.get("self_passages", []))
        orel = {x["id"] for x in ob.gold.get(q["id"], [])} - {q.get("self_passage")}
        if not ncore or not ocore:
            continue
        groups = [set(oldp2new.get(c, [])) - set(q.get("self_passages", [])) for c in sorted(ocore)]
        for a in ("P-c 벡터+BM25", "P-s P-c+구조", "P-ts P-t+구조"):
            rows.setdefault(("new", a), {})[q["id"]] = per_query_grouped(o[a], groups, NL, sum(NL.values()))
            rows.setdefault(("old", a), {})[q["id"]] = per_query(so[q["id"]][a], ocore, orel, OL, sum(OL.values()))
            if q["type"] == "drafting":
                rows[("new", a)][q["id"]]["사실1.6만"] = fact_recall(o[a], NP, NL, 16000, q["id"])
                rows[("old", a)][q["id"]]["사실1.6만"] = fact_recall(so[q["id"]][a], OP, OL, 16000, q["id"])
    json.dump(missed, open(nb.d / "missed-before.json", "w"), ensure_ascii=False, indent=1)
    rng = np.random.default_rng(0)
    summ = {}
    for a in ("P-c 벡터+BM25", "P-s P-c+구조", "P-ts P-t+구조"):
        new, old = rows[("new", a)], rows[("old", a)]
        e = {}
        for m in ("재현@10", "8천자", "1.6만자", "90%까지 글자", "사실1.6만"):
            qq = [k for k in new if new[k].get(m) is not None and old[k].get(m) is not None]
            if not qq:
                continue
            d = [new[k][m] - old[k][m] for k in qq]
            mean, lo, hi = boot(d, rng)
            e[m] = {"old": float(np.mean([old[k][m] for k in qq])), "new": float(np.mean([new[k][m] for k in qq])),
                    "diff": mean, "lo": lo, "hi": hi, "n": len(qq),
                    "old_med": float(np.median([old[k][m] for k in qq])), "new_med": float(np.median([new[k][m] for k in qq]))}
        summ[a] = e
    cats = sorted({c for c, _ in recov})
    rec = {c: {"n": recov[(c, "n")], "step1": recov[(c, "1")], "step2": recov[(c, "2")], "step1+2": recov[(c, "1+2")]}
           for c in cats}
    res = {"book": name, "missed_n": len(missed), "recovery": rec,
           "pool": {k: {"median": float(np.median(v)), "max": max(v)} for k, v in pools.items()},
           "arms": summ, "nq": len(rows[("new", "P-s P-c+구조")])}
    json.dump(res, open(nb.d / "eval.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    a = ap.parse_args(argv)
    for s in {"all": ["build", "embed", "eval"]}.get(a.cmd, [a.cmd]):
        globals()[f"cmd_{s}"](a.book)


if __name__ == "__main__":
    main()
