"""그림 속 글 뽑기 (PDX1 경계 D). 그림 영역만 잘라 비전 LLM 에 보내고, 정답 낱말 목록과 견준다.

  # 정답 짓기: 영역 안 글자 층 낱말(사람이 잘라 낸 이미지와 대조해 확인한다)
  uv run --no-project --python 3.12 --with pypdfium2==5.14.0 --with pillow==12.3.0 python -I figure_run.py gold <specs.json> <PDF 폴더> <정답 폴더>
  # 부르기(--dry-run 이면 부르지 않고 이미지 크기·예상 금액만)
  uv run ... --with openai==3.26.0 python -I figure_run.py run <specs.json> <PDF 폴더> <out> --model gpt-6.1-sol [--dry-run]
  # 채점(표준 라이브러리만)
  python3 figure_run.py score <정답 폴더> <out>

지표: 찾음률 = 정답 낱말 중 모델 글(정규화해 이은 것)에 들어 있는 비율. 지어낸 낱말 = 모델 글의 낱말(2자 이상) 중
정답 글(정규화해 이은 것)에 없는 것의 수. 정규화는 NFKC·공백 지움·쉼표 지움·가운뎃점 통일.
호출마다 <out>/../calls.jsonl 에 한 줄(vision_run.py 와 같은 꼴).
"""

import argparse
import base64
import io
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

PRICES = {"gpt-6-luna": (0.10, 0.01, 0.50), "gpt-6.1-sol": (2.00, 0.10, 10.00)}  # 1M 토큰당 USD, 확인 2026-10-27
SCALE = 2.5  # 180dpi 꼴
PROMPT = """지속가능경영보고서의 그림(도식·조직도·도표) 하나를 잘라 낸 이미지다. 그림 안에 보이는 글자를 빠짐없이 옮겨 적어라.
- 칸·상자·축 이름·수치·범례 하나하나를 항목 하나로, 보이는 그대로(한글·영문·숫자, 요약·번역·추론 금지).
- 읽는 차례: 위에서 아래, 왼쪽에서 오른쪽, 상자는 상자 단위로.
- 이미지 가장자리에서 잘린 글자는 읽을 수 있는 만큼만."""
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {"items": {"type": "array", "items": {"type": "string"}}},
}


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[∙·•‧ㆍ・]", "·", re.sub(r"\s+", "", s).replace(",", ""))


def crop_png(pdf, page, bbox):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        img = doc[page - 1].render(scale=SCALE).to_pil().crop([int(v * SCALE) for v in bbox])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), img.size
    finally:
        doc.close()


def cmd_gold(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # python -I 는 스크립트 폴더를 경로에 넣지 않는다
    from make_gold import cluster_rows, words_in

    out = Path(a.target)
    out.mkdir(parents=True, exist_ok=True)
    for s in json.loads(Path(a.specs).read_text(encoding="utf-8")):
        rows = cluster_rows(words_in(Path(a.pdfs) / s["pdf"], s["page"], s["bbox"]))
        words = [re.sub(r"\s+", "", w[4]) if re.fullmatch(r"[\d.,%\s]+", w[4]) else w[4] for r in rows for w in r["ws"]]
        # 숫자 자간(`1 59`)은 한 낱말로: 이웃한 숫자 조각을 잇는다
        merged = []
        for w in words:
            if merged and re.fullmatch(r"\d+", merged[-1]) and re.fullmatch(r"\d+", w) and len(merged[-1]) == 1:
                merged[-1] += w
            else:
                merged.append(w)
        d = {**s, "words": merged, "method": "영역 안 글자 층 낱말, 잘라 낸 이미지와 눈으로 대조"}
        (out / f"{s['id']}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(s["id"], len(merged), "낱말")


def cmd_run(a):
    out = Path(a.target)
    (out / "raw").mkdir(parents=True, exist_ok=True)
    client = None
    if not a.dry_run:
        from openai import OpenAI

        client = OpenAI()
    tot = 0.0
    for s in json.loads(Path(a.specs).read_text(encoding="utf-8")):
        png, px = crop_png(Path(a.pdfs) / s["pdf"], s["page"], s["bbox"])
        if a.dry_run:
            est_in, est_out = 900 + px[0] * px[1] / 750, 600  # 어림: 픽셀 750개당 1토큰
            pin, _, pout = PRICES[a.model]
            c = (est_in * pin + est_out * pout) / 1e6
            tot += c
            print(f"{s['id']} {px[0]}×{px[1]} 예상 입력 {est_in:.0f} 출력 {est_out} ${c:.4f}")
            continue
        req = {
            "model": a.model,
            "instructions": PROMPT,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "high"}
                    ],
                }
            ],
            "reasoning": {"effort": "low"},
            "max_output_tokens": 8000,
            "text": {"format": {"type": "json_schema", "name": "pdx_figure", "strict": True, "schema": SCHEMA}},
        }
        t0 = time.time()
        resp = client.responses.create(**req)
        u = resp.usage
        pin, pcache, pout = PRICES[a.model]
        cached = u.input_tokens_details.cached_tokens
        c = ((u.input_tokens - cached) * pin + cached * pcache + u.output_tokens * pout) / 1e6
        tot += c
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "key": "marcspon",
            "mode": "figure",
            "model": a.model,
            "effort": "low",
            "report": s["report"],
            "page": s["page"],
            "figure": s["id"],
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
        (out / "raw" / f"{s['id']}.json").write_text(resp.output_text or "", encoding="utf-8")
        print(s["id"], u.input_tokens, u.output_tokens, f"${c:.4f}")
    print(f"합계 ${tot:.4f}" + (" (예상)" if a.dry_run else ""))


def cmd_score(a):
    res, found_n, gold_n, made_n = {}, 0, 0, 0
    for g in sorted(Path(a.specs).glob("*.json")):
        if g.name == "specs.json":
            continue
        gold = json.loads(g.read_text(encoding="utf-8"))
        rp = Path(a.target) / "raw" / g.name
        items = json.loads(rp.read_text(encoding="utf-8"))["items"] if rp.is_file() else []
        out_text = norm("".join(items))
        gold_text = norm("".join(gold["words"]))
        gw = [norm(w) for w in gold["words"] if norm(w)]
        found = [w for w in gw if w in out_text]
        made = [w for it in items for w in it.split() if len(norm(w)) >= 2 and norm(w) not in gold_text]
        res[g.stem] = {"gold_words": len(gw), "found": len(found), "made_up": len(made), "made_up_examples": made[:10]}
        found_n, gold_n, made_n = found_n + len(found), gold_n + len(gw), made_n + len(made)
    print("| 그림 | 정답 낱말 | 찾음 | 지어낸 낱말 |\n|---|---:|---:|---:|")
    for k, r in res.items():
        print(f"| {k} | {r['gold_words']} | {r['found']} | {r['made_up']} |")
    print(f"| **합계** | {gold_n} | {found_n} ({found_n / max(1, gold_n):.1%}) | {made_n} |")
    dump = {"figures": res, "found_rate": round(found_n / max(1, gold_n), 4), "made_up": made_n}
    (Path(a.target) / "score.json").write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("gold", "run", "score"))
    ap.add_argument("specs", help="gold/run: specs.json, score: 정답 폴더")
    ap.add_argument("pdfs", nargs="?", help="gold/run: PDF 폴더")
    ap.add_argument("target", nargs="?", help="gold: 정답 폴더, run/score: 산출 폴더")
    ap.add_argument("--model", choices=sorted(PRICES), default="gpt-6.1-sol")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "score":
        a.target = a.pdfs
    {"gold": cmd_gold, "run": cmd_run, "score": cmd_score}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
