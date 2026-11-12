"""T-a 표: 책 × 언어 × (코드만 / luna / 코드+luna) 점수와 토픽 짝(KR·EN 범위가 같은 PDF 쪽) %. AI 0(산출만 읽음).

명령: python -m trlab.ta_report → OUT/toc/summary.json 과 마크다운 표
"""

import json

from trlab.common import BOOKS, GOLD, OUT

MODES = {"code": "{k}-{l}.json", "luna": "{k}-{l}-luna.json", "code+luna": "{k}-{l}-combined.json"}


def pair_pct(k, mode):
    a = json.load(open(OUT / "toc" / MODES[mode].format(k=k, l="ko")))["score"]["rows"]
    b = json.load(open(OUT / "toc" / MODES[mode].format(k=k, l="en")))["score"]["rows"]
    ok = n = 0
    for x, y in zip(a, b):
        n += 1
        px, py = x.get("pred"), y.get("pred")
        ok += bool(px and py and px["start_page"] and px["start_page"] == py["start_page"] and px["end_page"] == py["end_page"])
    return round(100 * ok / n, 1)


def main():
    rows, out = [], {}
    for k in BOOKS:
        role = "개발" if k == "sam" else "채점"
        for mode in MODES:
            for l in ("ko", "en"):
                p = OUT / "toc" / MODES[mode].format(k=k, l=l)
                if not p.exists():
                    continue
                s = json.load(open(p))["score"]
                out[f"{k}-{l}-{mode}"] = {x: v for x, v in s.items() if x != "rows"}
                mid = s.get("mid", {}) if mode == "code" else {}
                rows.append(f"| {k} ({role}) | {l} | {mode} | {'예' if s['toc_found'] else '아니오'} | {s['extracted_pct']} | "
                            f"{s['block_ok_pct']} | {s['start_ok_pct']} / {s['start_pm1_pct']} | {s['end_ok_pct']} / {s['end_pm1_pct']} | "
                            + (f"{mid.get('recall')} / {mid.get('precision')} | {mid.get('per_item')}" if mid else "– | –") + " |")
            try:
                out[f"{k}-pair-{mode}"] = pair_pct(k, mode)
            except FileNotFoundError:
                pass
    json.dump(out, open(OUT / "toc" / "summary.json", "w"), ensure_ascii=False, indent=1)
    print("| 책 | 언어 | 판 | 목차 쪽 | 항목 뽑힘 % | 항목→시작 제목 % | 시작 쪽 정확/±1 % | 끝 쪽 정확/±1 % | 중제목 재현/정밀 | 목차에 없는 중제목/항목 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print()
    print("토픽 짝(KR 범위 = EN 범위) %:", {k: v for k, v in out.items() if "-pair-" in k})


if __name__ == "__main__":
    main()
