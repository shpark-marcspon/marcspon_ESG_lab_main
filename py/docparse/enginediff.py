"""두 엔진의 산출을 견준다 (측정 도구, 밴드 PD1). 분해 규칙이 아니다.

두 층으로 잰다.

- 원재료(`raw_diff`): 같은 PDF 를 두 엔진으로 열어 이음매가 내는 것(글자, 줄, 스팬, 낱말, 그리기 항목, 그림 상자)을 쪽마다 견준다.
  여기서 「다른 곳의 까닭」이 나온다.
- 산출(`outputs_diff`): 두 엔진으로 낸 `document.json`·`char-places.json`·`chart-labels.json`·`chart-candidates.json`·
  `number-words.json`·`tags.json`·`page-lines.json` 을 견준다.

「같다」는 글자 그대로 같은 것이다. 좌표를 조금 봐준 수치(`near`)는 따로 적고, 봐준 폭을 이름에 밝힌다.
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .engine import open_pdf

BOX_NEAR = 0.15      # 소수 첫째 자리로 반올림한 좌표가 한 눈금 어긋난 것까지 「가깝다」
RAW_NEAR = 0.05      # 원재료의 상자: 이 안이면 같은 자리
CHAR_REACH = 1.0     # 글자를 짝지을 때 원점이 이 안이어야 한다
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f]")
CAUSES = ("char", "line", "span", "word", "drawing_kind", "drawing_color", "drawing_other", "image")
CAUSE_NAMES = {"char": "글자 차이", "line": "줄 가름", "span": "스팬 가름", "word": "낱말 가름",
               "drawing_kind": "그리기 항목 갈래", "drawing_color": "그리기 색", "drawing_other": "그리기 항목의 수·상자·굵기",
               "image": "그림 상자", "rounding": "좌표 반올림", "other": "그 밖"}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _near(a, b, tol=BOX_NEAR):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _quantiles(values):
    if not values:
        return {"n": 0}
    s = sorted(values)
    pick = lambda q: round(s[min(len(s) - 1, int(q * len(s)))], 4)
    return {"n": len(s), "zero": sum(1 for v in s if v == 0), "p50": pick(0.5), "p95": pick(0.95), "p99": pick(0.99), "max": round(s[-1], 4)}


# ── 원재료 ────────────────────────────────────────────────────────────────

def _page_raw(page):
    """쪽 하나의 원재료를 견주기 좋은 꼴로."""
    chars, lines, spans = [], [], []
    for block in page.get_text_blocks(chars=True):
        for line in block.get("lines", []):
            members = []
            for span in line["spans"]:
                mine = []
                for c in span["chars"]:
                    if c["c"].strip():
                        mine.append(len(chars))
                        chars.append((c["c"], tuple(c["bbox"])))
                if mine:
                    spans.append((mine, span["size"]))
                members.extend(mine)
            if members:
                lines.append((members, tuple(line["dir"])))
    words = [(w[4], tuple(w[:4])) for w in page.get_words() if w[4].strip()]
    drawings = []
    for d in page.get_drawings():
        drawings.append({"kinds": "".join(it[0][0] for it in d["items"]) if len(d["items"]) <= 12 else f"{len(d['items'])}:" + "".join(sorted({it[0] for it in d["items"]})),
                         "rect": tuple(d["rect"]), "fill": _hex(d.get("fill")), "color": _hex(d.get("color")),
                         "width": d.get("width"), "opaque": d.get("fill_opacity", 1) != 0, "items": len(d["items"])})
    return {"size": (page.rect.width, page.rect.height), "chars": chars, "lines": lines, "spans": spans, "words": words,
            "drawings": drawings, "images": [tuple(b) for b in page.get_image_boxes()]}


def _hex(c):
    return None if c is None else "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)


def _match_chars(a, b):
    """a 의 글자마다 b 의 같은 글자·같은 자리(원점 CHAR_REACH 안)를 하나씩 짝짓는다. {a 번호: b 번호}"""
    grid = {}
    for j, (c, box) in enumerate(b):
        grid.setdefault((c, int(box[0]), int(box[1])), []).append(j)
    used, pairs = set(), {}
    for i, (c, box) in enumerate(a):
        best, best_d = None, None
        for gx in (-1, 0, 1):
            for gy in (-1, 0, 1):
                for j in grid.get((c, int(box[0]) + gx, int(box[1]) + gy), ()):
                    if j in used:
                        continue
                    d = abs(b[j][1][0] - box[0]) + abs(b[j][1][1] - box[1])
                    if d <= CHAR_REACH and (best is None or d < best_d):
                        best, best_d = j, d
        if best is not None:
            used.add(best)
            pairs[i] = best
    return pairs


def _groups_same(groups_a, groups_b, pairs, extra_equal=None):
    """(a 의 묶음 수, b 의 묶음 수, 같은 수). 같다 = 같은 글자들을 같은 차례로 담았다.

    짝이 없는 글자(한 엔진만 낸 글자)는 묶음에서 빼고 본다: 글자 차이는 따로 세고, 여기서는 가름만 본다.
    """
    back = set(pairs.values())
    index, n_b = {}, 0
    for members, extra in groups_b:
        kept = tuple(j for j in members if j in back)
        if kept:
            index[kept] = extra
            n_b += 1
    same = n_a = 0
    for members, extra in groups_a:
        mapped = tuple(pairs[i] for i in members if i in pairs)
        if not mapped:
            continue
        n_a += 1
        if mapped in index and (extra_equal is None or extra_equal(extra, index[mapped])):
            same += 1
    return n_a, n_b, same


def _match_boxes(a, b, key, tol=RAW_NEAR):
    """상자가 tol 안에서 같은 것끼리 하나씩 짝짓는다. [(a 번호, b 번호)]"""
    grid = {}
    for j, item in enumerate(b):
        box = key(item)
        grid.setdefault((round(box[0]), round(box[1])), []).append(j)
    used, pairs = set(), []
    for i, item in enumerate(a):
        box = key(item)
        hit = None
        for gx in (-1, 0, 1):
            for gy in (-1, 0, 1):
                for j in grid.get((round(box[0]) + gx, round(box[1]) + gy), ()):
                    if j not in used and _near(box, key(b[j]), tol):
                        hit = j
                        break
                if hit is not None:
                    break
            if hit is not None:
                break
        if hit is not None:
            used.add(hit)
            pairs.append((i, hit))
    return pairs


def _page_diff(a, b, samples, pno):
    """쪽 하나의 원재료 차이. 수는 전부 (같은 수, a 의 수, b 의 수) 꼴로 나중에 더한다."""
    out = {"size_same": a["size"] == b["size"]}
    pairs = _match_chars(a["chars"], b["chars"])
    deltas = [max(abs(x - y) for x, y in zip(a["chars"][i][1], b["chars"][j][1])) for i, j in pairs.items()]
    out["chars"] = {"a": len(a["chars"]), "b": len(b["chars"]), "paired": len(pairs),
                    "box_within_0.001": sum(1 for d in deltas if d <= 0.001), "box_within_0.05": sum(1 for d in deltas if d <= 0.05)}
    out["char_box_deltas"] = deltas
    back = set(pairs.values())
    only_a = Counter(a["chars"][i][0] for i in range(len(a["chars"])) if i not in pairs)
    only_b = Counter(b["chars"][j][0] for j in range(len(b["chars"])) if j not in back)
    out["chars_only_a"], out["chars_only_b"] = only_a, only_b
    out["lines"] = dict(zip(("a", "b", "same"), _groups_same(a["lines"], b["lines"], pairs, lambda x, y: _near(x, y, 0.001))))
    out["spans"] = dict(zip(("a", "b", "same"), _groups_same(a["spans"], b["spans"], pairs, lambda x, y: abs(x - y) <= 0.01)))
    word_pairs = _match_boxes(a["words"], b["words"], lambda w: w[1])
    out["words"] = {"a": len(a["words"]), "b": len(b["words"]),
                    "same": sum(1 for i, j in word_pairs if a["words"][i][0] == b["words"][j][0])}
    dp = _match_boxes(a["drawings"], b["drawings"], lambda d: d["rect"])
    kinds = sum(1 for i, j in dp if a["drawings"][i]["kinds"] == b["drawings"][j]["kinds"])
    paint = sum(1 for i, j in dp if (a["drawings"][i]["fill"] is None) == (b["drawings"][j]["fill"] is None)
                and (a["drawings"][i]["color"] is None) == (b["drawings"][j]["color"] is None)
                and a["drawings"][i]["opaque"] == b["drawings"][j]["opaque"])
    colors = sum(1 for i, j in dp if a["drawings"][i]["fill"] == b["drawings"][j]["fill"] and a["drawings"][i]["color"] == b["drawings"][j]["color"])
    widths = sum(1 for i, j in dp if abs((a["drawings"][i]["width"] or 0) - (b["drawings"][j]["width"] or 0)) <= 0.01)
    out["drawings"] = {"a": len(a["drawings"]), "b": len(b["drawings"]), "paired": len(dp), "kinds_same": kinds,
                       "paint_same": paint, "colors_same": colors, "width_same": widths,
                       "items_a": sum(d["items"] for d in a["drawings"]), "items_b": sum(d["items"] for d in b["drawings"])}
    ip = _match_boxes(a["images"], b["images"], lambda x: x)
    out["images"] = {"a": len(a["images"]), "b": len(b["images"]), "same": len(ip)}
    causes = set()
    c = out["chars"]
    if not (c["a"] == c["b"] == c["paired"]):
        causes.add("char")
    if not (out["lines"]["a"] == out["lines"]["b"] == out["lines"]["same"]):
        causes.add("line")
    if not (out["spans"]["a"] == out["spans"]["b"] == out["spans"]["same"]):
        causes.add("span")
    if not (out["words"]["a"] == out["words"]["b"] == out["words"]["same"]):
        causes.add("word")
    d = out["drawings"]
    if d["kinds_same"] != d["paired"]:
        causes.add("drawing_kind")
    if d["colors_same"] != d["paired"]:
        causes.add("drawing_color")
    if not (d["a"] == d["b"] == d["paired"]) or d["width_same"] != d["paired"] or d["paint_same"] != d["paired"]:
        causes.add("drawing_other")
    if not (out["images"]["a"] == out["images"]["b"] == out["images"]["same"]):
        causes.add("image")
    out["causes"] = sorted(causes)
    if samples is not None:
        if "drawing_kind" in causes and len(samples["drawing_kind"]) < 12:
            i, j = next((i, j) for i, j in dp if a["drawings"][i]["kinds"] != b["drawings"][j]["kinds"])
            samples["drawing_kind"].append({"page": pno, "a": a["drawings"][i]["kinds"], "b": b["drawings"][j]["kinds"], "rect": [round(v, 1) for v in a["drawings"][i]["rect"]]})
        if "drawing_color" in causes and len(samples["drawing_color"]) < 12:
            i, j = next((i, j) for i, j in dp if (a["drawings"][i]["fill"], a["drawings"][i]["color"]) != (b["drawings"][j]["fill"], b["drawings"][j]["color"]))
            samples["drawing_color"].append({"page": pno, "a": [a["drawings"][i]["fill"], a["drawings"][i]["color"]], "b": [b["drawings"][j]["fill"], b["drawings"][j]["color"]]})
        if "word" in causes and len(samples["word"]) < 12:
            matched_a, matched_b = {i for i, j in word_pairs if a["words"][i][0] == b["words"][j][0]}, {j for i, j in word_pairs if a["words"][i][0] == b["words"][j][0]}
            samples["word"].append({"page": pno, "a": [a["words"][i][0] for i in range(len(a["words"])) if i not in matched_a][:4],
                                    "b": [b["words"][j][0] for j in range(len(b["words"])) if j not in matched_b][:4]})
        if "line" in causes and len(samples["line"]) < 12:
            index = {tuple(j for j in m if j in back) for m, _ in b["lines"]}
            text = lambda src, members: "".join(src["chars"][i][0] for i in members)[:40]
            bad = [m for m, _ in a["lines"] if tuple(pairs[i] for i in m if i in pairs) not in index][:3]
            samples["line"].append({"page": pno, "a": [text(a, m) for m in bad]})
    return out


def raw_diff(pdf_path, engine_a="pymupdf", engine_b="pdfium", pages=None):
    """문서 하나의 원재료 차이. {"totals", "pages": [{"page", "causes", ...}], "samples", "toc_same"}"""
    totals = {}
    deltas, only_a, only_b = [], Counter(), Counter()
    samples = {"drawing_kind": [], "drawing_color": [], "word": [], "line": []}
    out_pages = []
    with open_pdf(pdf_path, engine_a) as da, open_pdf(pdf_path, engine_b) as db:
        n = len(da)
        toc_a, toc_b = da.get_toc(), db.get_toc()
        for pno in (pages or range(1, n + 1)):
            ra, rb = _page_raw(da[pno - 1]), _page_raw(db[pno - 1])
            d = _page_diff(ra, rb, samples, pno)
            deltas.extend(d.pop("char_box_deltas"))
            only_a.update(d.pop("chars_only_a"))
            only_b.update(d.pop("chars_only_b"))
            for key in ("chars", "lines", "spans", "words", "drawings", "images"):
                for field, value in d[key].items():
                    totals.setdefault(key, Counter())[field] += value
            totals.setdefault("pages", Counter())["n"] += 1
            totals["pages"]["size_same"] += d["size_same"]
            out_pages.append({"page": pno, "causes": d["causes"], **{k: d[k] for k in ("chars", "lines", "spans", "words", "drawings", "images")}})
    cause_pages = {c: [p["page"] for p in out_pages if c in p["causes"]] for c in CAUSES}
    return {"engines": [engine_a, engine_b], "page_count": n, "toc": {"a": len(toc_a), "b": len(toc_b), "same": toc_a == toc_b},
            "totals": {k: dict(v) for k, v in totals.items()}, "char_box_delta": _quantiles(deltas),
            "chars_only_a": [[repr(c), k] for c, k in only_a.most_common(15)], "chars_only_b": [[repr(c), k] for c, k in only_b.most_common(15)],
            "cause_pages": cause_pages, "samples": samples, "pages": out_pages}


# ── 산출 ──────────────────────────────────────────────────────────────────

def _strip_control(text):
    return _CONTROL.sub("", text)


def _document_diff(a, b):
    out = {}
    pa, pb = {p["page"]: p for p in a["pages"]}, {p["page"]: p for p in b["pages"]}
    fields = ("width", "height", "text_layer", "char_count", "image_coverage", "columns", "notes")
    out["pages"] = {"a": len(pa), "b": len(pb), "same": sum(1 for k in pa if pa[k] == pb.get(k)),
                    **{f: sum(1 for k in pa if k in pb and pa[k][f] == pb[k][f]) for f in fields}}
    out["sections"] = {"a": len(a["sections"]), "b": len(b["sections"]), "same": sum(1 for x, y in zip(a["sections"], b["sections"]) if x == y)}
    by_page_a, by_page_b = {}, {}
    for blk in a["blocks"]:
        by_page_a.setdefault(blk["page"], []).append(blk)
    for blk in b["blocks"]:
        by_page_b.setdefault(blk["page"], []).append(blk)
    exact = near = near_ctl = order_same = 0
    page_state = {}
    for pno in sorted(set(by_page_a) | set(by_page_b)):
        xa, xb = by_page_a.get(pno, []), by_page_b.get(pno, [])
        if xa == xb:
            exact += len(xa)
            near += len(xa)
            near_ctl += len(xa)
            order_same += len(xa)
            page_state[pno] = "same"
            continue
        ids_b = {blk["id"]: blk for blk in xb}
        e = sum(1 for blk in xa if ids_b.get(blk["id"]) == blk)
        exact += e
        used = set()
        matched = []

        def find(blk, text_of):
            for j, other in enumerate(xb):
                if j in used or other["kind"] != blk["kind"] or not _near(blk["bbox"], other["bbox"]):
                    continue
                if text_of(blk.get("text", "")) == text_of(other.get("text", "")):
                    used.add(j)
                    return j
            return None

        n_near = 0
        rest = []
        for i, blk in enumerate(xa):
            j = find(blk, lambda t: t)
            if j is None:
                rest.append((i, blk))
            else:
                n_near += 1
                matched.append((i, j))
        n_ctl = n_near
        for i, blk in rest:
            j = find(blk, _strip_control)
            if j is not None:
                n_ctl += 1
                matched.append((i, j))
        near += n_near
        near_ctl += n_ctl
        order_same += sum(1 for i, j in matched if xa[i]["order"] == xb[j]["order"])
        page_state[pno] = "near" if n_near == len(xa) == len(xb) else ("near_ignoring_control" if n_ctl == len(xa) == len(xb) else "differs")
    out["blocks"] = {"a": len(a["blocks"]), "b": len(b["blocks"]), "same": exact, f"near_{BOX_NEAR}": near,
                     f"near_{BOX_NEAR}_ignoring_control_chars": near_ctl, "order_same_among_near": order_same}
    ta, tb = {}, {}
    for t in a["tables"]:
        ta.setdefault(t["page"], []).append(t)
    for t in b["tables"]:
        tb.setdefault(t["page"], []).append(t)
    same_tables = paired = header = method = status = shape = 0
    cells_total = sum(len(row) for t in a["tables"] for row in t["cells"])
    cells_same = cells_same_ctl = 0
    table_state = {}
    for pno in sorted(set(ta) | set(tb)):
        xa, xb = ta.get(pno, []), tb.get(pno, [])
        used = set()
        state = "same" if xa == xb else "differs"
        all_cells = True
        for t in xa:
            j = next((j for j, o in enumerate(xb) if j not in used and _near(t["bbox"], o["bbox"], 1.0)), None)
            if j is None:
                all_cells = False
                continue
            used.add(j)
            o = xb[j]
            paired += 1
            same_tables += t == o
            header += t["header"] == o["header"]
            method += t["method"] == o["method"]
            status += t["status"] == o["status"]
            if (t["n_rows"], t["n_cols"]) == (o["n_rows"], o["n_cols"]):
                shape += 1
                for row_a, row_b in zip(t["cells"], o["cells"]):
                    for ca, cb in zip(row_a, row_b):
                        cells_same += ca == cb
                        cells_same_ctl += _strip_control(ca) == _strip_control(cb)
                        all_cells = all_cells and ca == cb
            else:
                all_cells = False
        if state == "differs" and all_cells and len(xa) == len(xb):
            state = "cells_same"
        table_state[pno] = state
    out["tables"] = {"a": len(a["tables"]), "b": len(b["tables"]), "paired_by_bbox_1.0": paired, "same": same_tables,
                     "shape_same": shape, "header_same": header, "method_same": method, "status_same": status,
                     "cells": cells_total, "cells_same": cells_same, "cells_same_ignoring_control_chars": cells_same_ctl}
    out["page_state"] = {"blocks": page_state, "tables": table_state}
    return out


def _places_diff(a, b):
    ca, cb = {c["id"]: c for c in a["containers"]}, {c["id"]: c for c in b["containers"]}
    dx, dy, comparable = [], [], 0
    for cid, c in ca.items():
        o = cb.get(cid)
        if o is None or len(c["runs"]) != len(o["runs"]) or any(len(r["x0"]) != len(s["x0"]) for r, s in zip(c["runs"], o["runs"])):
            continue
        comparable += 1
        for r, s in zip(c["runs"], o["runs"]):
            dy.extend((abs(r["y0"] - s["y0"]), abs(r["y1"] - s["y1"])))
            dx.extend(abs(u - v) for u, v in zip(r["x0"], s["x0"]))
            dx.extend(abs(u - v) for u, v in zip(r["x1"], s["x1"]))
    return {"containers": {"a": len(ca), "b": len(cb), "same_id": len(set(ca) & set(cb)), "same": sum(1 for k in ca if ca[k] == cb.get(k)),
                           "same_id_and_glyph_count": comparable},
            "unlinked": {"a": len(a["unlinked"]), "b": len(b["unlinked"])},
            "glyph_x_delta": _quantiles(dx), "line_y_delta": _quantiles(dy)}


def _list_diff(xa, xb, box, same, tol=BOX_NEAR):
    """쪽과 상자로 짝지어 (a 수, b 수, 짝, 같은 것)."""
    by_page = {}
    for item in xb:
        by_page.setdefault(item["page"], []).append(item)
    used, paired, equal = set(), 0, 0
    for item in xa:
        for other in by_page.get(item["page"], ()):
            if id(other) in used or not _near(box(item), box(other), tol):
                continue
            used.add(id(other))
            paired += 1
            equal += bool(same(item, other))
            break
    return {"a": len(xa), "b": len(xb), f"paired_by_bbox_{tol}": paired, "same": equal}


def outputs_diff(dir_a, dir_b):
    """두 산출 디렉터리(문서 하나)의 차이."""
    dir_a, dir_b = Path(dir_a), Path(dir_b)
    load = lambda d, name: json.loads((d / name).read_text(encoding="utf-8"))
    names = ("document.json", "char-places.json", "chart-labels.json", "chart-candidates.json", "places.json",
             "number-words.json", "tags.json", "page-lines.json")
    out = {"sha256": {n: {"a": _sha(dir_a / n), "b": _sha(dir_b / n)} for n in names if (dir_a / n).is_file() and (dir_b / n).is_file()}}
    for n, v in out["sha256"].items():
        v["same"] = v["a"] == v["b"]
    out["document"] = _document_diff(load(dir_a, "document.json"), load(dir_b, "document.json"))
    out["char_places"] = _places_diff(load(dir_a, "char-places.json"), load(dir_b, "char-places.json"))
    la, lb = load(dir_a, "chart-labels.json"), load(dir_b, "chart-labels.json")
    strip = lambda d, keys: {k: v for k, v in d.items() if k not in keys}
    out["chart_labels"] = {
        "charts": _list_diff(la["charts"], lb["charts"], lambda c: c["bbox"], lambda x, y: strip(x, ("id",)) == strip(y, ("id",)), 1.0),
        "words": _list_diff([{**w, "page": int(w["chart"].split("-")[0][1:])} for w in la["words"]],
                            [{**w, "page": int(w["chart"].split("-")[0][1:])} for w in lb["words"]], lambda w: w["bbox"],
                            lambda x, y: strip(x, ("container", "start", "end", "chart", "bbox")) == strip(y, ("container", "start", "end", "chart", "bbox"))),
        "values": {"a": sum(1 for w in la["words"] if w["role"] == "value"), "b": sum(1 for w in lb["words"] if w["role"] == "value")},
        "unlinked": {"a": len(la["unlinked"]), "b": len(lb["unlinked"])},
    }
    ca, cb = load(dir_a, "chart-candidates.json"), load(dir_b, "chart-candidates.json")
    out["chart_candidates"] = {
        "pages_same": sum(1 for x, y in zip(ca["pages"], cb["pages"]) if x == y), "pages": len(ca["pages"]),
        **{d: _list_diff([c for c in ca["candidates"] if c["definition"] == d], [c for c in cb["candidates"] if c["definition"] == d],
                         lambda c: c["bbox"], lambda x, y: (x["marks"], x["loose_numbers"]) == (y["marks"], y["loose_numbers"]), 1.0)
           for d in ("marks", "numbers", "both")},
    }
    na, nb = load(dir_a, "number-words.json"), load(dir_b, "number-words.json")
    out["number_words"] = {"mark_pages": {"a": len(na["mark_pages"]), "b": len(nb["mark_pages"]), "same": len(set(na["mark_pages"]) & set(nb["mark_pages"]))},
                           "words": _list_diff(na["words"], nb["words"], lambda w: w["bbox"], lambda x, y: (x["text"], x["place"], x["year"]) == (y["text"], y["place"], y["year"]))}
    out["tags"] = _list_diff(load(dir_a, "tags.json"), load(dir_b, "tags.json"), lambda t: t["bbox"], lambda x, y: True)
    pla, plb = load(dir_a, "page-lines.json"), load(dir_b, "page-lines.json")
    texts = lambda p: Counter(seg["text"] for seg in p["lines"])
    out["page_lines"] = {"pages": {"a": len(pla), "b": len(plb), "same": sum(1 for k in pla if pla[k] == plb.get(k))},
                         "lines": {"a": sum(len(p["lines"]) for p in pla.values()), "b": sum(len(p["lines"]) for p in plb.values()),
                                   "same_text_on_same_page": sum(sum((texts(pla[k]) & texts(plb[k])).values()) for k in pla if k in plb)}}
    for side, d in (("a", dir_a), ("b", dir_b)):
        if (d / "times.json").is_file():
            out.setdefault("seconds", {})[side] = load(d, "times.json")
    return out


def attribute(raw, outputs):
    """산출이 다른 쪽마다 그 쪽의 원재료 차이(까닭의 갈래)를 붙인다. 원재료 차이가 없으면 `rounding` 또는 `other`."""
    raw_causes = {p["page"]: p["causes"] for p in raw["pages"]}
    state_b, state_t = outputs["document"]["page_state"]["blocks"], outputs["document"]["page_state"]["tables"]
    rows, tally = [], Counter()
    for pno in sorted(set(state_b) | set(state_t)):
        sb, st = state_b.get(pno, "same"), state_t.get(pno, "same")
        if sb == "same" and st == "same":
            continue
        causes = list(raw_causes.get(pno, []))
        if not causes:
            causes = ["rounding"] if sb in ("same", "near") and st in ("same", "cells_same") else ["other"]
        rows.append({"page": pno, "blocks": sb, "tables": st, "causes": causes})
        tally.update(causes)
    return {"pages_differing": len(rows), "by_cause": dict(tally), "pages": rows}


def render_markdown(name, raw, outputs, attribution):
    t = raw["totals"]
    L = [f"### {name}", ""]
    L.append("산출 파일의 sha256 이 같은가: " + ", ".join(f"`{n}` {'같음' if v['same'] else '다름'}" for n, v in outputs["sha256"].items()))
    L += ["", "원재료 (a = pymupdf, b = pdfium)", "", "| 항목 | a | b | 같은 것 | 비고 |", "|---|---:|---:|---:|---|"]
    c = t["chars"]
    L.append(f"| 글자(공백 아님) | {c['a']} | {c['b']} | {c['paired']} | 같은 글자·같은 자리. 상자가 0.001pt 안 {c['box_within_0.001']}, 0.05pt 안 {c['box_within_0.05']} |")
    for key, label in (("lines", "줄"), ("spans", "스팬"), ("words", "낱말")):
        v = t[key]
        L.append(f"| {label} | {v['a']} | {v['b']} | {v['same']} | |")
    d = t["drawings"]
    L.append(f"| 그리기 항목(경로) | {d['a']} | {d['b']} | {d['paired']} | 상자 {RAW_NEAR}pt 안에서 짝. 갈래 같음 {d['kinds_same']}, 채움·획 유무 같음 {d['paint_same']}, 색 같음 {d['colors_same']}, 굵기 같음 {d['width_same']} |")
    L.append(f"| 그림 상자 | {t['images']['a']} | {t['images']['b']} | {t['images']['same']} | |")
    L.append(f"| 쪽 크기 | {t['pages']['n']} | {t['pages']['n']} | {t['pages']['size_same']} | |")
    L.append(f"| 책갈피 | {raw['toc']['a']} | {raw['toc']['b']} | {'전부' if raw['toc']['same'] else '다름'} | |")
    doc = outputs["document"]
    L += ["", "`document.json`", "", "| 항목 | a | b | 같은 것 | 비고 |", "|---|---:|---:|---:|---|"]
    p = doc["pages"]
    L.append(f"| 쪽 메타 | {p['a']} | {p['b']} | {p['same']} | text_layer {p['text_layer']}, columns {p['columns']}, notes {p['notes']}, char_count {p['char_count']}, image_coverage {p['image_coverage']} |")
    L.append(f"| 섹션 | {doc['sections']['a']} | {doc['sections']['b']} | {doc['sections']['same']} | |")
    blk = doc["blocks"]
    L.append(f"| 블록 | {blk['a']} | {blk['b']} | {blk['same']} | 글·갈래 같고 상자 {BOX_NEAR}pt 안 {blk[f'near_{BOX_NEAR}']}, 제어 글자를 빼고 보면 {blk[f'near_{BOX_NEAR}_ignoring_control_chars']}, 그 가운데 차례 같음 {blk['order_same_among_near']} |")
    tb = doc["tables"]
    L.append(f"| 표 | {tb['a']} | {tb['b']} | {tb['same']} | 상자로 짝 {tb['paired_by_bbox_1.0']}, 행·열 수 같음 {tb['shape_same']}, header {tb['header_same']}, method {tb['method_same']}, status {tb['status_same']} |")
    L.append(f"| 표의 칸 | {tb['cells']} | | {tb['cells_same']} | 제어 글자를 빼고 보면 {tb['cells_same_ignoring_control_chars']} |")
    cp = outputs["char_places"]
    L += ["", "`char-places.json`", "", f"- 그릇 a {cp['containers']['a']} / b {cp['containers']['b']}, 같은 ID {cp['containers']['same_id']}, 통째로 같음 {cp['containers']['same']}, 글자 수까지 같아 견줄 수 있는 것 {cp['containers']['same_id_and_glyph_count']}",
          f"- unlinked a {cp['unlinked']['a']} / b {cp['unlinked']['b']}",
          f"- 글자 x 의 차: {cp['glyph_x_delta']}", f"- 줄 y 의 차: {cp['line_y_delta']}"]
    cl, cc = outputs["chart_labels"], outputs["chart_candidates"]
    L += ["", "도표 쪽 산출", "", "| 항목 | a | b | 짝 | 같은 것 |", "|---|---:|---:|---:|---:|"]
    row = lambda label, v: f"| {label} | {v['a']} | {v['b']} | {next((v[k] for k in v if k.startswith('paired')), '')} | {v['same']} |"
    L.append(row("chart-labels 도표", cl["charts"]))
    L.append(row("chart-labels 낱말(라벨까지)", cl["words"]))
    L.append(f"| chart-labels 값 낱말 | {cl['values']['a']} | {cl['values']['b']} | | |")
    for dname in ("marks", "numbers", "both"):
        L.append(row(f"chart-candidates `{dname}`", cc[dname]))
    L.append(f"| chart-candidates 쪽 요약 | {cc['pages']} | {cc['pages']} | | {cc['pages_same']} |")
    L.append(row("number-words 숫자 낱말(글·자리 갈래)", outputs["number_words"]["words"]))
    L.append(row("tags 번호표 대상", outputs["tags"]))
    pl = outputs["page_lines"]
    L.append(f"| page-lines 쪽 | {pl['pages']['a']} | {pl['pages']['b']} | | {pl['pages']['same']} |")
    L.append(f"| page-lines 줄 토막(글) | {pl['lines']['a']} | {pl['lines']['b']} | | {pl['lines']['same_text_on_same_page']} |")
    L += ["", f"`document.json` 이 다른 쪽 {attribution['pages_differing']} / {raw['page_count']}. 그 쪽들의 원재료 차이(한 쪽에 여럿일 수 있다): "
          + (", ".join(f"{CAUSE_NAMES[k]} {v}쪽" for k, v in sorted(attribution["by_cause"].items(), key=lambda kv: -kv[1])) or "없음")]
    L += ["", "원재료가 다른 쪽 수(문서 전체): " + ", ".join(f"{CAUSE_NAMES[k]} {len(v)}" for k, v in raw["cause_pages"].items())]
    L.append(f"a 에만 있는 글자(많은 순): {raw['chars_only_a']}")
    L.append(f"b 에만 있는 글자(많은 순): {raw['chars_only_b']}")
    if "seconds" in outputs:
        L.append(f"시간(초): {outputs['seconds']}")
    return "\n".join(L) + "\n"


def run(pdf_path, dir_a, dir_b, out_dir, engine_a="pymupdf", engine_b="pdfium"):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_diff(pdf_path, engine_a, engine_b)
    outputs = outputs_diff(dir_a, dir_b)
    attribution = attribute(raw, outputs)
    name = Path(pdf_path).name
    (out_dir / "engine-diff.json").write_text(json.dumps({"file": name, "raw": raw, "outputs": outputs, "attribution": attribution}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = render_markdown(name, raw, outputs, attribution)
    (out_dir / "engine-diff.md").write_text(md, encoding="utf-8")
    return raw, outputs, attribution
