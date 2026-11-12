"""목차 ↔ 본문 범위(T-a). AI 0. 규칙은 sam 만 보고 짰고 cos·kb 로 채점한다(보고 T-a).

1. 목차 쪽: 앞 10쪽에서 「글 + 끝 쪽 번호」 꼴(표 칸 짝·글 줄)이 가장 몰린 쪽. 「목차/Contents」 제목이 있으면 더한다.
2. 항목: 표는 칸을 (글, 번호) 짝으로 읽고, 번호 없는 글 칸은 [..] 이면 묶음 머리, 아니면 바로 위 항목의 이어짐.
   목차 쪽의 표 밖 짧은 글 블록 중 항목보다 큰 글자는 대(장) 항목(쪽 번호 없음). 번호 없는 목차는 글자 크기로 층을 가른다.
3. 찍힌 쪽 → PDF 쪽: 꼬리말 띠(region footer)의 쪽 번호를 읽어 쪽마다 사상을 만들고(펼침면 = 번호 둘), 없으면 어긋남 최빈값.
4. 항목 → 제목 블록: 그 PDF 쪽 ±1 안 큰 글자 블록과 rapidfuzz 비율(국문 항목은 블록의 한글 부분과) + 글자 크기 순위.
   번호 없는 항목은 앞 항목 시작 뒤에서 처음 맞는 쪽 맨 위 제목(차례를 거스르지 않음).
5. 범위: 시작 블록 ~ 다음 항목 시작 블록 앞.

명령: python -m trlab.toc <키> <ko|en> [--gold] → OUT/toc/<키>-<lang>.json(+ 정답이 있으면 점수)
"""

import json
import re
import statistics
import sys
from collections import Counter

from rapidfuzz import fuzz

from trlab.align import body
from trlab.common import GOLD, OUT, Block, is_hangul, load, split_bilingual

NUM = re.compile(r"^\d{1,3}$")
TAIL = re.compile(r"^(.*\S)\s+(\d{1,3})$")
TOC_WORD = re.compile(r"^(목차|contents|table of contents)$", re.I)


# ---------- 1·2 목차 쪽과 항목 ----------
def _pairs_from_rows(rows):
    """표 행들 → [(글, 번호|None, 열)]. 한 행에 짝이 여럿(나란한 단)일 수 있다."""
    out = []
    for r in rows:
        cells = [c.strip() for c in r]
        i = 0
        while i < len(cells):
            c = cells[i]
            if not c or NUM.match(c):
                i += 1
                continue
            m = TAIL.match(c)
            if m and not NUM.match(m.group(1)):
                out.append((m.group(1), int(m.group(2)), i))
                i += 1
                continue
            num, col = None, i
            if i + 1 < len(cells) and NUM.match(cells[i + 1] or "x"):
                num = int(cells[i + 1])
                i += 1
            out.append((c, num, col))
            i += 1
    return out


def toc_score(page_blocks):
    s = 0
    for b in page_blocks:
        if b.kind == "table":
            s += sum(1 for _, n, _ in _pairs_from_rows(b.rows) if n is not None)
        else:
            s += len([ln for ln in b.text.split("\n") if TAIL.match(ln.strip())])
            if TOC_WORD.match(b.text.strip()):
                s += 5
    return s


def find_toc_pages(blocks, first=10):
    bod = [b for b in body(blocks) if b.page <= first]
    by = {}
    for b in bod:
        by.setdefault(b.page, []).append(b)
    scored = sorted(((toc_score(v), p) for p, v in by.items()), reverse=True)
    if not scored or scored[0][0] < 5:
        return []
    best = scored[0][0]
    return sorted(p for s, p in scored if s >= max(5, best * 0.6))


def extract_entries(blocks, toc_pages):
    """목차 항목 [{text, printed, level, group}] 목차 차례대로."""
    ents = []
    # 머리글·꼬리말 띠의 탐색 글(장 이름) — 표 안 번호 없는 칸이 이것과 같으면 이어짐이 아니라 장 머리다
    nav = {b.text.strip().lower() for b in blocks if b.region in ("header", "footer") and len(b.text) < 40}
    for p in toc_pages:
        pb = [b for b in body(blocks) if b.page == p]
        sizes = [b.font_size for b in pb if b.kind != "table" and b.font_size]
        for b in sorted(pb, key=lambda b: (b.bbox[0] // 160, b.bbox[1])):  # 단(열) 우선 읽기
            if b.kind == "table":
                group, last_col = "", {}
                for text, num, col in _pairs_from_rows(b.rows):
                    if re.match(r"^\[.*\]$", text):
                        group = text
                        continue
                    if num is None and col in last_col and text.lower() not in nav:
                        last_col[col]["text"] += " " + text  # 이어짐
                        continue
                    e = {"text": text, "printed": num, "level": 2 if num is not None else 0, "group": group, "src": b.id, "in_table": True,
                         "fs": b.font_size}
                    ents.append(e)
                    last_col[col] = e
            else:
                t = b.text.strip()
                if TOC_WORD.match(t) or len(t) > 60 or NUM.match(t):
                    continue
                m = TAIL.match(t)
                side = [x for x in pb if x.kind != "table" and NUM.match(x.text.strip()) and abs(x.cy - b.cy) < 0.012
                        and 0 < x.cx - b.cx < 0.3]
                if not m and side:  # 「제목」 블록과 「61」 블록이 한 줄에 따로
                    ents.append({"text": t, "printed": int(side[0].text.strip()), "level": 2, "group": "", "src": b.id})
                elif m:
                    ents.append({"text": m.group(1), "printed": int(m.group(2)), "level": 2, "group": "", "src": b.id})
                else:
                    ents.append({"text": t, "printed": None, "level": 0, "group": "", "src": b.id, "fs": b.font_size})
    # 번호 없는 글 블록의 층: 같은 쪽 번호 있는 항목이 있으면 대(1). 모두 번호가 없으면 글자 크기로 가른다.
    loose = [e for e in ents if e["level"] == 0]
    if loose:
        if any(e["printed"] for e in ents):
            # 대 항목은 같은 글자 크기로 줄지어 있다 — 최빈 크기가 아닌 것(표지 제목·보고서 이름)은 버린다
            fmode = Counter(round(e["fs"], 1) for e in loose if e["src"].count("-b")).most_common(1)[0][0]
            ents = [e for e in ents if e["level"] != 0 or abs(e["fs"] - fmode) < 0.3 or e.get("in_table")]
            for e in ents:
                if e["level"] == 0:
                    e["level"] = 1
        else:
            big = max(e["fs"] for e in loose)
            for e in loose:
                e["level"] = 1 if e["fs"] >= big - 0.5 else 2
    # 표의 단(열) 읽기 차례가 목차 차례와 어긋날 수 있다 — 번호 있는 것은 번호 차례로 끼워 넣는다
    return ents


# ---------- 3 찍힌 쪽 → PDF 쪽 ----------
def page_map(blocks):
    """{찍힌 번호: PDF 쪽}, 어긋남 최빈값."""
    m, offs = {}, []
    for b in blocks:
        if b.region != "footer":
            continue
        for n in re.findall(r"(?<!\d)\d{1,3}(?!\d)", b.text.strip()) if len(b.text.strip()) <= 9 else []:
            n = int(n)
            if 0 < n < 1000 and abs(n - b.page) < 30:
                m.setdefault(n, b.page)
                offs.append(b.page - n)
    off = Counter(offs).most_common(1)[0][0] if offs else 0
    return m, off


def to_pdf(printed, pmap, off, npages):
    if printed is None:
        return None
    p = pmap.get(printed, printed + off)
    return max(1, min(npages, p))


# ---------- 4 항목 → 제목 블록 ----------
def _lang_part(item_text, block_text):
    lat, ko = split_bilingual(block_text)
    if is_hangul(item_text):
        return ko or block_text
    return lat or block_text


def match_score(item_text, b: Block, size_rank):
    t = re.sub(r"^\[[^\]]*\]\s*", "", item_text)
    bt = _lang_part(t, b.text)
    r = max(fuzz.ratio(t.lower(), bt.lower()), fuzz.partial_ratio(t.lower(), bt.lower()) * 0.9) / 100
    return r * 0.8 + size_rank * 0.2


def _size_rank(cands):
    fs = sorted({round(b.font_size, 1) for b in cands}, reverse=True)
    return {b.id: 1 - fs.index(round(b.font_size, 1)) / max(1, len(fs)) for b in cands}


def heading_cands(bod, pages):
    cs = [b for b in bod if b.page in pages and b.kind != "table" and len(b.text) <= 120]
    if not cs:
        return []
    med = statistics.median(b.font_size for b in bod if b.kind != "table")
    return [b for b in cs if b.font_size >= med * 1.15 or b.kind == "heading"]


def locate(blocks, ents, toc_pages):
    bod = body(blocks)
    npages = max(b.page for b in blocks)
    pmap, off = page_map(blocks)
    after = max(toc_pages) if toc_pages else 0
    prev_page = after
    numbered = any(e["printed"] for e in ents)
    out = []
    for e in ents:
        guess = to_pdf(e["printed"], pmap, off, npages)
        if guess:
            pages = {guess - 1, guess, guess + 1}
        elif numbered:
            pages = set(range(after + 1, npages + 1))  # 번호 있는 목차의 대 항목: 차례에 매이지 않고 책 전체에서
        else:
            pages = set(range(prev_page + (0 if out else 1), npages + 1))
        cands = heading_cands(bod, pages)
        rank = _size_rank(cands)
        best, bs = None, 0.0
        for b in cands:
            s = match_score(e["text"], b, rank[b.id])
            if guess:
                s -= 0.05 * abs(b.page - guess)
            elif numbered:
                s += 0.1 * (b.font_size >= max(x.font_size for x in cands) * 0.8) - 0.0005 * b.page
            else:
                s -= 0.002 * (b.page - prev_page)  # 번호 없으면 앞쪽을 조금 더
            if s > bs:
                best, bs = b, s
        hit = best if bs >= 0.55 else None
        start_page = hit.page if hit else guess
        r = dict(e, pdf_guess=guess, start_block=hit.id if hit else None, start_page=start_page, score=round(bs, 3))
        if start_page:
            prev_page = start_page
        out.append(r)
    # 5 범위: 다음 항목 시작 앞까지
    starts = sorted({r["start_page"] for r in out if r["start_page"]})
    for r in out:
        if not r["start_page"]:
            r["end_page"] = None
            continue
        nx = [s for s in starts if s > r["start_page"]]
        r["end_page"] = nx[0] - 1 if nx else npages
    return out, off


def mid_headings(blocks, start, end):
    """범위 안 중제목 후보: 본문 최빈 글자보다 뚜렷이 크고 짧은 블록(숫자로 시작 X)."""
    bod = [b for b in body(blocks) if start <= b.page <= end and b.kind != "table"]
    if not bod:
        return []
    allbod = [b for b in body(blocks) if b.kind != "table"]
    mode = Counter(round(b.font_size, 1) for b in allbod if len(b.text) > 80).most_common(1)[0][0]
    return [b for b in bod if b.font_size >= mode * 1.12 and len(b.text) <= 70 and not re.match(r"^[\d\-(+~·‧•*]", b.text.strip())
            and not re.search(r"\d[\d,.]*\s*(%|명|건|개|시간|cases|hours|individuals)", b.text)]


def run(key, lang):
    blocks = load(key, lang)
    tp = find_toc_pages(blocks)
    ents = extract_entries(blocks, tp)
    res, off = locate(blocks, ents, tp)
    return {"book": key, "lang": lang, "toc_pages": tp, "offset": off, "items": res}, blocks


# ---------- 채점 ----------
def score(pred, gold, blocks):
    gi = gold["items"]
    P = pred["items"]
    used, rows = set(), []
    for g in gi:
        gt = re.sub(r"^\[[^\]]*\]\s*", "", g["text"]).lower()
        best, bs = None, 0
        for i, p in enumerate(P):
            if i in used:
                continue
            s = fuzz.ratio(gt, re.sub(r"^\[[^\]]*\]\s*", "", p["text"]).lower())
            if s > bs:
                best, bs = i, s
        if best is not None and bs >= 80:
            used.add(best)
            p = P[best]
            rows.append({"n": g["n"], "found": True, "block_ok": p["start_block"] in [g["start_block"]] + g["alt_blocks"],
                         "start_ok": p["start_page"] == g["start_page"], "end_ok": p["end_page"] == g["end_page"],
                         "start_pm1": p["start_page"] is not None and abs(p["start_page"] - g["start_page"]) <= 1,
                         "end_pm1": p["end_page"] is not None and abs(p["end_page"] - g["end_page"]) <= 1,
                         "pred": {k: p[k] for k in ("text", "start_block", "start_page", "end_page")}})
        else:
            rows.append({"n": g["n"], "found": False, "block_ok": False, "start_ok": False, "end_ok": False,
                         "start_pm1": False, "end_pm1": False})
    n = len(rows)
    pct = lambda k: round(100 * sum(r[k] for r in rows) / n, 1)
    # 중제목
    tp = fp = fn = 0
    nonidx = []
    for g in gi:
        if "mid" not in g:
            continue
        found = [b.id for b in mid_headings(blocks, g["start_page"], g["end_page"])]
        found = [f for f in found if f not in [g["start_block"]] + g["alt_blocks"]]
        gold_ids = {m["block"] for m in g["mid"] if m["block"]}
        tp += len(gold_ids & set(found))
        fp += len(set(found) - gold_ids)
        fn += len(g["mid"]) - len(gold_ids & set(found))
        nonidx.append(len(g["mid"]))
    return {"items": n, "toc_found": bool(pred["toc_pages"]) and bool(set(pred["toc_pages"]) & set(gold["toc_pages"])),
            "extracted_pct": pct("found"), "block_ok_pct": pct("block_ok"), "start_ok_pct": pct("start_ok"),
            "end_ok_pct": pct("end_ok"), "start_pm1_pct": pct("start_pm1"), "end_pm1_pct": pct("end_pm1"),
            "mid": {"recall": round(tp / max(1, tp + fn), 3), "precision": round(tp / max(1, tp + fp), 3),
                    "tp": tp, "fp": fp, "fn": fn, "per_item": round(statistics.mean(nonidx), 1) if nonidx else None},
            "rows": rows}


def main(argv):
    key, lang = argv[0], argv[1]
    pred, blocks = run(key, lang)
    (OUT / "toc").mkdir(parents=True, exist_ok=True)
    gp = GOLD / "toc" / f"{key}-{lang}.json"
    if "--gold" in argv and gp.exists():
        pred["score"] = score(pred, json.load(open(gp)), blocks)
        s = {k: v for k, v in pred["score"].items() if k != "rows"}
        print(key, lang, json.dumps(s, ensure_ascii=False))
    json.dump(pred, open(OUT / "toc" / f"{key}-{lang}.json", "w"), ensure_ascii=False, indent=1)
    print(key, lang, "toc", pred["toc_pages"], "offset", pred["offset"], "entries", len(pred["items"]))


if __name__ == "__main__":
    main(sys.argv[1:])
