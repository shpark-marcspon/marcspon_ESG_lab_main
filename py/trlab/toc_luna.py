"""T-a luna 덧댐(조건부: cos·kb 항목→제목 맞음 < 90% 일 때). 목차 쪽 글 + 후보 제목 목록(블록 id·쪽·글)을 주고
항목마다 시작 블록 하나를 고르게 한다. 같은 정답으로 「코드만」과 나란히 채점한다.

명령: uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.toc_luna <키> <lang> [--dry-run]
키는 OPENAI_API_KEY(마크스폰 키)에서만. 호출마다 OUT/calls.jsonl 한 줄(semgraph.llm).
"""

import json
import statistics
import sys

from semgraph import llm
from trlab.align import body
from trlab.common import GOLD, OUT, load
from trlab.toc import find_toc_pages, score

MODEL = "gpt-6-luna"
INSTR = """You map a sustainability report's table of contents to where each section starts in the parsed body.
You get (1) the text of the table-of-contents page(s) and (2) candidate title blocks: id | pdf page | text.
List every TOC entry in TOC order, including chapter-level entries that have no page number (level 1) and
section entries (level 2). For each entry pick the ONE candidate block where that section starts:
- printed page numbers usually equal pdf pages but may be offset; prefer the first page of a run of repeated running titles;
- a chapter/section may start on a divider page whose only text is a large title or a summary table — pick a block on that page;
- a section's lead-in page (e.g. key figures) may come before the page carrying its title;
- if nothing fits, give block_id "".
Copy entry text as printed in the TOC (without the page number)."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["text", "level", "printed_page", "block_id"],
                               "properties": {"text": {"type": "string"}, "level": {"type": "integer"},
                                              "printed_page": {"type": ["integer", "null"]},
                                              "block_id": {"type": "string"}}}}}}


def candidates(blocks, per_page=3):
    bod = body(blocks)
    med = statistics.median(b.font_size for b in bod if b.kind != "table")
    out = []
    for p in sorted({b.page for b in bod}):
        pb = [b for b in bod if b.page == p]
        big = sorted([b for b in pb if b.kind != "table" and (b.font_size >= med * 1.15 or b.kind == "heading")
                      and len(b.text) <= 120], key=lambda b: -b.font_size)[:per_page]
        if not big:  # 간지: 표로 뽑힌 큰 글
            big = pb[:1]
        for b in sorted(big, key=lambda b: b.order):
            out.append(f"{b.id} | {p} | {b.text[:70]}".replace("\n", " "))
    return out


def prompt(key, lang):
    blocks = load(key, lang)
    tp = find_toc_pages(blocks) or [2, 3, 4, 5]
    toc = "\n".join(b.text for b in blocks if b.page in tp and b.region == "body")
    text = f"## TOC page(s) {tp}\n{toc}\n\n## Candidate blocks (id | pdf page | text)\n" + "\n".join(candidates(blocks))
    return blocks, tp, text


def to_pred(key, lang, blocks, tp, items):
    ids = {b.id: b for b in blocks}
    npages = max(b.page for b in blocks)
    res = []
    for it in items:
        b = ids.get(it["block_id"])
        res.append({"text": it["text"], "printed": it["printed_page"], "level": it["level"],
                    "start_block": b.id if b else None, "start_page": b.page if b else None})
    starts = sorted({r["start_page"] for r in res if r["start_page"]})
    for r in res:
        nx = [s for s in starts if r["start_page"] and s > r["start_page"]]
        r["end_page"] = (nx[0] - 1 if nx else npages) if r["start_page"] else None
    return {"book": key, "lang": lang, "toc_pages": tp, "items": res}


def main(argv):
    key, lang = argv[0], argv[1]
    if "--combine" in argv:
        print(key, lang, "code+luna", json.dumps(combine(key, lang)))
        return
    blocks, tp, text = prompt(key, lang)
    est_in = int(len(text) / 2.2)  # 한·영 섞인 글 — 위쪽 어림
    est_out = 60 * 40
    print(key, lang, "dry-run 예상 입력", est_in, "출력", est_out, "USD", round(llm.cost(MODEL, est_in, 0, est_out), 5))
    if "--dry-run" in argv:
        return
    out = OUT / "toc"
    out.mkdir(parents=True, exist_ok=True)
    raw, row = llm.respond(MODEL, INSTR, text, SCHEMA, OUT, f"TRX1/T-a/{key}-{lang}", effort="low")
    pred = to_pred(key, lang, blocks, tp, json.loads(raw)["items"])
    pred["call"] = row
    gp = GOLD / "toc" / f"{key}-{lang}.json"
    pred["score"] = score(pred, json.load(open(gp)), blocks)
    json.dump(pred, open(out / f"{key}-{lang}-luna.json", "w"), ensure_ascii=False, indent=1)
    s = {k: v for k, v in pred["score"].items() if k not in ("rows", "mid")}
    print(key, lang, "luna", json.dumps(s), "USD", round(row["costUsd"], 5))



def combine(key, lang, conf=0.8):
    """코드 + luna: luna 항목 목록을 바탕으로, 같은 글의 코드 항목이 점수 conf 이상으로 블록을 잡았으면 코드 것을 쓴다."""
    from rapidfuzz import fuzz
    code = json.load(open(OUT / "toc" / f"{key}-{lang}.json"))
    lun = json.load(open(OUT / "toc" / f"{key}-{lang}-luna.json"))
    blocks = load(key, lang)
    ids = {b.id: b for b in blocks}
    items = []
    for it in lun["items"]:
        best = max(code["items"], key=lambda c: fuzz.ratio(c["text"].lower(), it["text"].lower()), default=None)
        if best and fuzz.ratio(best["text"].lower(), it["text"].lower()) >= 85 and best.get("start_block") \
                and best.get("score", 0) >= conf:
            items.append({"text": it["text"], "level": it["level"], "printed_page": it["printed"],
                          "block_id": best["start_block"]})
        else:
            items.append({"text": it["text"], "level": it["level"], "printed_page": it["printed"],
                          "block_id": it["start_block"] or ""})
    pred = to_pred(key, lang, blocks, lun["toc_pages"], items)
    pred["score"] = score(pred, json.load(open(GOLD / "toc" / f"{key}-{lang}.json")), blocks)
    json.dump(pred, open(OUT / "toc" / f"{key}-{lang}-combined.json", "w"), ensure_ascii=False, indent=1)
    return {k: v for k, v in pred["score"].items() if k not in ("rows", "mid")}


if __name__ == "__main__":
    main(sys.argv[1:])
