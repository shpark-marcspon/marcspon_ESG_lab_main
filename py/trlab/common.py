"""분해본(docparse layout 2 document.json)을 블록 목록으로 편다. 책은 파일 키(sam·cos·kb)로만 부른다.

경로는 환경 변수로 바꿀 수 있다: TRX1_OUT(산출, 기본 메인 체크아웃 esg-lab/out/trx1), TRX1_GOLD(정답, prework/gold-trx1).
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../marcspon (워크트리든 메인이든 같은 높이)
if ROOT.name.endswith(".worktrees"):
    ROOT = ROOT.parent
OUT = Path(os.environ.get("TRX1_OUT", ROOT / "esg-lab" / "out" / "trx1"))
GOLD = Path(os.environ.get("TRX1_GOLD", ROOT / "prework" / "gold-trx1"))
BOOKS = ("sam", "cos", "kb")

_NUM = re.compile(r"\d[\d,.]*\d|\d")
_LATIN = re.compile(r"[A-Za-z][A-Za-z&\-']+")
_HANGUL = re.compile(r"[가-힣]")


@dataclass
class Block:
    id: str  # 블록 id(p<쪽>-b<n>). 표도 블록 id 로 부른다(table_id 는 따로)
    kind: str  # heading | paragraph | table
    page: int
    order: int
    text: str
    bbox: tuple
    font_size: float
    region: str
    pw: float
    ph: float
    table_id: str = ""
    rows: list = field(default_factory=list)

    @property
    def cx(self):
        return (self.bbox[0] + self.bbox[2]) / 2 / self.pw

    @property
    def cy(self):
        return (self.bbox[1] + self.bbox[3]) / 2 / self.ph

    def nums(self):
        return {n.replace(",", "") for n in _NUM.findall(self.text) if len(n) >= 2}

    def latin(self):
        return {w.lower() for w in _LATIN.findall(self.text) if len(w) >= 3}


def table_text(rows):
    return "\n".join(" | ".join(c for c in r if c) for r in rows)


def load_doc(path):
    """document.json → Block 목록(쪽·차례 순). 겹쳐 그려진 같은 블록(같은 쪽·글·자리)은 하나만 둔다."""
    doc = json.load(open(path))
    sizes = {p["page"]: (p["width"], p["height"]) for p in doc["pages"]}
    tables = {t["id"]: t for t in doc["tables"]}
    seen, out = set(), []
    for b in sorted(doc["blocks"], key=lambda b: (b["page"], b["order"])):
        pw, ph = sizes[b["page"]]
        rows, tid = [], ""
        if b["kind"] == "table":
            tid = b["table_id"]
            rows = [[c or "" for c in r] for r in tables[tid]["cells"]]
            text = table_text(rows)
        else:
            text = b.get("text") or ""
        sig = (b["page"], text, tuple(round(x) for x in b["bbox"]))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(Block(b["id"], b["kind"], b["page"], b["order"], text, tuple(b["bbox"]),
                         float(b.get("font_size") or 0), b.get("region", "body"), pw, ph, tid, rows))
    return out


def load(key, lang):
    return load_doc(OUT / "parse" / f"{key}-{lang}" / "document.json")


def is_hangul(s):
    return bool(_HANGUL.search(s))


def split_bilingual(text):
    """「영문 제목 + 국문 제목」 한 블록을 (영문, 국문)으로 뗀다. 한글이 없으면 국문은 ''."""
    lat = " ".join(t for t in re.split(r"\s+", text) if t and not is_hangul(t)).strip()
    ko = " ".join(t for t in re.split(r"\s+", text) if is_hangul(t)).strip()
    return lat, ko
