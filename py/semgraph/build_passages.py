"""SG1d 가 단계: 세 권의 단락 만들기 · 정답/이름표를 단락으로 옮기기. AI 0. py/ 에서 python3 -m semgraph.build_passages"""
import json, statistics, sys
from pathlib import Path

from semgraph.units import load
from semgraph import passages as P
from semgraph.facts import load_tags

BOOKS = [("samsung", "../out/docparse-samsung/document.json", "../out/semgraph"),
         ("cosmax", "../out/parse-cosmax_2025_kor/document.json", "../out/semgraph-cosmax"),
         ("kb", "../out/parse-kb_2025_kor/document.json", "../out/semgraph-kb")]
for name, docp, out in BOOKS:
    doc, units = load(docp)
    ps = P.build(doc, units)
    P.write(doc, ps, out)
    bmap = P.block_map(ps)
    L = sorted(len(p.text) for p in ps)
    npar = sum(1 for u in units if u.kind == "paragraph" and u.text.strip())
    kinds = {u.id: u.kind for u in units}
    merged = sum(sum(kinds[b] == "paragraph" for b in p.members) for p in ps if p.kind == "paragraph" and len(p.members) > 1)
    print(f"{name}: 블록 {len(units)} → 단락 {len(ps)} (문단 {sum(p.kind=='paragraph' for p in ps)}, 표 {sum(p.kind=='table' for p in ps)}) "
          f"중앙 {statistics.median(L):.0f} p90 {L[int(.9*len(L))]} 60자 미만 {sum(l<60 for l in L)/len(L):.1%} "
          f"합쳐진 문단 블록 {merged}/{npar} 빠진 블록 {sum(1 for u in units if u.kind!='heading' and u.text.strip() and u.id not in bmap)}")
    Path(out).mkdir(exist_ok=True)
    if name == "kb":
        with open(Path(out) / "units.txt", "w") as f:
            sec = {s["id"]: (s["title"] or "")[:30] for s in doc["sections"]}
            for u in units:
                f.write(f"{u.id}\t{u.page}\t{u.section_id}\t{sec.get(u.section_id,'')}\t{u.kind}\t{u.text.replace(chr(10),' / ')}\n")
        json.dump([{"id": s["id"], "title": s["title"], "pages": [s["page_start"], s["page_end"]]} for s in doc["sections"]],
                  open(Path(out) / "sections.json", "w"), ensure_ascii=False, indent=0)
        qd = [q for q in json.load(open("../out/semgraph/queries.json")) if q["type"] == "drafting"]
        json.dump(qd, open(Path(out) / "queries-D.json", "w"), ensure_ascii=False, indent=1)
        continue
    queries = json.load(open(Path(out) / "queries.json"))
    gold = json.load(open(Path(out) / "gold.json"))
    gp = P.remap_gold(gold, bmap)
    selfdrop = 0
    for q in queries:
        if q.get("self"):
            sp = bmap.get(q["self"])
            q["self_passage"] = sp
            before = [x for x in gp[q["id"]] if x["id"] == sp]
            selfdrop += sum(1 for x in before if x["label"] == "core")
            gp[q["id"]] = [x for x in gp[q["id"]] if x["id"] != sp]
    json.dump(gp, open(Path(out) / "gold-passage.json", "w"), ensure_ascii=False, indent=1)
    json.dump(queries, open(Path(out) / "queries-passage.json", "w"), ensure_ascii=False, indent=1)
    coreb = sum(1 for g in gold.values() for x in g if x["label"] == "core")
    corep = sum(1 for g in gp.values() for x in g if x["label"] == "core")
    multi_q = 0
    for qid, g in gold.items():
        cnt = {}
        for x in g:
            if x["label"] == "core" and x["id"] in bmap:
                cnt[bmap[x["id"]]] = cnt.get(bmap[x["id"]], 0) + 1
        multi_q += any(v > 1 for v in cnt.values())
    print(f"   정답 핵심 블록 {coreb} → 핵심 단락 {corep} · 한 단락에 핵심 블록 여럿 합쳐진 질의 {multi_q}/{len(gold)} · 자기 단락에 걸려 빠진 핵심 {selfdrop}")
    tags = load_tags(out)
    tp = P.remap_tags(tags, ps)
    json.dump(tp, open(Path(out) / "facts" / "tags-passage.json", "w"), ensure_ascii=False)
    print(f"   이름표 단락 {len(tp)}/{len(ps)}")
