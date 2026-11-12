"""래스터·윤곽선 정답 짓기 보조 (RAS1 G1). 사람이 렌더 확대 이미지를 보며 spec 에 손으로 적은 격자·글을
검사·정규화해 `make_gold.py`·`figure_run.py gold` 산출과 같은 꼴의 JSON 으로 쓰고 해시를 낸다. 글자 층을 읽지 않는다.

  python3 -I gold.py <specs.json> <정답 폴더>     # <정답 폴더>/tables/·figures/·hashes.txt

spec 항목: id, report, pdf, page, bbox(pt, 위-왼), kind(table|chart|scan|infographic), class(raster|outline), lang,
  note, 그리고 grid(표) 또는 lines(글 줄 목록 — 빈칸으로 끊어 낱말).
표(grid 가 있는 항목)는 tables/<id>.json(gold-unseen3 꼴: id·report·page·bbox·form·lang·note·grid, form = raster_table|
outlined_table) 과 figures/<id>.json(낱말 = 칸 글을 빈칸으로 편 것) 둘. 나머지는 figures/ 만(gold-figure 꼴: id·report·pdf·
page·bbox·kind·class·words·method). 두 폴더로 나누는 까닭: table_score 는 grid+bbox 가 있는 JSON 만, figure_run score 는
폴더의 *.json 전부를 words 로 읽는다.
"""

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

KINDS = ("table", "chart", "scan", "infographic")
CLASSES = ("raster", "outline")
METHOD = "렌더 확대 이미지(200dpi 이상)를 보며 손으로 옮김, 두 번 읽어 대조 (글자 층 없음)"


def check(s):
    errs = []
    for k in ("id", "report", "pdf", "page", "bbox", "kind", "class"):
        if k not in s:
            errs.append(f"{k} 없음")
    if s.get("kind") not in KINDS:
        errs.append(f"kind {s.get('kind')}")
    if s.get("class") not in CLASSES:
        errs.append(f"class {s.get('class')}")
    b = s.get("bbox") or []
    if len(b) != 4 or not (b[0] < b[2] and b[1] < b[3]):
        errs.append(f"bbox {b}")
    if ("grid" in s) == ("lines" in s):
        errs.append("grid 와 lines 중 하나만")
    if "grid" in s:
        widths = {len(r) for r in s["grid"]}
        if len(widths) != 1:
            errs.append(f"격자 열 수가 행마다 다름 {sorted(widths)}")
    return errs


def nfc(t):
    return unicodedata.normalize("NFC", t)


def words_of(s):
    if "grid" in s:
        return [w for row in s["grid"] for c in row for w in nfc(c).split()]
    return [w for line in s["lines"] for w in nfc(line).split()]


def sha16(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("specs")
    ap.add_argument("out")
    a = ap.parse_args(argv)
    specs = json.loads(Path(a.specs).read_text(encoding="utf-8"))
    ids = [s.get("id") for s in specs]
    bad = {s.get("id"): e for s in specs if (e := check(s))}
    if len(set(ids)) != len(ids):
        bad["_"] = ["id 겹침"]
    if bad:
        sys.exit(f"spec 오류: {bad}")
    out = Path(a.out)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    lines = [f"specs {sha16(a.specs)} {Path(a.specs).name}"]
    for s in specs:
        if "grid" in s:
            t = {
                "id": s["id"],
                "report": s["report"],
                "page": s["page"],
                "bbox": s["bbox"],
                "form": f"{'raster' if s['class'] == 'raster' else 'outlined'}_table",
                "lang": s.get("lang", "kor"),
                "note": s.get("note", ""),
                "grid": [[nfc(c) for c in row] for row in s["grid"]],
            }
            tp = out / "tables" / f"{s['id']}.json"
            tp.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
            lines.append(f"tables/{tp.name} {sha16(tp)}")
        f = {
            "id": s["id"],
            "report": s["report"],
            "pdf": s["pdf"],
            "page": s["page"],
            "bbox": s["bbox"],
            "kind": s["kind"],
            "class": s["class"],
            "words": words_of(s),
            "method": METHOD,
        }
        fp = out / "figures" / f"{s['id']}.json"
        fp.write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
        lines.append(f"figures/{fp.name} {sha16(fp)}")
        print(f"{s['id']:<16} {s['kind']:<12} {s['class']:<8} 낱말 {len(f['words']):>4}" + (f" 격자 {len(s['grid'])}×{len(s['grid'][0])}" if "grid" in s else ""))
    (out / "hashes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(lines[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
