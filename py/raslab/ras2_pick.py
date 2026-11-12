"""TRX9 ②·③ — 영역 자르기(180dpi · native)·장부·자리 상한·부르기(luna 기본/소수점, sol 되묻기)·②a 흉내·채점.
실호출은 마크스폰 키(환경 변수 OPENAI_API_KEY, 값 출력 없음). PIL 없이 numpy 렌더(`ras2_scan.render`) + `trlab.thumb_vision.png_bytes`.
`raslab.run` 은 INSTR·SCHEMA·EST·to_page·inter·area·golds·write_raw·cmd_score 를 import 만 한다(장부가 섞이지 않게 `cmd_call` 은 부르지 않는다).

  … python -m raslab.ras2_pick sim                       ②a: RAS1 luna raw → 검사(R1·R2·R3·R5) → 걸린 영역은 RAS1 sol raw 로 → 채점 (실호출 0)
  … python -m raslab.ras2_pick call b2 luna [--hint] [--dry-run]   ②b: 정답 쪽 영역 27 을 luna 로(기본 | 소수점 지시문)
  … python -m raslab.ras2_pick check b2                  luna 둘 → R1~R5 → OUT9/b2/check.json (sol 되묻기 후보)
  … python -m raslab.ras2_pick call b2 sol [--dry-run]    걸린 영역만 sol
  … python -m raslab.ras2_pick score b2                  채점 셋: luna 기본 · luna 소수점 · luna+검사→sol
  … python -m raslab.ras2_pick crop [--native]            ③: 네 영역 자름 → OUT9/crop/*.png + crops.json
  … python -m raslab.ras2_pick call b3 luna|sol [--dry-run]   ③: 네 영역 native 로
  … python -m raslab.ras2_pick score b3
장부 OUT9/calls.jsonl (RAS1 run.cmd_call 꼴 + tag·site·estimateUsd·status). 태그 TRX9/<판>/<키>_p<쪽>_r<영역>.
자리 상한 CAPS9 luna 0.1 · sol 0.5 · 합 0.6, 판 상한 SUBCAPS9 b2/sol ≤ 0.25 · b3/sol ≤ 0.15. 누적 + 잡아 둔 몫 + 예상 > 상한이면 over_cap(보내지 않음).
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

from raslab import ras2_check as C
from raslab import ras2_scan as S
from raslab import run as R

OUT9 = S.OUT9
RAS1 = Path(os.environ.get("RAS1_OUT", str(Path.home() / "Desktop" / "marcspon" / "esg-lab" / "out" / "ras1")))
GOLD = Path(os.environ.get("GOLD_RASTER", str(Path.home() / "Desktop" / "marcspon" / "prework" / "gold-raster")))
MODELS = {"luna": "gpt-6-luna", "sol": "gpt-6.1-sol"}
PRICES = {"gpt-6-luna": (0.1, 0.01, 0.5), "gpt-6.1-sol": (2.0, 0.1, 10.0)}  # semgraph/llm.py:9-13 과 같음
CAPS9 = {"luna": 0.1, "sol": 0.5}
TOTAL_CAP9 = 0.6
SUBCAPS9 = {("b2", "sol"): 0.25, ("b3", "sol"): 0.15}
NATIVE = {"min_dpi": 180.0, "max_dpi": 600.0, "max_px": 4_000_000}
B3_REGIONS = {("issi", 45, 0), ("issi", 46, 1), ("issi", 50, 0), ("issi", 74, 1)}  # 발주서 ★5 — 원본 dpi > 180 인 넷
B3_GOLD = {"is_p45_c1", "is_p45_c2", "is_p45_c3", "is_p46_tbl", "is_p50_tbl", "is_p74_tbl"}
_RESERVED = {"luna": 0.0, "sol": 0.0}


# ---------- 키 ----------
def keymap():
    """RAS1 regions.json 의 보고서 키 ↔ 짧은 키 (books.json 의 pdf 이름으로 잇는다). 코드·보고에는 짧은 키만 쓴다."""
    _, books = S.load_books()
    scan = {}
    for p in sorted((RAS1 / "scan").glob("*.json")):
        if p.stem == "frozen":
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        scan[d["pdf"]] = p.stem
    long2short = {scan[b["pdf"]]: k for k, b in books.items() if b["pdf"] in scan}
    return long2short, {v: k for k, v in long2short.items()}


def regions27():
    """정답 쪽의 영역 27 — [(짧은 키, 쪽, i, bbox, src)], RAS1 regions.json 읽기만."""
    regs = json.loads((RAS1 / "regions" / "regions.json").read_text(encoding="utf-8"))
    l2s, _ = keymap()
    pages = {"issi": [31, 45, 46, 50, 73, 74, 82, 89], "fm": [4, 8, 11]}
    out = []
    for long, pmap in regs.items():
        k = l2s[long]
        for pg in pages.get(k, []):
            for i, r in enumerate(pmap.get(str(pg), [])):
                out.append((k, pg, i, r["bbox"], r["src"]))
    return out


def tag(site, k, pg, i):
    return f"TRX9/{site}/{k}_p{pg}_r{i}"


# ---------- 장부 ----------
def calls():
    p = OUT9 / "calls.jsonl"
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()] if p.exists() else []


def spent(rows, prefix=""):
    return sum(r.get("usd", 0) for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(prefix))


def site_of(model):
    return "luna" if "luna" in model else "sol"


def reserve(site, sub, usd, rows=None):
    """누적 + 잡아 둔 몫 + 예상 > 자리 상한(또는 합·판 상한)이면 False."""
    rows = calls() if rows is None else rows
    held = sum(_RESERVED.values())
    by_site = sum(r.get("usd", 0) for r in rows if not r.get("dry_run") and site_of(r.get("model", "")) == site)
    if by_site + _RESERVED[site] + usd > CAPS9[site]:
        return False
    if spent(rows) + held + usd > TOTAL_CAP9:
        return False
    cap = SUBCAPS9.get((sub, site))
    if cap is not None and _sub_spent(rows, sub, site) + _RESERVED[site] + usd > cap:
        return False
    _RESERVED[site] += usd
    return True


def _sub_spent(rows, sub, site):
    return sum(r.get("usd", 0) for r in rows if not r.get("dry_run") and r.get("tag", "").startswith(f"TRX9/{sub}") and site_of(r.get("model", "")) == site)


def release(site, usd):
    _RESERVED[site] = max(0.0, _RESERVED[site] - usd)


def log(row):
    OUT9.mkdir(parents=True, exist_ok=True)
    with open(OUT9 / "calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def estimate(model, px, out_tokens=None):
    """입력 ≈ 450 + px/750(run.EST), 출력 ≈ 같은 영역 RAS1 실측(없으면 600)."""
    pin, _, pout = PRICES[model]
    est_in = R.EST["prompt"] + px[0] * px[1] / R.EST["px_per_token"]
    return (est_in * pin + (out_tokens or R.EST["out"]) * pout) / 1e6, est_in


def ras1_out_tokens(model, k, pg, i):
    _, s2l = keymap()
    for r in [json.loads(x) for x in open(RAS1 / "calls.jsonl", encoding="utf-8") if x.strip()]:
        if r.get("mode") == "region" and r.get("model") == model and r.get("report") == s2l.get(k) and r.get("page") == pg and r.get("region") == i:
            return r.get("output_tokens")
    return None


# ---------- 자르기 ----------
def crop_png(pdf, page, bbox, scale):
    import pypdfium2 as pdfium

    from trlab.thumb_vision import png_bytes

    doc = pdfium.PdfDocument(str(pdf))
    try:
        arr = S.crop_px(S.render(doc[page - 1], scale), bbox, scale)
    finally:
        doc.close()
    return png_bytes(arr), (arr.shape[1], arr.shape[0])


def native_scale(pieces_dpi, bbox, min_dpi=NATIVE["min_dpi"], max_dpi=NATIVE["max_dpi"], max_px=NATIVE["max_px"]):
    """영역 배율 = max(조각 native dpi, 180)/72, 위 600dpi·4Mpx. (배율, 고른 dpi)."""
    dpi = max([min_dpi] + [d for d in pieces_dpi if d])
    dpi = min(dpi, max_dpi)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if w * h * (dpi / 72) ** 2 > max_px:
        dpi = (max_px / (w * h)) ** 0.5 * 72
    return dpi / 72, round(dpi, 1)


def pieces_native_dpi(pdf, page, bbox):
    """영역과 겹치는 이미지 조각의 원래 화소로 native dpi — pypdfium2 PdfImage.get_metadata()."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument(str(pdf))
    out = []
    try:
        pg = doc[page - 1]
        _, h = pg.get_size()
        for obj in pg.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
            (x0, y0, x1, y1), _, _ = S.base.to_page(obj)
            bx = [x0, h - y1, x1, h - y0]
            if R.inter(bx, bbox) <= 0:
                continue
            m = obj.get_metadata()
            if m.width and bx[2] - bx[0] > 0:
                out.append({"bbox": [round(v, 1) for v in bx], "px": [m.width, m.height], "dpi": round(m.width / (bx[2] - bx[0]) * 72, 1)})
    finally:
        doc.close()
    return out


def cmd_crop(a):
    pdfs, books = S.load_books()
    (OUT9 / "crop").mkdir(parents=True, exist_ok=True)
    out = {}
    for k, pg, i, bbox, src in regions27():
        if (k, pg, i) not in B3_REGIONS:
            continue
        pdf = pdfs / books[k]["pdf"]
        pieces = pieces_native_dpi(pdf, pg, bbox)
        for name, scale in (("180", R.SCALE), ("native", native_scale([p["dpi"] for p in pieces], bbox)[0])):
            if name == "native" and not a.native:
                continue
            png, px = crop_png(pdf, pg, bbox, scale)
            p = OUT9 / "crop" / f"{k}_p{pg}_r{i}-{name}.png"
            p.write_bytes(png)
            out[f"{k}_p{pg}_r{i}-{name}"] = {"scale": round(scale, 3), "dpi": round(scale * 72, 1), "px": px, "pieces": pieces}
            print(p.name, px, "dpi", round(scale * 72), pieces and pieces[0]["px"])
    (OUT9 / "crop" / "crops.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 부르기 ----------
def request(model, png, instr):
    return {
        "model": model,
        "instructions": instr,
        "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode(), "detail": "high"}]}],
        "reasoning": {"effort": "low"},
        "max_output_tokens": 16000,
        "text": {"format": {"type": "json_schema", "name": "ras_region", "strict": True, "schema": R.SCHEMA}},
    }


def respond(client, req):
    """실제 호출 — 시험은 가짜 client 로 바꾼다."""
    return client.responses.create(**req)


def send(client, site, sub, model, png, px, t, k, pg, i, hint=False, dry_run=False, est_out=None):
    inst = R.INSTR + (C.DECIMAL_HINT if hint else "")
    est_usd, est_in = estimate(model, px, est_out)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "key": "marcspon", "mode": "region", "model": model, "effort": "low", "report": k, "page": pg, "region": i, "image_px": list(px), "tag": t, "site": site, "hint": hint, "estimateUsd": round(est_usd, 6)}
    if dry_run:
        rec.update(dry_run=True, status="dry_run", usd=0.0)
        log(rec)
        return None, rec
    if not reserve(site, sub, est_usd):
        rec.update(status="over_cap", usd=0.0)
        log(rec)
        return None, rec
    t0 = time.time()
    try:
        resp = respond(client, request(model, png, inst))
    finally:
        release(site, est_usd)
    u = resp.usage
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    pin, pcache, pout = PRICES[model]
    c = ((u.input_tokens - cached) * pin + cached * pcache + u.output_tokens * pout) / 1e6
    rec.update(status=resp.status, secs=round(time.time() - t0, 1), input_tokens=u.input_tokens, cached_tokens=cached, output_tokens=u.output_tokens,
               reasoning_tokens=getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", None), usd=round(c, 6))
    log(rec)
    (OUT9 / "calls").mkdir(parents=True, exist_ok=True)
    (OUT9 / "calls" / (t.replace("/", "_") + ".json")).write_text(resp.output_text or "", encoding="utf-8")
    try:
        return json.loads(resp.output_text), rec
    except (json.JSONDecodeError, TypeError):
        return None, rec


def run_dir(sub, model_key, hint=False):
    return OUT9 / sub / (model_key + ("_hint" if hint else ""))


def cmd_call(a):
    pdfs, books = S.load_books()
    model = MODELS[a.model]
    site = a.model
    sub = a.site
    only = None
    if sub == "b2" and a.model == "sol":
        chk = json.loads((OUT9 / "b2" / "check.json").read_text(encoding="utf-8"))
        only = {tuple(x["region"]) for x in chk["regions"] if x["sol"]}
    client = None
    if not a.dry_run:
        from openai import OpenAI

        client = OpenAI(timeout=300.0, max_retries=1)
    d = run_dir(sub, a.model, a.hint)
    (d / "raw").mkdir(parents=True, exist_ok=True)
    tot = n = 0
    for k, pg, i, bbox, src in regions27():
        if sub == "b3" and (k, pg, i) not in B3_REGIONS:
            continue
        if only is not None and (k, pg, i) not in only:
            continue
        name = f"{k}_p{pg}_r{i}"
        if (d / "raw" / f"{name}.json").is_file() and not a.again:
            continue
        pdf = pdfs / books[k]["pdf"]
        if sub == "b3":
            scale = native_scale([p["dpi"] for p in pieces_native_dpi(pdf, pg, bbox)], bbox, max_dpi=a.max_dpi)[0]
        else:
            scale = R.SCALE
        png, px = crop_png(pdf, pg, bbox, scale)
        t = tag(sub + ("luna" if a.model == "luna" else "sol") + ("h" if a.hint else ""), k, pg, i)
        res, rec = send(client, site, sub, model, png, px, t, k, pg, i, hint=a.hint, dry_run=a.dry_run, est_out=ras1_out_tokens(model, k, pg, i))
        n += 1
        tot += rec.get("usd", 0) if not a.dry_run else rec["estimateUsd"]
        print(name, rec["status"], px, f"est ${rec['estimateUsd']:.4f}", f"usd ${rec.get('usd', 0):.4f}", rec.get("input_tokens", ""), rec.get("output_tokens", ""), flush=True)
        if res is not None:
            (d / "raw" / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        if a.limit and n >= a.limit:
            break
    print(f"{n}건 {'예상' if a.dry_run else '실제'} 합 ${tot:.4f} · 장부 누적 ${spent(calls()):.4f}")


# ---------- 검사 ----------
def load_raw(d, name):
    p = d / "raw" / f"{name}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def cmd_check(a):
    base, hint = run_dir("b2", "luna"), run_dir("b2", "luna", True)
    out = []
    for k, pg, i, bbox, src in regions27():
        name = f"{k}_p{pg}_r{i}"
        ra, rb = load_raw(base, name), load_raw(hint, name)
        if ra is None:
            out.append({"region": [k, pg, i], "name": name, "status": "missing", "flags": [], "sol": False})
            continue
        r = C.check(ra, rb)
        out.append({"region": [k, pg, i], "name": name, **r, "rules": C.rules_hit(r["flags"]), "n_tables": len(ra.get("tables") or []), "n_tables_hint": len((rb or {}).get("tables") or [])})
    res = {"n": len(out), "sol": sum(x["sol"] for x in out), "no_table": sum(x["status"] == "no_table" for x in out), "by_rule": {r: sum(r in x.get("rules", []) for x in out) for r in ("R1", "R2", "R3", "R4", "R5")}, "regions": out}
    (OUT9 / "b2").mkdir(parents=True, exist_ok=True)
    (OUT9 / "b2" / "check.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "regions"}, ensure_ascii=False))
    for x in out:
        print(x["name"], x["status"], x.get("rules"), x["flags"][:6])


# ---------- ②a 흉내 (RAS1 raw, 실호출 0) ----------
def cmd_sim(a):
    _, s2l = keymap()
    luna, sol = RAS1 / "c3x" / "gpt-6-luna" / "raw", RAS1 / "c3x" / "gpt-6.1-sol" / "raw"
    d = OUT9 / "b2a"
    (d / "raw").mkdir(parents=True, exist_ok=True)
    out = []
    for k, pg, i, bbox, src in regions27():
        long = f"{s2l[k]}_p{pg}_r{i}"
        name = f"{k}_p{pg}_r{i}"
        try:
            ra = json.loads((luna / f"{long}.json").read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            ra = None
        if ra is None:
            out.append({"name": name, "status": "missing", "flags": [], "sol": False})
            continue
        r = C.check(ra)  # R4 는 응답 하나뿐이라 못 돈다
        pick = ra
        if r["sol"]:
            try:
                pick = json.loads((sol / f"{long}.json").read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        (d / "raw" / f"{name}.json").write_text(json.dumps(pick, ensure_ascii=False), encoding="utf-8")
        out.append({"name": name, **r, "rules": C.rules_hit(r["flags"]), "n_tables": len(ra.get("tables") or [])})
    res = {"n": len(out), "sol": sum(x["sol"] for x in out), "no_table": sum(x["status"] == "no_table" for x in out), "by_rule": {r: sum(r in x.get("rules", []) for x in out) for r in ("R1", "R2", "R3", "R5")}, "regions": out}
    (d / "check.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "regions"}, ensure_ascii=False))
    for x in out:
        if x["status"] != "pass":
            print(x["name"], x["status"], x.get("rules"), x["flags"][:6])
    collect_and_score(d, "②a luna+검사→sol(흉내)")


# ---------- 채점 ----------
def collect(raw_dir, cand):
    """정답 항목마다 겹치는 영역(정답 넓이 10% 이상 또는 영역 넓이 50% 이상)의 items 를 모아 cand/raw/<id>.json (run.cmd_collect_region 과 같은 규칙, 짧은 키)."""
    regs = json.loads((RAS1 / "regions" / "regions.json").read_text(encoding="utf-8"))
    l2s, _ = keymap()
    for g in R.golds(GOLD / "figures"):
        k = l2s[g["report"]]
        items, src = [], []
        for i, r in enumerate(regs.get(g["report"], {}).get(str(g["page"]), [])):
            x = R.inter(r["bbox"], g["bbox"])
            if x >= 0.1 * R.area(g["bbox"]) or x >= 0.5 * R.area(r["bbox"]):
                p = raw_dir / f"{k}_p{g['page']}_r{i}.json"
                if p.is_file():
                    try:
                        items += json.loads(p.read_text(encoding="utf-8"))["items"]
                        src.append(p.stem)
                    except (json.JSONDecodeError, KeyError):
                        src.append(p.stem + ":깨짐")
        R.write_raw(cand, g["id"], items, src)


def collect_and_score(d, label):
    cand = d / "cand"
    collect(d / "raw", cand)
    ns = argparse.Namespace(gold=str(GOLD / "figures"), cand=str(cand), regions=str(RAS1 / "regions" / "regions.json"), label=label)
    R.cmd_score(ns)
    return json.loads((cand / "score.json").read_text(encoding="utf-8"))


def cmd_score(a):
    if a.site == "b2":
        chk = json.loads((OUT9 / "b2" / "check.json").read_text(encoding="utf-8"))
        combo = OUT9 / "b2" / "combo"
        (combo / "raw").mkdir(parents=True, exist_ok=True)
        for x in chk["regions"]:
            src = run_dir("b2", "sol") if x["sol"] and (run_dir("b2", "sol") / "raw" / f"{x['name']}.json").is_file() else run_dir("b2", "luna")
            p = src / "raw" / f"{x['name']}.json"
            if p.is_file():
                (combo / "raw" / f"{x['name']}.json").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
        for d, label in ((run_dir("b2", "luna"), "②b luna 기본"), (run_dir("b2", "luna", True), "②b luna 소수점"), (combo, "②b luna+검사→sol")):
            collect_and_score(d, label)
    else:
        for d, label in ((run_dir("b3", "luna"), "③ native luna"), (run_dir("b3", "sol"), "③ native sol")):
            collect_and_score(d, label)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sim")
    c = sub.add_parser("call")
    c.add_argument("site", choices=["b2", "b3"])
    c.add_argument("model", choices=["luna", "sol"])
    c.add_argument("--hint", action="store_true")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--again", action="store_true")
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--max-dpi", type=float, default=NATIVE["max_dpi"], help="③ 위 캡(발주서 물음 6: 첫 sol 토큰이 3배 넘으면 남은 셋 400)")
    k = sub.add_parser("check")
    k.add_argument("site", choices=["b2"])
    s = sub.add_parser("score")
    s.add_argument("site", choices=["b2", "b3"])
    cr = sub.add_parser("crop")
    cr.add_argument("--native", action="store_true")
    a = ap.parse_args(argv)
    {"sim": cmd_sim, "call": cmd_call, "check": cmd_check, "score": cmd_score, "crop": cmd_crop}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
