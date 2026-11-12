"""분해본(document.json)을 찾는 단위와 구조 관계로 편다. AI 0.

단위 = 블록 하나(문단·제목·표 하나). 표는 칸을 행마다 ' | ' 로 이어 한 단위로 둔다.
구조 관계(RP2-design §1-6 의 단위 판): 다음 블록 · 제목 · 같은 섹션 · 같은 쪽. 섹션·쪽은 가운데 마디를 둔다.
"""

import json
from dataclasses import dataclass, field


@dataclass
class Unit:
    id: str
    kind: str  # paragraph | heading | table
    page: int
    section_id: str
    order: int
    text: str
    rows: list = field(default_factory=list)  # 표만: 칸 목록의 목록


def load(path):
    doc = json.load(open(path))
    tables = {t["id"]: t for t in doc["tables"]}
    units = []
    for b in sorted(doc["blocks"], key=lambda b: (b["page"], b["order"])):
        if b["kind"] == "table":
            t = tables[b["table_id"]]
            rows = [[c or "" for c in r] for r in t["cells"]]
            text = "\n".join(" | ".join(c for c in r if c) for r in rows)
            units.append(Unit(t["id"], "table", b["page"], b["section_id"], b["order"], text, rows))
        else:
            units.append(Unit(b["id"], b["kind"], b["page"], b["section_id"], b["order"], b.get("text") or ""))
    return doc, units


def structure_edges(doc, units):
    """(가, 나, 이름) 목록. 섹션·쪽은 가운데 마디 'sec:<id>'·'page:<n>' 로 잇는다."""
    edges = []
    for a, b in zip(units, units[1:]):
        if a.section_id == b.section_id:
            edges.append((a.id, b.id, "next_block"))
    heading = {}
    for u in units:
        if u.kind == "heading":
            heading[u.section_id] = u.id
        elif u.section_id in heading:
            edges.append((u.id, heading[u.section_id], "title_path"))
        edges.append((u.id, f"sec:{u.section_id}", "section_contains"))
        edges.append((u.id, f"page:{u.page}", "on_page"))
    return edges
