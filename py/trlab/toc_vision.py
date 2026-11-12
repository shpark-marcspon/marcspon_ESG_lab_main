"""TRX2 G1 — 목차 쪽을 코드로 찾고, 150dpi 렌더 이미지를 gpt-6.1-sol 한 호출로 읽어 목차 항목을 뽑는다.

쪽 찾기(AI 0): 앞 10쪽에서 (본문 블록 글이 목차|contents|table of contents) 또는 toc.toc_score ≥ 5 인 쪽.
가장 높은 쪽에서 앞뒤로 이어진 쪽 중 점수 ≥ 그 쪽의 0.6 이거나 목차 낱말이 있는 쪽을 붙인다(최대 3쪽).
렌더는 esg-app/services/parser 의 docparse CLI 로 부르기만 한다(esg-app 0줄).
지시문은 sam ko·en 으로만 다듬었고(PROMPT_V) 그 판을 얼려 cos·kb·nb 에 한 번씩 돌린다.

명령: uv run --frozen --group scope3colab --with openai==3.26.0 python -m trlab.toc_vision <키> <lang> [--dry-run|--score]
     → OUT2/g1/<키>-<lang>.json (응답 원문·항목·호출 줄·정답 있으면 읽기 정확)
키는 OPENAI_API_KEY(마크스폰 키)에서만. 호출마다 OUT2/calls.jsonl 한 줄(semgraph.llm).
"""

import base64
import json
import re
import subprocess
import sys

from rapidfuzz import fuzz

from trlab import glyphs
from trlab.align import body
from trlab.cands import gold_path
from trlab.glyphs import OUT2, load_blocks, pdf_path
from trlab.toc import TOC_WORD, extract_entries, find_toc_pages, toc_score

MODEL = "gpt-6.1-sol"
CAP = 0.4  # G1 sol 자리 상한(USD)
PROMPT_V = "v1"
INSTR = """You read the table-of-contents page image(s) of one sustainability report.
Return every entry in reading order exactly as printed (the page number removed from the text):
- level 1 = a chapter/part heading (usually without a page number); level 2 = an entry under it;
- page numbers may sit in a separate column — pair each number with the entry on the same line;
- a chapter heading or group heading without a page number is still an entry (printed_page null);
- drop tags printed next to an entry (standard codes such as ESRS-E1, GRI 2-1) from its text;
- if an entry shows two page numbers, use the first;
- group = the level-1 heading the entry belongs to ("" for level 1 entries)."""
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "items": {"type": "object", "additionalProperties": False,
                               "required": ["text", "printed_page", "level", "group"],
                               "properties": {"text": {"type": "string"},
                                              "printed_page": {"type": ["integer", "null"]},
                                              "level": {"type": "integer", "enum": [1, 2]},
                                              "group": {"type": "string"}}}}}}
EST_PAGE_IN = 3500
EST_OUT = 3000


# ---------- 쪽 찾기 ----------
def has_word(pb):
    return any(TOC_WORD.match(b.text.strip()) for b in pb if b.kind != "table")


def find_pages(blocks, first=10, most=3):
    by = {}
    for b in body(blocks):
        if b.page <= first:
            by.setdefault(b.page, []).append(b)
    sc = {p: toc_score(v) for p, v in by.items()}
    word = {p for p, v in by.items() if has_word(v)}
    ok = [p for p in by if p in word or sc[p] >= 5]
    if not ok:
        return []
    best = max(ok, key=lambda p: (sc[p], p in word, -p))
    th = sc[best] * 0.6
    keep = lambda p: p in by and (p in word or (sc[p] >= th and sc[p] >= 1))
    pages, lo, hi = [best], best - 1, best + 1
    while len(pages) < most:
        if keep(lo):
            pages.insert(0, lo)
            lo -= 1
        elif keep(hi):
            pages.append(hi)
            hi += 1
        else:
            break
    return sorted(pages)[:most]


# ---------- 렌더·메시지 ----------
def render(key, lang, pages, dpi=150):
    out = OUT2 / "render" / f"{key}-{lang}"
    need = [p for p in pages if not (out / f"p{p:04d}.png").exists()]
    if need:
        cwd = glyphs.ROOT / "esg-app" / "services" / "parser"
        subprocess.run(["uv", "run", "--frozen", "python", "-m", "docparse", "render", str(pdf_path(key, lang)),
                        "--out", str(out), "--pages", ",".join(map(str, need)), "--dpi", str(dpi)],
                       cwd=cwd, check=True, capture_output=True)
    return [out / f"p{p:04d}.png" for p in pages]


def messages(pngs, pages):
    content = [{"type": "input_text", "text": f"Pages {pages[0]}..{pages[-1]} of one report's table of contents"}]
    for p in pngs:
        content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(
            open(p, "rb").read()).decode(), "detail": "high"})
    return [{"role": "user", "content": content}]


def spent(prefix):
    p = OUT2 / "calls.jsonl"
    if not p.exists():
        return 0.0, []
    rows = [json.loads(x) for x in open(p) if x.strip()]
    rows = [r for r in rows if r.get("tag", "").startswith(prefix) and not r.get("dry_run")]
    return sum(r.get("costUsd", 0) for r in rows), rows


def estimate(n_pages):
    """첫 실측이 있으면 그 쪽당 입력·출력 토큰으로, 없으면 발주서 어림(쪽 3.5k + 300, 출력 3k)."""
    from semgraph import llm
    _, rows = spent("TRX2/G1/")
    if rows:
        r = rows[0]
        per = r["inputTokens"] / r["pages"] if r.get("pages") else EST_PAGE_IN
        inp, out = int(per * n_pages), r["outputTokens"]
    else:
        inp, out = n_pages * EST_PAGE_IN + 300, EST_OUT
    return inp, out, llm.cost(MODEL, inp, 0, out)


# ---------- 읽기 정확 ----------
def _clean(t):
    return re.sub(r"^\[[^\]]*\]\s*", "", t or "").strip().lower()


def accuracy(gold, items):
    """정답 항목 중 짝 맞음(fuzz.ratio ≥ 80, toc.score 와 같은 법) · 번호 맞음 · 수준 맞음."""
    used, found, num, lvl = set(), 0, 0, 0
    for g in gold["items"]:
        gt = _clean(g["text"])
        best, bs = None, 0
        for i, p in enumerate(items):
            if i in used:
                continue
            s = fuzz.ratio(gt, _clean(p["text"]))
            if s > bs:
                best, bs = i, s
        if best is None or bs < 80:
            continue
        used.add(best)
        found += 1
        p = items[best]
        num += p.get("printed_page") == g["printed_page"]
        lvl += p.get("level") == g["level"]
    n = len(gold["items"])
    pct = lambda a, d: round(100 * a / d, 1) if d else None
    return {"n": n, "found": found, "found_pct": pct(found, n), "printed_ok": num, "printed_ok_pct": pct(num, found),
            "level_ok": lvl, "level_ok_pct": pct(lvl, found), "items": len(items)}


def code_items(blocks):
    """TRX1 코드 판(toc.extract_entries) 항목을 같은 꼴로."""
    tp = find_toc_pages(blocks)
    ents = extract_entries(blocks, tp)
    return tp, [{"text": e["text"], "printed_page": e["printed"], "level": e["level"] or 2} for e in ents]


def score_file(key, lang, res, blocks=None):
    gp = gold_path(key, lang)
    if not gp.exists():
        return
    gold = json.load(open(gp))
    blocks = blocks or load_blocks(key, lang)
    tp, ci = code_items(blocks)
    res["score"] = {"found_pages": bool(set(res["pages"]) & set(gold["toc_pages"])),
                    "g1": accuracy(gold, res["items"]) if res.get("items") is not None else None,
                    "code": dict(accuracy(gold, ci), pages=tp, found_pages=bool(set(tp) & set(gold["toc_pages"])))}


def run(key, lang, dry=False):
    blocks = load_blocks(key, lang)
    pages = find_pages(blocks)
    res = {"book": key, "lang": lang, "pages": pages, "model": MODEL, "prompt": PROMPT_V}
    if not pages:
        res["status"] = "not_found"
        res["items"] = None
        score_file(key, lang, res, blocks)
        return res
    inp, outp, usd = estimate(len(pages))
    res["estimate"] = {"input": inp, "output": outp, "usd": round(usd, 5)}
    total, _ = spent("TRX2/G1/")
    if dry:
        res["status"] = "dry_run"
        return res
    if total + usd > CAP:
        res["status"] = "over_cap"
        res["spent"] = round(total, 5)
        return res
    from semgraph import llm
    pngs = render(key, lang, pages)
    raw, row = llm.respond(MODEL, INSTR, messages(pngs, pages), SCHEMA, OUT2, f"TRX2/G1/{key}-{lang}", effort="low")
    row["pages"] = len(pages)
    # 쪽 수를 calls.jsonl 줄에도 남긴다(다음 예상용) — 방금 쓴 마지막 줄을 고쳐 쓴다
    p = OUT2 / "calls.jsonl"
    lines = open(p).read().splitlines()
    lines[-1] = json.dumps(row, ensure_ascii=False)
    open(p, "w").write("\n".join(lines) + "\n")
    res.update(status="ok", raw=raw, items=json.loads(raw)["items"], call=row)
    score_file(key, lang, res, blocks)
    return res


def load(key, lang):
    return json.load(open(OUT2 / "g1" / f"{key}-{lang}.json"))


def main(argv):
    key, lang = argv[0], argv[1]
    (OUT2 / "g1").mkdir(parents=True, exist_ok=True)
    path = OUT2 / "g1" / f"{key}-{lang}.json"
    if "--score" in argv:
        res = json.load(open(path))
        score_file(key, lang, res)
    else:
        res = run(key, lang, dry="--dry-run" in argv)
        if res["status"] in ("dry_run", "over_cap"):
            print(key, lang, res["status"], res["pages"], res.get("estimate"), res.get("spent", ""))
            return
    json.dump(res, open(path, "w"), ensure_ascii=False, indent=1)
    print(key, lang, res["status"], "pages", res["pages"], "call", {k: res.get("call", {}).get(k) for k in
                                                                    ("inputTokens", "outputTokens", "costUsd")},
          json.dumps(res.get("score"), ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
