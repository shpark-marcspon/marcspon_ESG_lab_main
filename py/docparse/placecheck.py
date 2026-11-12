"""글자 자리의 측정 도구: 값 후보의 상자가 그 숫자를 덮는지 PDF 를 다시 읽어 보고, 눈으로 볼 표본 그림을 만든다.

입력은 `ts/reconcile` 의 `places-cli.ts fact-places` 가 낸 `fact-places.json` (값 후보마다 원문 글자와 상자)이다.
여기에는 숫자를 알아보는 규칙이 없다: 상자 안의 글자를 PDF 에서 읽어 그 원문 글자와 같은지만 본다.
분해의 길(`layout.page_chunks`)을 거치지 않고 PDF 의 글자를 따로 읽는다 (보이지 않는 작은 글자, 누운 글자도 포함).
"""
import json
import random
from pathlib import Path

import pymupdf

BUCKETS = ("table_cell", "table_text_cell", "sentence", "number_only_block")
BUCKET_KO = {"table_cell": "표의 숫자 칸", "table_text_cell": "표의 글 칸 속", "sentence": "문단 속", "number_only_block": "숫자뿐인 블록"}
CENTER_PAD = 0.15  # 글자의 가운데가 상자 안인지 볼 때의 여유 (pt). 좌표를 소수 한 자리로 반올림해 싣기 때문이다


def _squash(text):
    return "".join(text.split())


def page_chars(page):
    """쪽의 글자 전부: [(글자, x0, y0, x1, y1, 글자 크기)]. 공백은 뺀다."""
    out = []
    for block in page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for c in span["chars"]:
                    if c["c"].strip():
                        out.append((c["c"], *c["bbox"], span["size"]))
    return out


def text_in_box(chars, box):
    """가운데가 상자 안에 든 글자를 왼쪽부터 이은 것과 그 글자들."""
    hit = [c for c in chars if box[0] - CENTER_PAD <= (c[1] + c[3]) / 2 <= box[2] + CENTER_PAD and box[1] - CENTER_PAD <= (c[2] + c[4]) / 2 <= box[3] + CENTER_PAD]
    hit.sort(key=lambda c: (c[1], c[2]))
    return "".join(c[0] for c in hit), hit


def classify(raw, got, hits):
    """기계 확인의 판정과 어긋난 까닭."""
    want = _squash(raw)
    if got == want:
        return "same", None
    sizes = sorted(c[5] for c in hits)
    if any(s < 2.0 for s in sizes) and "".join(c[0] for c in sorted((c for c in hits if c[5] >= 2.0), key=lambda c: (c[1], c[2]))) == want:
        return "differs", "hidden_tiny_text_in_box"   # 2pt 미만의 보이지 않는 글자가 같은 자리에 겹쳐 있다
    seen, once = set(), []
    for c in hits:
        key = (c[0], round(c[1], 1), round(c[2], 1))
        if key not in seen:
            seen.add(key)
            once.append(c)
    if "".join(c[0] for c in once) == want:
        return "differs", "same_glyph_drawn_twice"    # 같은 글자가 같은 자리에 두 번 그려져 있다
    if want in got:
        return "differs", "other_text_overlaps_box"   # 상자 안에 그 숫자가 다 있고 다른 글자도 걸친다
    if got and got in want:
        return "differs", "part_of_number_outside_box"
    if sorted(got) == sorted(want):
        return "differs", "same_chars_other_order"    # 글자는 같고 왼쪽부터 이은 차례만 다르다 (겹쳐 놓인 글자)
    return "differs", "other"


def check(pdf_path, fact_places):
    """값 후보마다의 기계 확인. [{id, bucket, page, raw, found, verdict, why, got, lines}]"""
    rows = []
    with pymupdf.open(pdf_path) as doc:
        cache = {}
        for f in fact_places["facts"]:
            row = {"id": f["id"], "bucket": f["bucket"], "page": f["page"], "raw": f["raw"], "found": f["found"],
                   "lines": len(f["boxes"]), "covered": f["covered"], "length": f["length"]}
            if not f["found"]:
                row.update({"verdict": "no_box", "why": f["reason"], "got": None})
            else:
                if f["page"] not in cache:
                    cache[f["page"]] = page_chars(doc[f["page"] - 1])
                got, hits = "", []
                for box in f["boxes"]:
                    t, h = text_in_box(cache[f["page"]], box)
                    got += t
                    hits += h
                verdict, why = classify(f["raw"], got, hits)
                row.update({"verdict": verdict, "why": why, "got": got})
            rows.append(row)
    return rows


def summarize(rows):
    """갈래별 수. 분모는 값 후보 전체."""
    out = {}
    for bucket in BUCKETS + ("all",):
        mine = [r for r in rows if bucket == "all" or r["bucket"] == bucket]
        why = {}
        for r in mine:
            if r["verdict"] != "same":
                why[f'{r["verdict"]}:{r["why"]}'] = why.get(f'{r["verdict"]}:{r["why"]}', 0) + 1
        out[bucket] = {"facts": len(mine), "box": sum(1 for r in mine if r["found"]), "no_box": sum(1 for r in mine if not r["found"]),
                       "same": sum(1 for r in mine if r["verdict"] == "same"), "differs": sum(1 for r in mine if r["verdict"] == "differs"),
                       "two_lines": sum(1 for r in mine if r["lines"] >= 2), "span_with_gap": sum(1 for r in mine if r["found"] and r["covered"] != r["length"]),
                       "why": dict(sorted(why.items()))}
    return out


def render_summary(name, summary):
    lines = [f"### {name}", "", "| 갈래 | 값 후보 | 상자를 얻음 | 못 얻음 | 상자 안의 글자가 원문과 같음 | 다름 | 상자가 둘 이상(여러 줄) |", "|---|---:|---:|---:|---:|---:|---:|"]
    for bucket in BUCKETS + ("all",):
        s = summary[bucket]
        lines.append(f'| {BUCKET_KO.get(bucket, "전체")} | {s["facts"]} | {s["box"]}/{s["facts"]} | {s["no_box"]}/{s["facts"]} | {s["same"]}/{s["box"]} | {s["differs"]}/{s["box"]} | {s["two_lines"]}/{s["box"]} |')
    lines.append("")
    why = summary["all"]["why"]
    if why:
        lines += ["어긋난 까닭:", ""] + [f"- `{k}`: {v}" for k, v in why.items()] + [""]
    return "\n".join(lines)


def draw_sample(fact_places, seed, per_bucket, total=0):
    """갈래마다 per_bucket 자리씩 씨앗으로 뽑는다 (모자라면 있는 만큼). 상자를 얻은 값 후보에서만.

    total 을 주면, 갈래가 모자라 덜 뽑힌 만큼을 남은 값 후보 전체에서 더 뽑아 채운다.
    """
    rng = random.Random(seed)
    picked = []
    for bucket in BUCKETS:
        pool = sorted((f for f in fact_places["facts"] if f["bucket"] == bucket and f["found"]), key=lambda f: f["id"])
        picked += rng.sample(pool, min(per_bucket, len(pool)))
    if total > len(picked):
        taken = {f["id"] for f in picked}
        rest = sorted((f for f in fact_places["facts"] if f["found"] and f["id"] not in taken), key=lambda f: f["id"])
        picked += rng.sample(rest, min(total - len(picked), len(rest)))
    return picked


def _ascii(text):
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in text)


def render_sheets(pdf_path, sample, out_dir, per_sheet=12, margin_x=70.0, margin_y=16.0, zoom=3.0, dpi=110):
    """표본의 자리마다 쪽의 한 조각을 잘라 상자를 그리고, 여러 조각을 한 장에 모은 PNG 를 쓴다. (파일 목록, 자리 목록)

    조각에는 번호만 적는다. 어떤 글자여야 하는지는 자리 목록(sample.json)에 있다.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols, cell_w, cell_h, label_h = 2, 2 * margin_x * zoom + 120 * zoom, 0, 12
    files, listing = [], []
    with pymupdf.open(pdf_path) as src:
        for s0 in range(0, len(sample), per_sheet):
            part = sample[s0:s0 + per_sheet]
            clips = []
            for f in part:
                x0 = min(b[0] for b in f["boxes"]); y0 = min(b[1] for b in f["boxes"])
                x1 = max(b[2] for b in f["boxes"]); y1 = max(b[3] for b in f["boxes"])
                page = src[f["page"] - 1]
                clip = pymupdf.Rect(x0 - margin_x, y0 - margin_y, x1 + margin_x, y1 + margin_y) & page.rect
                clips.append(clip)
            cell_w = max(c.width for c in clips) * zoom + 10
            heights = [c.height * zoom + label_h + 10 for c in clips]
            rows = [max(heights[i:i + cols]) for i in range(0, len(part), cols)]
            sheet = pymupdf.open()
            page_out = sheet.new_page(width=cols * cell_w + 10, height=sum(rows) + 10)
            y = 5
            for i, (f, clip) in enumerate(zip(part, clips)):
                if i and i % cols == 0:
                    y += rows[i // cols - 1]
                x = 5 + (i % cols) * cell_w
                n = s0 + i + 1
                page_out.insert_text((x, y + 9), f"#{n}", fontname="helv", fontsize=9, color=(0, 0, 0.8))
                target = pymupdf.Rect(x, y + label_h, x + clip.width * zoom, y + label_h + clip.height * zoom)
                page_out.show_pdf_page(target, src, f["page"] - 1, clip=clip)
                page_out.draw_rect(target, color=(0.6, 0.6, 0.6), width=0.3)
                for b in f["boxes"]:
                    r = pymupdf.Rect(target.x0 + (b[0] - clip.x0) * zoom, target.y0 + (b[1] - clip.y0) * zoom,
                                     target.x0 + (b[2] - clip.x0) * zoom, target.y0 + (b[3] - clip.y0) * zoom)
                    page_out.draw_rect(r, color=(1, 0, 0), width=0.5)
                listing.append({"n": n, "sheet": f"sheet-{s0 // per_sheet + 1:03d}.png", "id": f["id"], "bucket": f["bucket"], "page": f["page"],
                                "raw": f["raw"], "boxes": f["boxes"]})
            path = out_dir / f"sheet-{s0 // per_sheet + 1:03d}.png"
            page_out.get_pixmap(dpi=dpi).save(path)
            sheet.close()
            files.append(path)
    return files, listing
