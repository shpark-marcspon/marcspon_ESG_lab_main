"""RAS1 G2 — 찾은 영역을 잘라 비전 LLM 에 보내기(③ luna · ④ sol), 후보별 산출을 정답 항목에 모으기, 채점.

  # 부르기(--dry-run 이면 이미지 크기·예상 금액만). 영역은 regions.py 산출, --pages 로 쪽을 좁힌다
  uv run --no-project --python 3.12 --with openai==3.26.0 --with pypdfium2==5.14.0 --with pillow==12.3.0 python -I run.py call \
      --regions <regions.json> --pdfs <PDF 폴더> --pdf-map key=<pdf>,… --pages "key:1,2;key2:3" --model gpt-6-luna --out <산출> [--dry-run] [--limit 1]
  # 정답 항목마다 raw/<정답 id>.json 짓기 (발주서 §7-1)
  python3 -I run.py collect --gold <정답 폴더>/figures --out <후보 폴더> region --regions <regions.json> --calls-dir <call 산출>
  python3 -I run.py collect --gold <정답 폴더>/figures --out <후보 폴더> docparse --docs key=<document.json>,… [--tables <표 폴더>]
  # 채점: 분류 × 갈래 표와 score.json
  python3 -I run.py score --gold <정답 폴더>/figures --cand <후보 폴더> [--regions <regions.json>]

모델 지시는 `figure_run.PROMPT` 를 바탕으로 표면 격자도 받는다. 표 격자 bbox 는 자른 영역의 0~1 비율로 받아 쪽 pt 로 바꿔
<out>/tables/<key>_p<page>.json(table_score 꼴)에 쓴다. 호출마다 <out>/../calls.jsonl 에 한 줄(vision_run 과 같은 꼴).

지표(정답을 보는 것은 score 뿐):
  낱말 recall — figure_run score 정의 그대로(정규화 낱말이 산출 글을 정규화해 이은 것의 부분 문자열이면 찾음).
  숫자 recall — 숫자 꼴(`-1,234.5%`, 정규화 뒤)을 낱말 단위·중복 수 셈(multiset)으로: |정답 ∩ 산출| / |정답| (§7-2).
     부분 문자열 판(정답 숫자가 산출 글에 들어 있나)은 참고 칸.
  숫자 바뀐 칸(정답 대조) — 산출에 없는 정답 숫자 중 짝 없는 산출 숫자와 편집 거리 1 인 것(오독 꼴, 두 자 이상).
  (참고) 지어낸 낱말 — figure_run 정의, (참고) 정답 밖 숫자 = 산출 숫자 multiset − 정답 숫자 multiset.
     ③④ 는 찾은 영역이 정답 항목보다 넓으면(영역 넓이 ÷ 정답 넓이) 정답 밖 글이 섞여 이 둘이 부푼다 — 참고 칸.
  영역 맞음 — 정답 bbox 넓이의 90% 이상이 찾은 영역 하나 안. 함께 그 영역 넓이 ÷ 정답 넓이.
"""

import argparse
import base64
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pdx"))  # python -I 는 스크립트 폴더를 경로에 넣지 않는다
from figure_run import PRICES, PROMPT, norm  # noqa: E402

SCALE = 2.5  # 180dpi, figure_run.crop_png 와 같음
NUM = re.compile(r"[-+−△▲]?\d[\d]*(?:\.\d+)?%?")  # norm 뒤라 쉼표 없음
INSTR = (
    PROMPT
    + """
- 이미지 안에 데이터 표(행·열 격자)가 보이면 tables 에 표마다 격자 하나도 함께 낸다. 한 행 = 표의 한 줄, 칸은 왼쪽부터.
  병합 칸은 맨 위·맨 왼쪽 칸에만 글을 쓰고 나머지는 "". 숫자는 보이는 그대로(쉼표 포함). bbox 는 그 표의 자리를 이미지 너비·높이의 0~1 비율로.
- 표가 없으면 tables 는 빈 목록. 표 칸 글도 items 에 빠짐없이 넣는다."""
)
GRID = {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "tables"],
    "properties": {
        "items": {"type": "array", "items": {"type": "string"}},
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bbox", "grid"],
                "properties": {"bbox": {"type": "array", "items": {"type": "number"}}, "grid": GRID},
            },
        },
    },
}
EST = {"prompt": 450, "px_per_token": 750, "out": 600}  # dry-run 어림(첫 실호출로 고친다)


def crop(pdf, page, bbox, scale=SCALE):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        img = doc[page - 1].render(scale=scale).to_pil().crop([int(v * scale) for v in bbox])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), img.size
    finally:
        doc.close()


def parse_pages(s):
    out = {}
    for part in filter(None, s.split(";")):
        k, ps = part.split(":")
        out[k] = {int(p) for p in ps.split(",")}
    return out


def cmd_call(a):
    regions = json.loads(Path(a.regions).read_text(encoding="utf-8"))
    files = dict(x.split("=", 1) for x in a.pdf_map.split(",") if x)
    pages = parse_pages(a.pages)
    out = Path(a.out)
    for sub in ("raw", "tables"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    client = None
    if not a.dry_run:
        from openai import OpenAI

        client = OpenAI()
    pin, pcache, pout = PRICES[a.model]
    tot, n = 0.0, 0
    for key, pmap in regions.items():
        for pg_s, regs in pmap.items():
            pg = int(pg_s)
            if key in pages and pg not in pages[key]:
                continue
            if key not in pages and pages:
                continue
            tables_out = []
            for i, r in enumerate(regs):
                name = f"{key}_p{pg}_r{i}"
                if a.limit and n >= a.limit:
                    break
                if not a.dry_run and (out / "raw" / f"{name}.json").is_file() and not a.again:
                    res = json.loads((out / "raw" / f"{name}.json").read_text(encoding="utf-8"))
                    tables_out += to_page(res, r["bbox"])
                    continue
                png, px = crop(Path(a.pdfs) / files[key], pg, r["bbox"])
                est_in = EST["prompt"] + px[0] * px[1] / EST["px_per_token"]
                if a.dry_run:
                    c = (est_in * pin + EST["out"] * pout) / 1e6
                    tot += c
                    n += 1
                    print(f"{name} {r['src']} {px[0]}×{px[1]} 예상 입력 {est_in:.0f} 출력 {EST['out']} ${c:.4f}")
                    continue
                req = {
                    "model": a.model,
                    "instructions": INSTR,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "high"}
                            ],
                        }
                    ],
                    "reasoning": {"effort": "low"},
                    "max_output_tokens": 16000,
                    "text": {"format": {"type": "json_schema", "name": "ras_region", "strict": True, "schema": SCHEMA}},
                }
                t0 = time.time()
                resp = client.responses.create(**req)
                u = resp.usage
                cached = u.input_tokens_details.cached_tokens
                c = ((u.input_tokens - cached) * pin + cached * pcache + u.output_tokens * pout) / 1e6
                tot += c
                n += 1
                rec = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "key": "marcspon",
                    "mode": "region",
                    "model": a.model,
                    "effort": "low",
                    "report": key,
                    "page": pg,
                    "region": i,
                    "image_px": list(px),
                    "status": resp.status,
                    "secs": round(time.time() - t0, 1),
                    "input_tokens": u.input_tokens,
                    "cached_tokens": cached,
                    "output_tokens": u.output_tokens,
                    "reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", None),
                    "usd": round(c, 6),
                }
                with open(out.parent / "calls.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                (out / "raw" / f"{name}.json").write_text(resp.output_text or "", encoding="utf-8")
                try:
                    res = json.loads(resp.output_text)
                except (json.JSONDecodeError, TypeError):
                    print(f"{name}: JSON 아님 status={resp.status}", file=sys.stderr)
                    continue
                tables_out += to_page(res, r["bbox"])
                print(f"{name} {a.model} in {u.input_tokens} out {u.output_tokens} ${c:.4f} {rec['secs']}s")
            if not a.dry_run and tables_out:
                (out / "tables" / f"{key}_p{pg}.json").write_text(json.dumps(tables_out, ensure_ascii=False), encoding="utf-8")
    print(f"{n}건 합계 ${tot:.4f}" + (" (예상)" if a.dry_run else ""))


def to_page(res, rb):
    w, h = rb[2] - rb[0], rb[3] - rb[1]
    out = []
    for t in res.get("tables", []):
        b = t["bbox"] if len(t.get("bbox", [])) == 4 else [0, 0, 1, 1]
        out.append({"bbox": [rb[0] + b[0] * w, rb[1] + b[1] * h, rb[0] + b[2] * w, rb[1] + b[3] * h], "grid": t["grid"]})
    return out


def inter(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def golds(d):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(d).glob("*.json"))]


def write_raw(out, gid, items, src):
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "raw" / f"{gid}.json").write_text(json.dumps({"items": items, "from": src}, ensure_ascii=False), encoding="utf-8")


def cmd_collect_region(a):
    """③④: 정답 bbox 와 겹치는(정답 넓이의 10% 이상 또는 영역 넓이의 50% 이상) 찾은 영역 산출을 합친다."""
    regions = json.loads(Path(a.regions).read_text(encoding="utf-8"))
    out = Path(a.out)
    for g in golds(a.gold):
        regs = regions.get(g["report"], {}).get(str(g["page"]), [])
        items, src = [], []
        for i, r in enumerate(regs):
            x = inter(r["bbox"], g["bbox"])
            if x >= 0.1 * area(g["bbox"]) or x >= 0.5 * area(r["bbox"]):
                p = Path(a.calls_dir) / "raw" / f"{g['report']}_p{g['page']}_r{i}.json"
                if p.is_file():
                    try:
                        items += json.loads(p.read_text(encoding="utf-8"))["items"]
                        src.append(p.stem)
                    except (json.JSONDecodeError, KeyError):
                        src.append(p.stem + ":깨짐")
        write_raw(out, g["id"], items, src)
        print(g["id"], len(items), src)


def cmd_collect_docparse(a):
    """①②: 정답 bbox 안에 가운데가 든 docparse 블록 글(표 블록 빼고) + 표 폴더의 격자 중 정답과 겹치는 것(pick 꼴)의 칸 글."""
    docs = {k: json.loads(Path(v).read_text(encoding="utf-8")) for k, v in (x.split("=", 1) for x in a.docs.split(","))}
    out = Path(a.out)
    for g in golds(a.gold):
        d = docs[g["report"]]
        b = g["bbox"]
        items = []
        for blk in d["blocks"]:
            if blk["page"] != g["page"] or blk["kind"] == "table":
                continue
            x0, y0, x1, y1 = blk["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if b[0] <= cx <= b[2] and b[1] <= cy <= b[3] and (blk.get("text") or "").strip():
                items.append(blk["text"])
        tsrc = Path(a.tables) / f"{g['report']}_p{g['page']}.json" if a.tables else None
        if tsrc and tsrc.is_file():
            for t in json.loads(tsrc.read_text(encoding="utf-8")):
                x = inter(t["bbox"], b)
                if x >= 0.5 * area(t["bbox"]) or x >= 0.5 * area(b):
                    items += [c for row in t["grid"] for c in row if c and c.strip()]
        write_raw(out, g["id"], items, "docparse" + ("+tables" if tsrc else ""))
        print(g["id"], len(items))


def lev1(a, b):
    """편집 거리 1 이하(바꿈·넣음·뺌 한 글자)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1 :] == s for i in range(len(t)))


def changed(missing, extra):
    """숫자 바뀐 칸(정답 대조): 산출에 없는 정답 숫자 중, 짝 없는 산출 숫자가 편집 거리 1 로 가까운 것(오독 꼴). 두 자 이상만."""
    pool = list(extra.elements())
    n = 0
    for m in missing.elements():
        if len(m) < 2:
            continue
        k = next((i for i, x in enumerate(pool) if len(x) >= 2 and lev1(m, x)), None)
        if k is not None:
            pool.pop(k)
            n += 1
    return n


def nums(text_list):
    return Counter(m for t in text_list for w in t.split() for m in NUM.findall(norm(w)))


def cmd_score(a):
    regions = json.loads(Path(a.regions).read_text(encoding="utf-8")) if a.regions else None
    rows = []
    for g in golds(a.gold):
        rp = Path(a.cand) / "raw" / f"{g['id']}.json"
        items = json.loads(rp.read_text(encoding="utf-8"))["items"] if rp.is_file() else []
        out_text = norm("".join(items))
        gold_text = norm("".join(g["words"]))
        gw = [norm(w) for w in g["words"] if norm(w)]
        found = sum(w in out_text for w in gw)
        made = [w for it in items for w in it.split() if len(norm(w)) >= 2 and norm(w) not in gold_text]
        gn, on = nums(g["words"]), nums(items)
        r = {
            "id": g["id"],
            "kind": g["kind"],
            "class": g["class"],
            "gold_words": len(gw),
            "found": found,
            "made_up": len(made),
            "made_up_examples": made[:8],
            "gold_nums": sum(gn.values()),
            "nums_found": sum((gn & on).values()),
            "nums_found_substr": sum(c for m, c in gn.items() if m in out_text),
            "nums_bad": sum((on - gn).values()),
            "nums_changed": changed(gn - on, on - gn),
            "nums_bad_examples": list((on - gn).elements())[:8],
        }
        if regions is not None:
            regs = regions.get(g["report"], {}).get(str(g["page"]), [])
            best = max(regs, key=lambda x: inter(x["bbox"], g["bbox"]), default=None)
            cov = inter(best["bbox"], g["bbox"]) / area(g["bbox"]) if best else 0.0
            r["region_hit"] = cov >= 0.9
            r["region_cover"] = round(cov, 3)
            r["region_area_ratio"] = round(area(best["bbox"]) / area(g["bbox"]), 2) if best else None
            r["region_src"] = best["src"] if best else None
        rows.append(r)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["class"], r["kind"])].append(r)
        groups[(r["class"], "ALL")].append(r)
        groups[("ALL", "ALL")].append(r)

    def agg(rs):
        s = lambda k: sum(r[k] for r in rs)  # noqa: E731
        d = {
            "n": len(rs),
            "word_recall": round(s("found") / max(1, s("gold_words")), 4),
            "num_recall": round(s("nums_found") / max(1, s("gold_nums")), 4) if s("gold_nums") else None,
            "num_recall_substr": round(s("nums_found_substr") / max(1, s("gold_nums")), 4) if s("gold_nums") else None,
            "made_up": s("made_up"),
            "nums_bad": s("nums_bad"),
            "nums_changed": s("nums_changed"),
            "gold_words": s("gold_words"),
            "gold_nums": s("gold_nums"),
        }
        if "region_hit" in rs[0]:
            d["region_hit"] = f"{sum(r['region_hit'] for r in rs)}/{len(rs)}"
        return d

    summary = {f"{c}/{k}": agg(rs) for (c, k), rs in sorted(groups.items())}
    pct = lambda v: "—" if v is None else f"{v * 100:.1f}"  # noqa: E731
    print(f"## {a.label or a.cand}\n\n| 갈래/분류 | 항목 | 낱말 recall | 숫자 recall | (부분문자열) | 숫자 바뀐 칸(정답 대조) | (참고) 지어낸 낱말 | (참고) 정답 밖 숫자 | 영역 맞음 |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, d in summary.items():
        print(f"| {k} | {d['n']} | {pct(d['word_recall'])} | {pct(d['num_recall'])} | {pct(d['num_recall_substr'])} | {d['nums_changed']} | {d['made_up']} | {d['nums_bad']} | {d.get('region_hit', '')} |")
    Path(a.cand, "score.json").write_text(json.dumps({"items": rows, "summary": summary}, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("call")
    c.add_argument("--regions", required=True)
    c.add_argument("--pdfs", required=True)
    c.add_argument("--pdf-map", required=True)
    c.add_argument("--pages", default="")
    c.add_argument("--model", choices=sorted(PRICES), required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--again", action="store_true", help="이미 받은 영역도 다시 부른다")
    co = sub.add_parser("collect")
    co.add_argument("--gold", required=True)
    co.add_argument("--out", required=True)
    cs = co.add_subparsers(dest="src", required=True)
    r = cs.add_parser("region")
    r.add_argument("--regions", required=True)
    r.add_argument("--calls-dir", required=True)
    d = cs.add_parser("docparse")
    d.add_argument("--docs", required=True)
    d.add_argument("--tables")
    s = sub.add_parser("score")
    s.add_argument("--gold", required=True)
    s.add_argument("--cand", required=True)
    s.add_argument("--regions")
    s.add_argument("--label", default="")
    a = ap.parse_args(argv)
    if a.cmd == "call":
        cmd_call(a)
    elif a.cmd == "collect":
        (cmd_collect_region if a.src == "region" else cmd_collect_docparse)(a)
    else:
        cmd_score(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
