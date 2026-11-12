"""SG1b — 이름표(사실층) · 같은 것 잇기 · 질의를 마디로.

① tag: 섹션마다 luna 가 블록에 이름표를 단다 — 지표 이름(자유) · 부문 · 연도 · 종류. 표 행 머리는 코드로 지표 이름에 더한다.
② canon: 지표 이름을 모아 luna 가 같은 것끼리 묶는다 → 정규 지표 id. 블록 ↔ 지표 마디.
③ qmap: 질의마다 luna 가 정규 지표 목록에서 고른다(+ 부문).
④ 찾기: 질의 지표에 걸린 블록(부문이 맞으면 더)을 ⓔ 점수 차례로 먼저, 남는 예산은 ⓔ+구조 차례로 채운다(f1).
   f2 = ⓔ+구조 점수에 지표 일치 덤을 더함.
산출 <out>/facts/: tags.jsonl · names.json · canon.json · qmap.json
"""

import json
import re
from collections import defaultdict
from pathlib import Path

from . import llm
from .enrich import enriched_texts

KINDS = ["value", "target", "activity", "assurance", "policy", "other"]

TAG_INSTR = """너는 ESG 보고서 블록에 「무엇에 대한 데이터인가」 이름표를 다는 사람이다. 각 줄은 [블록id] 글 이다.
표는 행을 ' / ', 칸을 ' | ' 로 이었고, 「이름표:」 뒤는 그 표 앞의 제목·문단이다(표 숫자가 무엇인지 알려 줌).

블록마다(제목 블록·목차·빈 글은 빼도 됨):
- metrics: 그 블록이 말하는 지표·주제 이름, 보고서 말 그대로 짧게(예: 「Scope 1 배출량」「용수 재이용량」「재생에너지 전환율」「LTIR」「협력회사 제3자 검증」). 1~5개.
- division: DX · DS · 전사 · 지역 이름(국내·베트남 등) · null
- years: 나오는 연도들(「2025」「2030」 꼴)
- kind: value(측정·실적 수치) · target(목표) · activity(한 일) · assurance(검증·인증) · policy(방침·체계) · other
원문에 없는 것은 넣지 않는다."""

TAG_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["tags"], "properties": {"tags": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["block", "metrics", "division", "years", "kind"],
                               "properties": {"block": {"type": "string"},
                                              "metrics": {"type": "array", "items": {"type": "string"}},
                                              "division": {"type": ["string", "null"]},
                                              "years": {"type": "array", "items": {"type": "string"}},
                                              "kind": {"type": "string", "enum": KINDS}}}}}}

CANON_INSTR = """아래는 한 ESG 보고서에서 나온 지표·주제 이름 목록이다(번호: 이름). 같은 지표·주제를 가리키는 이름끼리 묶어라.
- 같은 것 = 같은 대상을 재는 같은 뜻(「용수 취수량」「총 수자원 취수량」「취수량」은 하나). 부문·연도·지역 차이는 무시하고 묶는다.
- 다른 것 = 재는 대상이 다름(「취수량」과 「재이용량」은 다름. 「Scope 1」과 「Scope 2」는 다름).
- 너무 넓게 묶지 않는다(「환경」 하나로 몰지 않음).
- 모든 번호는 정확히 한 묶음에. canonical 은 그 묶음의 대표 이름(짧게)."""

CANON_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["groups"], "properties": {"groups": {
    "type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["canonical", "members"],
                               "properties": {"canonical": {"type": "string"},
                                              "members": {"type": "array", "items": {"type": "integer"}}}}}}}

Q_INSTR = """아래 [지표 목록](번호: 이름)에서, 질의에 답하려면 찾아야 할 지표·주제를 골라라(1~8개, 직접 관련된 것만).
division 은 질의가 특정 부문(DX·DS)이나 지역만 말하면 그것, 아니면 null."""

Q_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ids", "division"], "properties": {
    "ids": {"type": "array", "items": {"type": "integer"}}, "division": {"type": ["string", "null"]}}}

NUMERIC = re.compile(r"^[\d,.\s%()\-’'~]+$")


def _dir(out):
    d = Path(out) / "facts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_tag(doc, units, out, model="gpt-6-luna", dry_run=False):
    d = _dir(out)
    texts = enriched_texts(doc, units)
    by_sec = defaultdict(list)
    for u, t in zip(units, texts):
        if u.kind == "heading" or not u.text.strip():
            continue
        if u.kind == "table":
            head, body = t.split("\n", 1)
            line = f"[{u.id}] 이름표: {head[:200]} // " + body.replace("\n", " / ")[:1800]
        else:
            line = f"[{u.id}] " + u.text.replace("\n", " / ")[:900]
        by_sec[u.section_id].append((u.id, line))
    if dry_run:
        n = sum(len(l) for v in by_sec.values() for _, l in v)
        print(f"{model} 호출 {len(by_sec)} · 입력 약 {n:,}자")
        return
    path = d / f"tags-{model}.jsonl"
    done = {json.loads(l)["section"] for l in open(path)} if path.exists() else set()
    for sid, rows in by_sec.items():
        if sid in done:
            continue
        ids = {i for i, _ in rows}
        raw, row = llm.respond(model, TAG_INSTR, "\n".join(l for _, l in rows), TAG_SCHEMA, out, f"tag:{sid}")
        try:
            tags = [t for t in json.loads(raw)["tags"] if t["block"] in ids]
        except Exception:
            tags = []
        with open(path, "a") as f:
            f.write(json.dumps({"section": sid, "tags": tags}, ensure_ascii=False) + "\n")
        print(f"{sid} 이름표 {len(tags)}/{len(rows)} ${row['costUsd']:.4f} {row['seconds']}s", flush=True)


def _row_labels(u):
    """표 행 머리(숫자 아닌 첫 칸) — 코드 몫 이름."""
    out = []
    for r in u.rows[1:]:
        for c in r:
            c = re.sub(r"\d\)|\*", "", c or "").strip()
            if c and not NUMERIC.match(c) and 2 <= len(c) <= 40:
                out.append(c)
                break
    return out


def load_tags(out, model="gpt-6-luna"):
    tags = {}
    for line in open(_dir(out) / f"tags-{model}.jsonl"):
        for t in json.loads(line)["tags"]:
            tags[t["block"]] = t
    return tags


def cmd_canon(units, out, model="gpt-6-luna"):
    d = _dir(out)
    tags = load_tags(out)
    names = defaultdict(set)  # 이름 → 블록들
    for b, t in tags.items():
        for m in t["metrics"]:
            names[m.strip()].add(b)
    for u in units:
        if u.kind == "table":
            for lab in _row_labels(u):
                names[lab].add(u.id)
    names = {k: sorted(v) for k, v in names.items() if k}
    lst = sorted(names)
    json.dump({"names": lst, "blocks": names}, open(d / "names.json", "w"), ensure_ascii=False)
    groups = []
    step = 700
    for i in range(0, len(lst), step):
        part = lst[i:i + step]
        raw, row = llm.respond(model, CANON_INSTR, "\n".join(f"{i + j}: {n}" for j, n in enumerate(part)),
                               CANON_SCHEMA, out, f"canon:{i}", max_output=32000)
        gs = json.loads(raw)["groups"]
        print(f"canon {i} 이름 {len(part)} → 묶음 {len(gs)} ${row['costUsd']:.4f} {row['seconds']}s", flush=True)
        groups.extend(gs)
    # 묶음끼리 대표 이름이 같으면 합침(조각 사이)
    merged, seen = {}, set()
    for g in groups:
        key = g["canonical"].strip()
        mem = [m for m in g["members"] if 0 <= m < len(lst) and m not in seen]
        seen.update(mem)
        merged.setdefault(key, []).extend(mem)
    for i in range(len(lst)):
        if i not in seen:
            merged.setdefault(lst[i], []).append(i)
    canon = [{"id": k, "canonical": c, "names": [lst[m] for m in ms],
              "blocks": sorted({b for m in ms for b in names[lst[m]]})}
             for k, (c, ms) in enumerate(sorted(merged.items()))]
    json.dump(canon, open(d / "canon.json", "w"), ensure_ascii=False, indent=0)
    print("정규 지표", len(canon), "이름", len(lst))


def cmd_qmap(queries, out, model="gpt-6-luna"):
    d = _dir(out)
    canon = json.load(open(d / "canon.json"))
    listing = "[지표 목록]\n" + "\n".join(f"{c['id']}: {c['canonical']}" for c in canon)
    res = {}
    for q in queries:
        raw, row = llm.respond(model, Q_INSTR, listing + "\n\n[질의]\n" + q["text"], Q_SCHEMA, out, f"qmap:{q['id']}")
        r = json.loads(raw)
        r["ids"] = [i for i in r["ids"] if 0 <= i < len(canon)]
        res[q["id"]] = r
        print(q["id"], [canon[i]["canonical"] for i in r["ids"]], r["division"], f"${row['costUsd']:.4f}", flush=True)
    json.dump(res, open(d / "qmap.json", "w"), ensure_ascii=False, indent=1)


def _div_ok(tagdiv, qdiv):
    if not qdiv:
        return True
    return bool(tagdiv) and (qdiv.lower() in tagdiv.lower() or tagdiv.lower() in qdiv.lower())


def entity_lists(units, queries, out, enr_orders, graph_expand):
    """질의마다 {갈래 이름: 순서} — enr_orders = ⓔ 의 base_orders, graph_expand(c_scores) → ⓔ+구조 순서."""
    d = _dir(out)
    canon = json.load(open(d / "canon.json"))
    qmap = json.load(open(d / "qmap.json"))
    tags = load_tags(out)
    ids = [u.id for u in units]
    idx = {u: i for i, u in enumerate(ids)}
    out_lists = []
    for q, e in zip(queries, enr_orders):
        m = qmap.get(q["id"], {"ids": [], "division": None})
        hit = set()
        for cid in m["ids"]:
            hit.update(canon[cid]["blocks"])
        if q.get("self"):
            hit.discard(q["self"])
        div = {b for b in hit if _div_ok((tags.get(b) or {}).get("division"), m["division"])}
        base = graph_expand(e["c_scores"])
        pos = {i: r for r, i in enumerate(base)}
        first = sorted((idx[b] for b in div if b in idx), key=lambda i: pos.get(i, 1e9))
        rest_hit = sorted((idx[b] for b in hit - div if b in idx), key=lambda i: pos.get(i, 1e9))
        f1 = first + [i for i in base if i not in set(first)]
        f1b = first + rest_hit + [i for i in base if i not in set(first) | set(rest_hit)]
        # f2: 순위 점수 + 덤
        bonus = {i: (1.0 if ids[i] in div else 0.5 if ids[i] in hit else 0.0) for i in base}
        f2 = sorted(base, key=lambda i: -(1 / (1 + pos[i] / 20) + 0.5 * bonus[i]))
        out_lists.append({"f1 지표 마디 먼저(+부문)": f1, "f1b 지표 마디 먼저(부문 뒤)": f1b, "f2 ⓔ+구조+지표 덤": f2,
                          "_hit": len(hit), "_div": len(div)})
    return out_lists
