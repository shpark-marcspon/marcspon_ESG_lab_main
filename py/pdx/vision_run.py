"""비전 LLM 후보 ③ 혼합 · ④ 비전만 (PDX1, MARC-TICK-339). OpenAI Responses API.

③ hybrid: 쪽 PNG + 그 쪽 글자 층 블록(docparse `parse` 산출의 블록 id·자리·글. 표는 칸 글만 한 블록으로 — 격자는 안 줌)
           → 표 격자(칸 글은 준 글자에서 베껴 씀)·블록 읽는 차례·끼워 넣기(위첨자 조각 등)
④ vision: 쪽 PNG 만 → 읽는 차례대로의 블록 글·표 격자

  uv run --no-project --python 3.12 --with openai==3.26.0 --with pypdfium2==5.14.0 --with pillow==12.3.0 python -I vision_run.py \
      --mode hybrid|vision --model gpt-6-luna --targets samsung:64,kb:17 --reports <PDF 폴더> \
      --docs samsung=<document.json>,kb=<document.json> --out <산출 폴더> [--dry-run] [--effort low] [--dpi 150] [--layout 1|2]

--layout 은 --docs 가 그 docparse 판(`parser.layout`, 없으면 1)으로 뽑힌 것인지 확인만 한다(PDX2). 모델에 주는 블록 꼴은 판과 무관하게 같다.

키는 환경 변수 OPENAI_API_KEY 에서만 읽는다(값을 쓰거나 찍지 않는다). --dry-run 이면 부르지 않고 예상 토큰·금액만 찍는다.
호출마다 <out>/../calls.jsonl 에 모델·쪽·usage·금액을 한 줄. 응답 JSON 은 <out>/raw/, 채점 꼴은 <out>/tables/·<out>/streams/.
"""

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

# 1M 토큰당 USD (Standard). https://developers.openai.com/api/docs/pricing 확인 2026-10-27
PRICES = {"gpt-6-luna": (0.10, 0.01, 0.50), "gpt-6.1-sol": (2.00, 0.10, 10.00)}
REPORT_FILES = {
    "samsung": "samsung_2026_kor.pdf",
    "kb": "kb_2025_kor.pdf",
    "kb_databook": "kb_2025_databook_kor.pdf",
    "cosmax": "cosmax_2025_kor.pdf",
    "hyosungchem": "hyosungchem_2025_kor.pdf",  # 경계 D (처음 보는 보고서)
    "cjenm": "cjenm_2025_kor.pdf",
    "hdec": "hdec_2025_kor.pdf",
    "hdelectric": "hdelectric_2025_kor.pdf",
    "gunpouc": "gunpouc_2025_kor.pdf",  # PDX2 G1 (처음 보는 보고서 셋째 묶음, 칸 괘선 표)
    "keco": "keco_2025_kor.pdf",
    "kcc": "kcc_2024_kor.pdf",
}
# dry-run 어림(첫 실호출로 고친다): 이미지 토큰, 글 1자당 토큰, 출력 = 쪽 글 × 비율 + 추론
EST = {"image": 1600, "per_char": 0.9, "prompt": 900, "out_ratio": {"hybrid": 0.7, "vision": 1.3}, "reasoning": 1500}

GRID = {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
BBOX = {"type": "array", "items": {"type": "number"}, "description": "[x0,y0,x1,y1], 쪽 너비·높이에 대한 0~1 비율, 위-왼 원점"}
TABLE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["bbox", "grid"],
    "properties": {"bbox": BBOX, "grid": GRID},
}
SCHEMA = {
    "hybrid": {
        "type": "object",
        "additionalProperties": False,
        "required": ["tables", "order", "inserts"],
        "properties": {
            "tables": {"type": "array", "items": TABLE},
            "order": {"type": "array", "items": {"type": "string"}, "description": "읽는 차례대로의 블록 id. 뺄 블록은 넣지 않는다"},
            "inserts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "into", "after"],
                    "properties": {"id": {"type": "string"}, "into": {"type": "string"}, "after": {"type": "string"}},
                },
            },
        },
    },
    "vision": {
        "type": "object",
        "additionalProperties": False,
        "required": ["blocks", "tables"],
        "properties": {
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "text"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["heading", "paragraph", "box", "list", "footnote", "caption", "visual", "table", "header", "footer"],
                        },
                        "text": {"type": "string", "description": "kind=table 이면 tables 의 번호(0부터)"},
                    },
                },
            },
            "tables": {"type": "array", "items": TABLE},
        },
    },
}

COMMON = """너는 지속가능경영보고서 PDF 한 쪽을 구조로 푸는 도구다. 결과는 번역과 수치 검증에 쓰인다.
읽는 차례: 사람이 읽는 차례로 — 단은 왼쪽 단을 위에서 아래로 다 읽고 다음 단, 상자·사이드바는 놓인 자리에서 통째로,
각주는 그 단의 끝. 쪽 머리·꼬리·탐색 띠와 쪽 번호는 맨 끝(또는 header/footer).
표: 쪽 안의 데이터 표마다 격자 하나. 한 행 = 표의 한 줄, 칸은 왼쪽부터. 병합 칸은 맨 위·맨 왼쪽 칸에만 글을 쓰고 나머지는 "".
머리글 행도 행으로 넣는다. 각주 표시(1) 등)는 붙은 자리 그대로 둔다. 숫자는 보이는 그대로(쉼표 포함). 도표(막대·원)는 표가 아니다.
bbox 는 그 표가 차지한 영역을 쪽 너비·높이의 0~1 비율로."""

HYBRID = (
    COMMON
    + """
이 쪽의 글자 층(PDF 에 든 글자)을 블록으로 준다. 블록마다 id, 자리[x0,y0,x1,y1](pt, 위-왼 원점), 글.
글자는 이 블록 글에서만 베껴 쓴다(고쳐 쓰거나 지어내지 않는다). 이미지는 구조(단·상자·표 격자)를 보는 데 쓴다.
- order: 읽는 차례대로의 블록 id. 표 글 블록(kind=table_text)은 표가 놓인 자리에 둔다.
- inserts: 줄 끝 위첨자 등으로 문단 밖으로 떨어져 나온 조각 블록이 있으면, 그 블록 id 를 order 에서 빼고
  {"id": 조각, "into": 들어갈 블록, "after": 들어갈 자리 바로 앞 글 8~15자(그 블록 글에 그대로 있는 글)} 로 적는다.
- tables: 데이터 표의 격자. 칸 글은 블록 글에서 베낀다."""
)

VISION = (
    COMMON
    + """
이미지만 보고 쪽의 글을 빠짐없이 옮겨 적는다(한글·영문 그대로, 요약·번역 금지).
- blocks: 읽는 차례대로. 제목은 heading, 본문 문단은 paragraph(문단 하나 = 블록 하나, 줄바꿈으로 끊지 않음), 상자 글은 box,
  글머리 목록은 list, 각주는 footnote, 그림·도표 제목은 caption, 도표·인포그래픽·사진 안의 글은 visual 하나에 모아서,
  표 자리는 kind=table 과 text=tables 번호.
- tables: 데이터 표의 격자."""
)


def render_png(pdf, page, dpi):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    try:
        p = doc[page - 1]
        w, h = p.get_size()
        img = p.render(scale=dpi / 72).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), (w, h), img.size
    finally:
        doc.close()


def page_blocks(doc, page):
    tables = {t["id"]: t for t in doc["tables"]}
    out = []
    for b in sorted((b for b in doc["blocks"] if b["page"] == page), key=lambda b: b["order"]):
        if b["kind"] == "table":
            text = " ".join(c for row in tables[b["table_id"]]["cells"] for c in row if c)
            out.append({"id": b["id"], "kind": "table_text", "bbox": [round(x) for x in b["bbox"]], "text": text})
        else:
            out.append({"id": b["id"], "kind": b["kind"], "bbox": [round(x) for x in b["bbox"]], "text": b.get("text", "")})
    return out


def cost(model, usage):
    pin, pcache, pout = PRICES[model]
    cached = usage.get("cached_tokens", 0)
    return ((usage["input_tokens"] - cached) * pin + cached * pcache + usage["output_tokens"] * pout) / 1e6


def stream_hybrid(blocks, res):
    by_id = {b["id"]: b["text"] for b in blocks}
    texts = {}
    for i in res["order"]:
        if i in by_id:
            texts[i] = by_id[i]
    for ins in res["inserts"]:
        if ins["id"] in by_id and ins["into"] in texts:
            t = texts[ins["into"]]
            k = t.find(ins["after"])
            frag = by_id[ins["id"]]
            texts[ins["into"]] = t[: k + len(ins["after"])] + frag + t[k + len(ins["after"]) :] if k >= 0 else t + " " + frag
    return "\n".join(texts[i] for i in res["order"] if i in texts)


def stream_vision(res):
    parts = []
    for b in res["blocks"]:
        if b["kind"] == "table":
            try:
                g = res["tables"][int(b["text"])]["grid"]
                parts.append(" ".join(c for row in g for c in row if c))
            except (ValueError, IndexError):
                parts.append(b["text"])
        else:
            parts.append(b["text"])
    return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("hybrid", "vision"), required=True)
    ap.add_argument("--model", choices=sorted(PRICES), required=True)
    ap.add_argument("--targets", required=True, help="report:page,report:page")
    ap.add_argument("--reports", required=True)
    ap.add_argument("--docs", required=True, help="report=<document.json>,...")
    ap.add_argument("--out", required=True)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--layout", type=int, choices=(1, 2), default=1)
    ap.add_argument("--pdf-map", default="", help="report=<파일 이름>,... (REPORT_FILES 에 더함)")
    a = ap.parse_args(argv)
    docs = {k: json.loads(Path(v).read_text(encoding="utf-8")) for k, v in (x.split("=", 1) for x in a.docs.split(","))}
    bad = [k for k, d in docs.items() if (d.get("parser") or {}).get("layout", 1) != a.layout]
    if bad:
        sys.exit(f"--layout {a.layout} 인데 판이 다른 document.json: {bad}")
    files = {**REPORT_FILES, **dict(x.split("=", 1) for x in a.pdf_map.split(",") if x)}
    targets = [(r, int(p)) for r, p in (x.split(":") for x in a.targets.split(","))]
    out = Path(a.out)
    for sub in ("raw", "tables", "streams"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    instr = HYBRID if a.mode == "hybrid" else VISION
    client = None
    if not a.dry_run:
        from openai import OpenAI

        client = OpenAI()
    tot_est = tot_cost = 0.0
    for rep, pg in targets:
        blocks = page_blocks(docs[rep], pg)
        page_chars = sum(len(b["text"]) for b in blocks)
        png, (w, h), px = render_png(Path(a.reports) / files[rep], pg, a.dpi)
        user_text = f"쪽 크기 {w:.0f}×{h:.0f} pt."
        if a.mode == "hybrid":
            user_text += "\n글자 층 블록(JSON):\n" + json.dumps(blocks, ensure_ascii=False)
        est_in = EST["prompt"] + EST["image"] + EST["per_char"] * (len(user_text) if a.mode == "hybrid" else 0)
        est_out = EST["reasoning"] + EST["per_char"] * page_chars * EST["out_ratio"][a.mode]
        est = cost(a.model, {"input_tokens": est_in, "output_tokens": est_out})
        tot_est += est
        if a.dry_run:
            print(f"{rep} p{pg} {a.mode} {a.model} 글 {page_chars}자 이미지 {px[0]}×{px[1]} 예상 입력 {est_in:.0f} 출력 {est_out:.0f} ${est:.4f}")
            continue
        b64 = base64.b64encode(png).decode()
        req = {
            "model": a.model,
            "instructions": instr,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_text},
                        {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": "high"},
                    ],
                }
            ],
            "reasoning": {"effort": a.effort},
            "max_output_tokens": 32000,
            "text": {"format": {"type": "json_schema", "name": f"pdx_{a.mode}", "strict": True, "schema": SCHEMA[a.mode]}},
        }
        t0 = time.time()
        resp = client.responses.create(**req)
        secs = time.time() - t0
        u = resp.usage
        usage = {
            "input_tokens": u.input_tokens,
            "cached_tokens": u.input_tokens_details.cached_tokens,
            "output_tokens": u.output_tokens,
            "reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", None),
        }
        c = cost(a.model, usage)
        tot_cost += c
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "key": "marcspon",
            "mode": a.mode,
            "model": a.model,
            "effort": a.effort,
            "report": rep,
            "page": pg,
            "dpi": a.dpi,
            "image_px": list(px),
            "status": resp.status,
            "secs": round(secs, 1),
            **usage,
            "usd": round(c, 6),
        }
        with open(out.parent / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        name = f"{rep}_p{pg}"
        (out / "raw" / f"{name}.json").write_text(resp.output_text or "", encoding="utf-8")
        try:
            res = json.loads(resp.output_text)
        except (json.JSONDecodeError, TypeError):
            print(f"{name}: JSON 아님 status={resp.status}", file=sys.stderr)
            continue
        tables = [
            {"bbox": [t["bbox"][0] * w, t["bbox"][1] * h, t["bbox"][2] * w, t["bbox"][3] * h], "grid": t["grid"]}
            for t in res["tables"]
            if len(t["bbox"]) == 4
        ]
        (out / "tables" / f"{name}.json").write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
        stream = stream_hybrid(blocks, res) if a.mode == "hybrid" else stream_vision(res)
        (out / "streams" / f"{name}.txt").write_text(stream, encoding="utf-8")
        print(f"{name} {a.mode} {a.model} in {usage['input_tokens']} out {usage['output_tokens']} (추론 {usage['reasoning_tokens']}) ${c:.4f} {secs:.0f}s")
    print(f"합계 예상 ${tot_est:.4f}" + ("" if a.dry_run else f" · 실제 ${tot_cost:.4f}"))


if __name__ == "__main__":
    main()
