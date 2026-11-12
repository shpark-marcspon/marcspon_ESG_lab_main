"""SG1d — 단락 단위로 갈래를 다시 세우고, 질의 때 AI 를 쓰는 기준선을 더한다.

질의 때 AI 0: P-a 벡터 · P-b BM25 · P-c RRF · P-s P-c+구조(다음 단락·같은 섹션·같은 쪽) · P-t 이름표-글 색인(RRF) · P-ts P-t+구조 · 쪽 퍼넣기
질의 때 AI 씀: Q-x 질의 확장(luna 가 보고서 말투로 바꿔 씀 → RRF) · Q-r 재순위(luna 가 P-ts 상위 50 을 다시 줄 세움)
              · Q-f 지표 마디 먼저(묶기 고침: 이름 임베딩으로 나눈 묶음 안에서만 luna, 두 판) + P-ts
산출 <out>/sg1d/ (임베딩·질의 확장·재순위·canon2·qmap2·orders.json) · <out>/pool-to-judge.json|md
py/ 에서: python -m semgraph.sg1d <prep|tag|canon|qmap|qx|rerank|orders|eval|pool|all> --book samsung|cosmax|kb
"""

import argparse
import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import lexical, llm, retrieve
from . import passages as P
from .facts import NUMERIC, TAG_SCHEMA, _div_ok
from .units import load

BOOKS = {"samsung": ("../out/docparse-samsung/document.json", "../out/semgraph"),
         "cosmax": ("../out/parse-cosmax_2025_kor/document.json", "../out/semgraph-cosmax"),
         "kb": ("../out/parse-kb_2025_kor/document.json", "../out/semgraph-kb"),
         "hdec": ("../out/semgraph-hdec/sg1g/app-l2/document.json", "../out/semgraph-hdec")}
MODEL = "gpt-6-luna"
TOPN = 20
ZERO = ["P-a 벡터", "P-b BM25", "P-c 벡터+BM25", "P-s P-c+구조", "P-t 이름표-글 색인", "P-ts P-t+구조", "쪽 퍼넣기"]
WITH_LLM = ["Q-x 질의 확장", "Q-r 재순위(P-ts 상위 50)", "Q-f 지표 마디 먼저(+P-ts)"]


class Book:
    def __init__(self, name, gold="gold-passage.json"):
        docp, out = BOOKS[name]
        self.name, self.out = name, out
        self.d = Path(out) / "sg1d"
        self.d.mkdir(parents=True, exist_ok=True)
        self.doc, self.units = load(docp)
        self.ps = P.build(self.doc, self.units)
        saved = json.load(open(Path(out) / "passages.json"))
        assert [p.id for p in self.ps] == [x["id"] for x in saved], "passages.json 과 다시 만든 단락이 다르다"
        self.ids = [p.id for p in self.ps]
        self.idx = {p: i for i, p in enumerate(self.ids)}
        qp = Path(out) / "queries-passage.json"
        self.queries = json.load(open(qp)) if qp.exists() else []
        gp = Path(out) / gold
        self.gold = json.load(open(gp)) if gp.exists() else {}

    def tags(self):
        p = Path(self.out) / "facts" / "tags-passage.json"
        return json.load(open(p)) if p.exists() else {}

    def tagtext(self):
        tg = self.tags()
        out = []
        for p in self.ps:
            t = tg.get(p.id)
            if t:
                head = (f"지표: {', '.join(t['metrics'])} / 부문: {t['division'] or '-'} / 연도: {', '.join(t['years'])}"
                        f" / 종류: {', '.join(t['kinds'])}")
                out.append(head + "\n" + p.text)
            else:
                out.append(p.text)
        return out

    def drop(self, q):
        return {self.idx[q["self_passage"]]} if q.get("self_passage") in self.idx else set()


def _norm(m):
    m = np.asarray(m, dtype="float32")
    return m / np.linalg.norm(m, axis=1, keepdims=True)


# ---- 임베딩 ----
def cmd_prep(b):
    for tag, texts in (("pass", [p.text for p in b.ps]), ("tagtext", b.tagtext())):
        f = b.d / f"emb-{tag}.npy"
        if tag == "tagtext" and not b.tags():
            continue
        if not f.exists():
            np.save(f, np.array(llm.embed(texts, b.out, f"sg1d:{tag}"), dtype="float32"))
    f = b.d / "emb-q.npy"
    if b.queries and not f.exists():
        np.save(f, np.array(llm.embed([q["text"] for q in b.queries], b.out, "sg1d:q"), dtype="float32"))


# ---- KB 이름표(단락에 바로) ----
TAG_INSTR = """너는 ESG 보고서 단락에 「무엇에 대한 데이터인가」 이름표를 다는 사람이다. 각 줄은 [단락id] 글 이다.
글의 첫 조각(' // ' 앞)은 그 단락의 섹션 제목·가까운 제목(표는 바로 앞 문단 앞부분까지)이다. 표는 행을 ' / ', 칸을 ' | ' 로 이었다.

단락마다(목차·빈 글은 빼도 됨):
- metrics: 그 단락이 말하는 지표·주제 이름, 보고서 말 그대로 짧게(예: 「Scope 1 배출량」「용수 재이용량」「재생에너지 전환율」「LTIR」「협력회사 제3자 검증」). 1~5개.
- division: 사업 부문·계열사·지역 이름(보고서에 나오는 대로) · 전사 · null
- years: 나오는 연도들(「2025」「2030」 꼴)
- kind: value(측정·실적 수치) · target(목표) · activity(한 일) · assurance(검증·인증) · policy(방침·체계) · other
원문에 없는 것은 넣지 않는다."""


def cmd_tag(b):
    path = b.d / "tags-native.jsonl"
    done = {json.loads(l)["chunk"] for l in open(path)} if path.exists() else set()
    by = defaultdict(list)
    for p in b.ps:
        if p.kind == "table":
            head, body = p.text.split("\n", 1)
            line = f"[{p.id}] {head[:200]} // " + body.replace("\n", " / ")[:1800]
        else:
            head, _, body = p.text.partition("\n")
            line = f"[{p.id}] {head} // {body.replace(chr(10), ' / ')[:1500]}" if body else f"[{p.id}] {head[:1500]}"
        by[p.section_id].append((p.id, line))
    chunks = []
    for sid, rows in by.items():
        for i in range(0, len(rows), 40):
            chunks.append((f"{sid}:{i}", rows[i:i + 40]))

    def one(c):
        key, rows = c
        if key in done:
            return
        ids = {i for i, _ in rows}
        raw, row = llm.respond(MODEL, TAG_INSTR, "\n".join(l for _, l in rows), TAG_SCHEMA, b.out, f"sg1d:tag:{key}")
        try:
            tags = [t for t in json.loads(raw)["tags"] if t["block"] in ids]
        except Exception:
            tags = []
        with open(path, "a") as f:
            f.write(json.dumps({"chunk": key, "tags": tags}, ensure_ascii=False) + "\n")
        print(f"{key} 이름표 {len(tags)}/{len(rows)} ${row['costUsd']:.4f} {row['seconds']}s", flush=True)

    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, chunks))
    out = {}
    for l in open(path):
        for t in json.loads(l)["tags"]:
            out[t["block"]] = {"metrics": sorted(set(t["metrics"])), "division": t["division"], "years": sorted(set(t["years"])),
                               "kinds": [t["kind"]]}
    (Path(b.out) / "facts").mkdir(exist_ok=True)
    json.dump(out, open(Path(b.out) / "facts" / "tags-passage.json", "w"), ensure_ascii=False)
    print("이름표 단락", len(out), "/", len(b.ps))


# ---- 묶기 고침 ----
CANON_INSTR = """아래는 한 ESG 보고서에서 나온, 서로 비슷한 지표·주제 이름 목록이다(번호: 이름). 같은 지표·주제를 가리키는 이름끼리 묶어라.
- 같은 것 = 같은 대상을 재는 같은 뜻(「용수 취수량」「총 수자원 취수량」「취수량」은 하나). 부문·연도·지역·단위 차이는 무시하고 묶는다.
- 다른 것 = 재는 대상이 다름(「취수량」과 「재이용량」은 다름. 「Scope 1」과 「Scope 2」는 다름).
- 너무 넓게 묶지 않는다(「환경」 하나로 몰지 않음).
- 이름이 둘 이상인 묶음만 낸다. 혼자인 이름은 내지 않는다. canonical 은 그 묶음의 대표 이름(짧게)."""

CANON_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {"groups": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["canonical", "members"],
                               "properties": {"canonical": {"type": "string"},
                                              "members": {"type": "array", "items": {"type": "integer"}}}}}}}
CHUNK = 120


def _split(idx, X, rng):
    """임베딩 2-평균으로 CHUNK 이하가 될 때까지 나눈다 — 비슷한 이름이 같은 호출에 들도록."""
    if len(idx) <= CHUNK:
        return [idx]
    sub = X[idx]
    c = sub[rng.choice(len(idx), 2, replace=False)]
    for _ in range(10):
        lab = np.argmax(sub @ c.T, axis=1)
        if lab.min() == lab.max():
            break
        c = _norm(np.stack([sub[lab == k].mean(0) for k in (0, 1)]))
    if lab.min() == lab.max():
        h = len(idx) // 2
        return _split(idx[:h], X, rng) + _split(idx[h:], X, rng)
    return _split(idx[lab == 0], X, rng) + _split(idx[lab == 1], X, rng)


def _names(b):
    """이름 → 단락 id 집합. 이름표 지표 + 표 행 머리(코드)."""
    names = defaultdict(set)
    for pid, t in b.tags().items():
        for m in t["metrics"]:
            if m.strip():
                names[m.strip()].add(pid)
    for p in b.ps:
        if p.kind != "table":
            continue
        for r in p.rows[1:]:
            for c in r:
                c = re.sub(r"\d\)|\*", "", c or "").strip()
                if c and not NUMERIC.match(c) and 2 <= len(c) <= 40:
                    names[c].add(p.id)
                    break
    return names


def _group_pass(names_list, X, tag, b, seed):
    rng = np.random.default_rng(seed)
    chunks = _split(np.arange(len(names_list)), X, rng)
    parent = list(range(len(names_list)))
    canon_of = {}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def one(ch):
        ch = list(ch)
        raw, row = llm.respond(MODEL, CANON_INSTR, "\n".join(f"{j}: {names_list[i]}" for j, i in enumerate(ch)),
                               CANON_SCHEMA, b.out, f"sg1d:{tag}:{ch[0]}", max_output=12000)
        try:
            gs = json.loads(raw)["groups"]
        except Exception:
            gs = []
        return [(g["canonical"], [ch[m] for m in g["members"] if 0 <= m < len(ch)]) for g in gs], row

    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, chunks))
    cost = 0.0
    for gs, row in res:
        cost += row["costUsd"]
        for c, mem in gs:
            if len(mem) < 2:
                continue
            r0 = find(mem[0])
            for m in mem[1:]:
                parent[find(m)] = r0
            canon_of[find(r0)] = c
    groups = defaultdict(list)
    for i in range(len(names_list)):
        groups[find(i)].append(i)
    print(f"{tag}: 호출 {len(chunks)} · 이름 {len(names_list)} → 묶음 {len(groups)} ${cost:.4f} {time.time() - t0:.0f}s", flush=True)
    return groups, {r: canon_of.get(r, names_list[r]) for r in groups}


def cmd_canon(b):
    names = _names(b)
    lst = sorted(names)
    f = b.d / "emb-names.npy"
    if not f.exists():
        np.save(f, np.array(llm.embed(lst, b.out, "sg1d:names"), dtype="float32"))
    X = _norm(np.load(f))
    # 1판: 이름끼리
    g1, c1 = _group_pass(lst, X, "canon1", b, 0)
    reps = sorted(g1)
    rep_names = [c1[r] for r in reps]
    # 2판: 1판 대표끼리(다른 나눔) — 조각 경계에 걸린 것을 잇는다
    Xr = _norm(np.stack([X[g1[r]].mean(0) for r in reps]))
    g2, c2 = _group_pass(rep_names, Xr, "canon2", b, 1)
    canon = []
    for r2, mem in g2.items():
        nm = [lst[i] for k in mem for i in g1[reps[k]]]
        canon.append({"canonical": c2[r2] if len(mem) > 1 else rep_names[mem[0]], "names": sorted(nm),
                      "passages": sorted({p for n in nm for p in names[n]}, key=lambda s: int(s[1:]))})
    canon.sort(key=lambda c: c["canonical"])
    for k, c in enumerate(canon):
        c["id"] = k
    json.dump(canon, open(b.d / "canon2.json", "w"), ensure_ascii=False, indent=0)
    old = Path(b.out) / "facts" / "canon.json"
    if old.exists():
        o = json.load(open(old))
        print(f"홑 묶음 비율: 전 {sum(len(c['names']) == 1 for c in o) / len(o):.1%} ({len(o)} 묶음)", end=" · ")
    print(f"뒤 {sum(len(c['names']) == 1 for c in canon) / len(canon):.1%} ({len(canon)} 묶음, 이름 {len(lst)})")


# ---- 질의 때 AI ----
Q_INSTR = """아래 [지표 목록](번호: 이름)에서, 질의에 답하려면 찾아야 할 지표·주제를 골라라(1~8개, 직접 관련된 것만).
division 은 질의가 특정 부문·계열사·지역만 말하면 그것, 아니면 null."""
Q_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ids", "division"], "properties": {
    "ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 8}, "division": {"type": ["string", "null"]}}}

QX_INSTR = """너는 ESG 보고서 검색을 돕는다. 질의를 그 보고서 본문·표에 실제로 나올 법한 말투로 3~5개 바꿔 써라
(문장 꼴 하나 이상, 표 머리·행 이름 꼴 하나 이상). metrics 에는 찾을 지표 이름을 보고서에서 흔히 쓰는 말로 적어라."""
QX_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["phrasings", "metrics"], "properties": {
    "phrasings": {"type": "array", "items": {"type": "string"}}, "metrics": {"type": "array", "items": {"type": "string"}}}}

RR_INSTR = """질의에 답하는 근거로 쓸모 있는 차례로 단락 id 를 늘어놓아라. 각 줄은 [단락id] 글(앞 300자) 이다.
가장 직접적인 근거(수치·목표·검증 사실)를 앞에, 관련 없는 것을 뒤에. 받은 id 를 모두 한 번씩."""
RR_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["order"], "properties": {
    "order": {"type": "array", "items": {"type": "string"}}}}


def _par(fn, items, n=8):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))


def cmd_qmap(b):
    canon = json.load(open(b.d / "canon2.json"))
    listing = "[지표 목록]\n" + "\n".join(f"{c['id']}: {c['canonical']}" for c in canon)

    def one(q):
        raw, row = llm.respond(MODEL, Q_INSTR, listing + "\n\n[질의]\n" + q["text"], Q_SCHEMA, b.out, f"sg1d:qmap:{q['id']}")
        r = json.loads(raw)
        r["ids"] = [i for i in r["ids"] if 0 <= i < len(canon)][:8]
        r["usd"], r["sec"] = row["costUsd"], row["seconds"]
        return q["id"], r

    res = dict(_par(one, b.queries))
    json.dump(res, open(b.d / "qmap2.json", "w"), ensure_ascii=False, indent=1)
    print("qmap", len(res), f"${sum(r['usd'] for r in res.values()):.4f}")


def cmd_qx(b):
    def one(q):
        raw, row = llm.respond(MODEL, QX_INSTR, q["text"], QX_SCHEMA, b.out, f"sg1d:qx:{q['id']}")
        r = json.loads(raw)
        r["usd"], r["sec"] = row["costUsd"], row["seconds"]
        return q["id"], r

    res = dict(_par(one, b.queries))
    texts, owner = [], []
    for q in b.queries:
        r = res[q["id"]]
        for t in r["phrasings"] + ([" ".join(r["metrics"])] if r["metrics"] else []):
            texts.append(t)
            owner.append(q["id"])
    np.save(b.d / "emb-qx.npy", np.array(llm.embed(texts, b.out, "sg1d:qx-emb"), dtype="float32"))
    json.dump({"res": res, "texts": texts, "owner": owner}, open(b.d / "qx.json", "w"), ensure_ascii=False, indent=1)
    print("qx", len(res), f"${sum(r['usd'] for r in res.values()):.4f}")


def _pid(x):
    """luna 가 id 대신 「[P12] 글…」 줄을 통째로 돌려주는 때가 있다(삼성 첫 판 16/30) — 앞의 id 만."""
    m = re.match(r"\s*\[?(P\d+)", x)
    return m.group(1) if m else x


def cmd_rerank(b):
    orders = json.load(open(b.d / "orders.json"))

    def one(q):
        base = orders[q["id"]]["P-ts P-t+구조"][:50]
        lines = [f"[{p}] " + b.ps[b.idx[p]].text.replace("\n", " / ")[:300] for p in base]
        raw, row = llm.respond(MODEL, RR_INSTR, f"[질의]\n{q['text']}\n\n[단락]\n" + "\n".join(lines), RR_SCHEMA, b.out,
                               f"sg1d:rr:{q['id']}")
        got = [p for p in dict.fromkeys(_pid(x) for x in json.loads(raw)["order"]) if p in set(base)]
        (b.d / "rerank-raw").mkdir(exist_ok=True)
        (b.d / "rerank-raw" / f"{q['id']}.json").write_text(raw)
        return q["id"], {"order": got + [p for p in base if p not in set(got)], "usd": row["costUsd"],
                         "sec": row["seconds"], "returned": len(got)}

    f = b.d / "rerank.json"
    res = json.load(open(f)) if f.exists() else {}
    for _ in range(2):  # 받은 id 가 반도 안 맞으면 다시(삼성 첫 판에서 16/30 이 0개 — 원인 못 찾음, 다시 부르면 됨)
        todo = [q for q in b.queries if res.get(q["id"], {}).get("returned", 0) < 25]
        res.update(dict(_par(one, todo)))
    json.dump(res, open(f, "w"), ensure_ascii=False, indent=1)
    print("rerank", len(res), f"${sum(r['usd'] for r in res.values()):.4f}",
          "돌려준 id 중앙", sorted(r["returned"] for r in res.values())[len(res) // 2])


# ---- 차례 만들기 ----
def _graph(b):
    edges = []
    for i, p in enumerate(b.ps):
        if i + 1 < len(b.ps) and b.ps[i + 1].section_id == p.section_id:
            edges.append((p.id, b.ps[i + 1].id, "next_block"))
        edges.append((p.id, f"sec:{p.section_id}", "section_contains"))
        for pg in p.pages:
            edges.append((p.id, f"page:{pg}", "on_page"))
    return retrieve.Graph(edges, dict(retrieve.STRUCT_W))


def _rrf_orders(qvec, qtext, U, bm, drop):
    va = [i for i in retrieve.ranks(list(U @ qvec)) if i not in drop]
    vb = [i for i in retrieve.ranks(bm.scores(qtext)) if i not in drop]
    cs = retrieve.rrf(va, vb)
    return va, vb, cs


def cmd_orders(b):
    U = _norm(np.load(b.d / "emb-pass.npy"))
    Q = _norm(np.load(b.d / "emb-q.npy"))
    bm = lexical.BM25([p.text for p in b.ps])
    has_t = (b.d / "emb-tagtext.npy").exists()
    if has_t:
        UT = _norm(np.load(b.d / "emb-tagtext.npy"))
        bmt = lexical.BM25(b.tagtext())
    g = _graph(b)
    qx = json.load(open(b.d / "qx.json")) if (b.d / "qx.json").exists() else None
    QX = _norm(np.load(b.d / "emb-qx.npy")) if qx else None
    rr = json.load(open(b.d / "rerank.json")) if (b.d / "rerank.json").exists() else {}
    qm = json.load(open(b.d / "qmap2.json")) if (b.d / "qmap2.json").exists() else None
    canon = json.load(open(b.d / "canon2.json")) if qm else None
    tags = b.tags()
    pages = defaultdict(list)
    for i, p in enumerate(b.ps):
        pages[p.pages[0]].append(i)
    out = {}
    for qi, q in enumerate(b.queries):
        drop = b.drop(q)
        va, vb, cs = _rrf_orders(Q[qi], q["text"], U, bm, drop)
        o = {"P-a 벡터": va, "P-b BM25": vb, "P-c 벡터+BM25": sorted(cs, key=lambda i: -cs[i])}
        o["P-s P-c+구조"] = [i for i in retrieve.expand(cs, b.ids, g) if i not in drop]
        # 쪽 퍼넣기: 쪽 점수 = 그 쪽 단락의 P-c 최고 점수, 쪽 안은 읽는 차례
        ps_ = {}
        for i, s in cs.items():
            for pg in b.ps[i].pages:
                ps_[pg] = max(ps_.get(pg, 0), s)
        seen, dump = set(), []
        for pg in sorted(ps_, key=lambda x: -ps_[x]):
            for i in pages.get(pg, []):
                if i not in seen and i not in drop:
                    seen.add(i)
                    dump.append(i)
        o["쪽 퍼넣기"] = dump + [i for i in o["P-c 벡터+BM25"] if i not in seen]
        if has_t:
            ta, tb, cst = _rrf_orders(Q[qi], q["text"], UT, bmt, drop)
            o["P-t 이름표-글 색인"] = sorted(cst, key=lambda i: -cst[i])
            o["P-ts P-t+구조"] = [i for i in retrieve.expand(cst, b.ids, g) if i not in drop]
        if qx:
            lists = [va, vb]
            for k, (t, ow) in enumerate(zip(qx["texts"], qx["owner"])):
                if ow == q["id"]:
                    lists.append([i for i in retrieve.ranks(list(U @ QX[k])) if i not in drop])
                    lists.append([i for i in retrieve.ranks(bm.scores(t)) if i not in drop])
            cx = retrieve.rrf(*lists)
            o["Q-x 질의 확장"] = sorted(cx, key=lambda i: -cx[i])
        if q["id"] in rr:
            top = [b.idx[p] for p in rr[q["id"]]["order"]]
            o["Q-r 재순위(P-ts 상위 50)"] = top + [i for i in o["P-ts P-t+구조"] if i not in set(top)]
        if qm and has_t:
            m = qm.get(q["id"], {"ids": [], "division": None})
            hit = {b.idx[p] for cid in m["ids"] for p in canon[cid]["passages"]} - drop
            div = {i for i in hit if _div_ok((tags.get(b.ids[i]) or {}).get("division"), m["division"])}
            base = o["P-ts P-t+구조"]
            pos = {i: r for r, i in enumerate(base)}
            first = sorted(div, key=lambda i: pos.get(i, 1e9))
            o["Q-f 지표 마디 먼저(+P-ts)"] = first + [i for i in base if i not in set(first)]
        out[q["id"]] = {k: [b.ids[i] for i in v] for k, v in o.items()}
    json.dump(out, open(b.d / "orders.json", "w"), ensure_ascii=False)
    print("차례", len(out), "갈래", len(next(iter(out.values()))))


# ---- 판정용 풀 ----
def cmd_pool(b):
    orders = json.load(open(b.d / "orders.json"))
    P_ = {p.id: p for p in b.ps}
    pool, md = {}, []
    for q in b.queries:
        have = {x["id"] for x in b.gold.get(q["id"], [])} | {q.get("self_passage")}
        cand = sorted({p for arm in orders[q["id"]].values() for p in arm[:TOPN]} - have, key=lambda s: int(s[1:]))
        pool[q["id"]] = cand
        md.append(f"## {q['id']} ({q['type']}) {q['text']}\n")
        for p in cand:
            pg = P_[p].pages
            md.append(f"- {p} (p{pg[0]}{'-' + str(pg[-1]) if len(pg) > 1 else ''} {P_[p].kind}) "
                      + P_[p].text.replace("\n", " / ")[:600])
        md.append("")
    json.dump(pool, open(Path(b.out) / "pool-to-judge.json", "w"), ensure_ascii=False, indent=1)
    (Path(b.out) / "pool-to-judge.md").write_text("\n".join(md))
    print("판정 후보", sum(len(v) for v in pool.values()), "질의당", [len(v) for v in pool.values()])


def cmd_eval(b):
    from .evaluate2 import run
    run(b)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--book", required=True, choices=list(BOOKS))
    ap.add_argument("--gold", default="gold-passage.json")  # 판정 보강 뒤 얼린 판은 gold-final.json
    a = ap.parse_args(argv)
    b = Book(a.book, a.gold)
    steps = {"all": ["prep", "canon", "qmap", "qx", "orders", "rerank", "orders", "eval", "pool"]}.get(a.cmd, [a.cmd])
    for s in steps:
        globals()[f"cmd_{s}"](b)


if __name__ == "__main__":
    main()
