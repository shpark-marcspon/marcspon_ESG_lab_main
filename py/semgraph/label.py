"""의미 관계 AI 몫 — 이름 붙이기. 섹션 하나 = 호출 하나.

코드가 후보를 준다: 그 섹션의 블록 + 섹션 밖 후보(코드 관계가 가리키는 단위 + 섹션 글로 BM25 상위). AI 는 짝을 골라 이름을 붙이고,
주어진 id 밖의 짝은 버린다. 산출 <out>/ai-edges-<모델>.jsonl, 원응답 <out>/raw/.
"""

import json
from collections import defaultdict
from pathlib import Path

from . import lexical, llm
from .codeedges import code_edges

RELS = {
    "supports": "근거한다 — 가(수치·활동·사례)가 나(주장·목표 달성·평가 문장)를 받친다",
    "target_of": "목표 대비 — 가(실적 수치·표)가 나(목표)의 이행 정도를 보인다",
    "assures": "검증한다 — 가(검증·인증·보증 내용)가 나(수치·활동)를 검증·인증한다",
    "explains": "설명한다 — 가(산정 방식·정의·배경 설명)가 나(표·수치)를 설명한다",
    "scopes_ai": "범위 한정 — 가(각주·주석·기준 문장)가 나(수치·표·주장)의 범위·기준을 정한다",
    "same_metric_ai": "같은 지표 — 가와 나가 같은 지표(같은 대상·같은 뜻)의 값을 말한다(다른 연도·부문이어도)",
}

INSTR = """너는 ESG 보고서 블록 사이의 관계에 이름을 붙이는 사람이다.
[섹션] 은 한 섹션의 블록들이고, [밖 후보] 는 다른 곳의 블록들이다. 각 줄은 [블록id] 글 이다(표는 행을 ' / ' 로 이음).

관계 이름(방향은 가 → 나):
""" + "\n".join(f"- {k}: {v}" for k, v in RELS.items()) + """

규칙:
- 가·나 중 적어도 하나는 [섹션] 블록이어야 한다. 주어진 id 만 쓴다.
- 글을 읽고 실제로 그 관계가 있을 때만 단다. 같은 낱말이 있을 뿐인 짝, 제목 블록, 막연한 주제 겹침은 달지 않는다.
- 섹션 하나에 40개 안. 확실한 것부터.
- why 는 15자 안."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["edges"],
    "properties": {"edges": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["src", "dst", "rel", "why"],
        "properties": {"src": {"type": "string"}, "dst": {"type": "string"},
                       "rel": {"type": "string", "enum": list(RELS)}, "why": {"type": "string"}}}}},
}


def _line(u, n):
    return f"[{u.id}] " + u.text.replace("\n", " / ")[:n]


def section_inputs(units):
    by_sec = defaultdict(list)
    for u in units:
        by_sec[u.section_id].append(u)
    U = {u.id: u for u in units}
    code = defaultdict(set)
    for a, b, _rel in code_edges(units):
        code[a].add(b)
        code[b].add(a)
    bm = lexical.BM25([u.text for u in units])
    out = {}
    for sid, us in by_sec.items():
        mine = {u.id for u in us}
        body = [u for u in us if u.kind != "heading" and u.text.strip()]
        if not body:
            continue
        ext = []
        for u in body:
            for v in sorted(code[u.id]):
                if v not in mine and v not in ext and ":" not in v:
                    ext.append(v)
        scores = bm.scores(" ".join(u.text for u in body)[:4000])
        for i in sorted(range(len(units)), key=lambda i: -scores[i])[:30]:
            v = units[i].id
            if v not in mine and v not in ext and units[i].kind != "heading":
                ext.append(v)
        ext = ext[:25]
        text = "[섹션]\n" + "\n".join(_line(u, 1500 if u.kind == "table" else 700) for u in body)
        text += "\n\n[밖 후보]\n" + "\n".join(_line(U[v], 400) for v in ext)
        out[sid] = (text, mine, set(ext))
    return out


def run(units, out, model, sections, dry_run):
    out = Path(out)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    inputs = section_inputs(units)
    sids = list(inputs) if sections == "all" else sections.split(",")
    if dry_run:
        n = sum(len(inputs[s][0]) for s in sids) + len(INSTR) * len(sids)
        print(f"{model} 호출 {len(sids)} · 입력 약 {n:,}자 · 출력 어림 {len(sids) * 1500:,} 토큰")
        return
    path = out / f"ai-edges-{model}.jsonl"
    done = set()
    if path.exists():
        done = {json.loads(l)["section"] for l in open(path)}
    for sid in sids:
        if sid in done:
            continue
        text, mine, ext = inputs[sid]
        raw, row = llm.respond(model, INSTR, text, SCHEMA, out, f"label:{sid}")
        (out / "raw" / f"{model}-{sid}.json").write_text(raw or "")
        try:
            edges = json.loads(raw)["edges"]
        except Exception:
            edges = []
        ok = [e for e in edges if {e["src"], e["dst"]} <= (mine | ext) and (e["src"] in mine or e["dst"] in mine)
              and e["src"] != e["dst"]]
        with open(path, "a") as f:
            for e in ok:
                f.write(json.dumps({"section": sid, **e}, ensure_ascii=False) + "\n")
            if not ok:
                f.write(json.dumps({"section": sid, "src": "", "dst": "", "rel": "none", "why": ""}) + "\n")
        print(f"{sid} 관계 {len(ok)}/{len(edges)} 입력 {row['inputTokens']} 출력 {row['outputTokens']} "
              f"${row['costUsd']:.4f} {row['seconds']}s", flush=True)
