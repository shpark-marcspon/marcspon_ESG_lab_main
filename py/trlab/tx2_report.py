"""TRX2 표: G0 후보 재현율·간지 판정 · G1 목차 읽기 정확 · G2 판 여섯(책 × 언어 × 판). AI 0(산출만 읽음).

명령: python -m trlab.tx2_report → OUT2/summary.json 과 마크다운 표
"""

import json

from trlab.glyphs import OUT2

BOOKS = [("nb", "ko", "블라인드"), ("cos", "ko", "반쯤 본"), ("cos", "en", "반쯤 본"), ("kb", "ko", "반쯤 본"),
         ("kb", "en", "반쯤 본"), ("sam", "ko", "개발"), ("sam", "en", "개발")]
MODES = [("a", "코드만"), ("b", "TRX1 luna"), ("c", "G1+코드"), ("d", "속성+AI luna"), ("e", "속성 뺀 AI luna"),
         ("dsol", "속성+AI sol")]


def _j(p):
    return json.load(open(p)) if p.exists() else None


def main():
    out = {"g0": {}, "g1": {}, "g2": {}}
    g0 = ["| 책 | 처지 | 언어 | 쪽당 후보 평균/중앙/최대 | TRX2 후보 재현 전체 | 간지 항목 | 아닌 항목 | TRX1 heading_cands(±1) | TRX1 luna 후보 | 간지 판정 재현/정밀 | 크기 일치 최빈/첫 글자/어떤 글자 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    g1 = ["| 책 | 처지 | 언어 | 목차 쪽(찾음) | G1 항목 뽑힘 | 번호 맞음 | 수준 맞음 | G1 항목 수 | 코드 뽑힘 | 코드 번호 | 코드 항목 수 | USD |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    g2 = ["| 책 | 처지 | 언어 | 판 | 목차 쪽 | 항목 뽑힘 % | **항목→시작 제목 %** | A 시작 정확/±1 | A 끝 정확/±1 | B 시작 정확/±1 | B 끝 정확/±1 | 간지 항목 A/B 시작 | 메움 | 호출 USD |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda r: f"{r['hit']}/{r['n']}" + (f" ({r['pct']})" if r["pct"] is not None else "")
    for k, l, role in BOOKS:
        c = _j(OUT2 / "cands" / f"{k}-{l}.json")
        a = _j(OUT2 / "attrs" / f"{k}-{l}.json")
        if c and "recall" in c:
            r = c["recall"]
            ck = a["check"]
            out["g0"][f"{k}-{l}"] = {"per_page": c["per_page"], "recall": r, "divider": c["divider"], "check": ck}
            g0.append(f"| {k} | {role} | {l} | {c['per_page']['mean']}/{c['per_page']['median']}/{c['per_page']['max']} | "
                      f"{f(r['trx2']['all'])} | {f(r['trx2']['divider'])} | {f(r['trx2']['other'])} | "
                      f"{f(r['trx1_heading_cands']['all'])} | {f(r['trx1_luna_cands']['all'])} | "
                      f"{c['divider']['recall']}/{c['divider']['precision']} | "
                      f"{ck['size_match']}/{ck['size_match_first']}/{ck['size_match_any']} |")
        v = _j(OUT2 / "g1" / f"{k}-{l}.json")
        if v and v.get("score"):
            s, cd = v["score"]["g1"], v["score"]["code"]
            out["g1"][f"{k}-{l}"] = v["score"]
            g1.append(f"| {k} | {role} | {l} | {v['pages']} ({'예' if v['score']['found_pages'] else '아니오'}) | "
                      f"{s['found']}/{s['n']} ({s['found_pct']}) | {s['printed_ok_pct']} | {s['level_ok_pct']} | {s['items']} | "
                      f"{cd['found_pct']} | {cd['printed_ok_pct']} | {cd['items']} | {v['call']['costUsd']:.4f} |")
        for m, name in MODES:
            p = _j(OUT2 / "g2" / f"{k}-{l}-{m}.json")
            if not p:
                continue
            s, vw = p["score"], p["views"]
            al, dv = vw["all"], vw["divider"]
            usd = p.get("usd")
            if m == "b" and k != "nb":
                usd_s = "TRX1 저장본"
            else:
                usd_s = f"{usd:.4f}" if usd else "0"
            out["g2"][f"{k}-{l}-{m}"] = {"score": {x: y for x, y in s.items() if x != "rows"}, "views": vw,
                                         "filled": p.get("filled"), "usd": usd}
            dvs = f"{dv['A_start']}/{dv['B_start']}" if dv["n"] else "– (간지 0, A=B)"
            g2.append(f"| {k} | {role} | {l} | {name} | {'예' if s['toc_found'] else '아니오'} | {s['extracted_pct']} | "
                      f"**{s['block_ok_pct']}** | {al['A_start']}/{al['A_start_pm1']} | {al['A_end']}/{al['A_end_pm1']} | "
                      f"{al['B_start']}/{al['B_start_pm1']} | {al['B_end']}/{al['B_end_pm1']} | {dvs} | "
                      f"{p.get('filled') if p.get('filled') is not None else '–'} | {usd_s} |")
    calls = [json.loads(x) for x in open(OUT2 / "calls.jsonl")] if (OUT2 / "calls.jsonl").exists() else []
    site = {}
    for x in calls:
        t = x["tag"]
        key = "G1 sol" if t.startswith("TRX2/G1/") else ("G2 sol" if x["model"] == "gpt-6.1-sol" else "G2 luna")
        site[key] = round(site.get(key, 0) + x.get("costUsd", 0), 4)
    out["usd"] = {"by_site": site, "total": round(sum(x.get("costUsd", 0) for x in calls), 4), "calls": len(calls)}
    json.dump(out, open(OUT2 / "summary.json", "w"), ensure_ascii=False, indent=1)
    print("### G0 후보\n" + "\n".join(g0) + "\n\n### G1 목차 읽기\n" + "\n".join(g1) + "\n\n### G2 판\n" + "\n".join(g2))
    print("\n실호출:", json.dumps(out["usd"], ensure_ascii=False))


if __name__ == "__main__":
    main()
