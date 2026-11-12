"""SG1d 찾는 단위 = 문단(조각 합침). AI 0.

- 문단: 같은 섹션의 잇단 문단 블록을 읽는 차례로 합친다. 쪽이 바뀌면 앞이 문장으로 끝나지 않을 때만 잇는다.
  문장이 끝나고 150자 이상이면, 제목·표가 끼면, 1200자를 넘으면 닫는다.
- 제목: 홀로 단위가 되지 않고 다음 단락 첫 줄에 「섹션 제목 · 가까운 제목」으로 붙는다(글자 수에 셈).
  단, 열린 단락이 150자 미만이면 제목을 닫지 않고 글로 잇는다(그림 속 숫자·낱말 조각).
- 표: 하나씩. 첫 줄 = 섹션 제목 · 가까운 제목 · 바로 앞 문단 앞 120자(enrich 와 같은 이름표), 다음 줄부터 행.
산출 <out>/passages.json · passages.txt
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

END = re.compile(r"(다|요|음|함|임|됨)\s*[.。]?\s*$|[.!?。]\s*$|[.!?]\)\s*$")
MIN, MAX = 150, 1200


@dataclass
class Passage:
    id: str
    kind: str  # paragraph | table
    pages: list
    section_id: str
    members: list
    text: str
    rows: list = field(default_factory=list)

    @property
    def page(self):
        return self.pages[0]


def build(doc, units):
    sec = {s["id"]: (s["title"] or "") for s in doc["sections"]}
    out, head, prev_par = [], {}, None
    cur = None  # [members, lines, pages, section]

    def label(sid):
        return " · ".join(t for t in (sec.get(sid, ""), head.get(sid, "")) if t)

    def close():
        nonlocal cur
        if cur and cur[0]:
            first = label(cur[3]) if cur[4] else ""
            body = " ".join(cur[1])
            out.append(Passage(f"P{len(out) + 1}", "paragraph", sorted(set(cur[2])), cur[3], cur[0],
                               (first + "\n" if first else "") + body))
        cur = None

    for u in units:
        if u.kind == "heading":
            # 그림 속 숫자·낱말이 제목으로 잡혀 짧은 조각 사이에 끼는 꼴: 단락이 짧으면 닫지 않고 글로 잇는다
            if cur and cur[3] == u.section_id and sum(len(x) + 1 for x in cur[1]) < MIN and u.text.strip():
                cur[0].append(u.id)
                cur[1].append(u.text.strip())
                cur[2].append(u.page)
                continue
            close()
            head[u.section_id] = u.text
            continue
        if u.kind == "table":
            close()
            tag = [sec.get(u.section_id, ""), head.get(u.section_id, "")]
            if prev_par is not None and prev_par.section_id == u.section_id:
                tag.append(prev_par.text[:120])
            out.append(Passage(f"P{len(out) + 1}", "table", [u.page], u.section_id, [u.id],
                               " · ".join(t for t in tag if t) + "\n" + u.text, u.rows))
            continue
        t = u.text.strip()
        if not t:
            continue
        if cur and (cur[3] != u.section_id or (u.page != cur[2][-1] and END.search(cur[1][-1]))):
            close()
        if cur is None:
            cur = [[], [], [], u.section_id, True]
        cur[0].append(u.id)
        cur[1].append(t)
        cur[2].append(u.page)
        prev_par = u
        n = sum(len(x) + 1 for x in cur[1])
        if (END.search(t) and n >= MIN) or n > MAX:
            close()
    close()
    return out


def block_map(passages):
    """블록 id → 단락 id."""
    return {b: p.id for p in passages for b in p.members}


def write(doc, passages, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    json.dump([{"id": p.id, "kind": p.kind, "pages": p.pages, "section_id": p.section_id, "members": p.members,
                "text": p.text} for p in passages], open(out / "passages.json", "w"), ensure_ascii=False, indent=0)
    sec = {s["id"]: (s["title"] or "")[:30] for s in doc["sections"]}
    with open(out / "passages.txt", "w") as f:
        for p in passages:
            pg = p.pages[0] if len(p.pages) == 1 else f"{p.pages[0]}-{p.pages[-1]}"
            f.write(f"{p.id}\t{pg}\t{p.section_id}\t{sec.get(p.section_id, '')}\t{p.kind}\t"
                    + p.text.replace("\n", " / ") + "\n")


def remap_gold(gold, bmap):
    """블록 정답 → 단락 정답. 단락 이름 = 구성 블록 중 가장 센 것(core > aux)."""
    out = {}
    for q, items in gold.items():
        best = {}
        for x in items:
            p = bmap.get(x["id"])
            if p is None:
                continue
            if best.get(p) != "core":
                best[p] = x["label"] if x["label"] == "core" else best.get(p, x["label"])
        out[q] = [{"id": p, "label": l} for p, l in best.items()]
    return out


def remap_tags(tags, passages):
    """블록 이름표 → 단락 이름표(합집합). division 은 처음 것, kind 는 목록."""
    out = {}
    for p in passages:
        ts = [tags[b] for b in p.members if b in tags]
        if not ts:
            continue
        out[p.id] = {"metrics": sorted({m for t in ts for m in t["metrics"]}),
                     "division": next((t["division"] for t in ts if t["division"]), None),
                     "years": sorted({y for t in ts for y in t["years"]}),
                     "kinds": sorted({t["kind"] for t in ts})}
    return out


def remap_blocks(blocks, bmap):
    """블록 id 목록(canon 의 blocks 등) → 단락 id 목록."""
    return sorted({bmap[b] for b in blocks if b in bmap}, key=lambda s: int(s[1:]))
