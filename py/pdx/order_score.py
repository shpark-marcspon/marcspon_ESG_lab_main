"""읽는 순서 채점 (PDX1, MARC-TICK-339). 표준 라이브러리만.

정답(`prework/gold-order/<report>_p<page>.json`)의 단위(제목·문단·상자·각주…)마다 머리 글(s), 끝 글(e), 가운데 글(m, 위첨자·줄바꿈이
섞이기 쉬운 자리)을 적어 두고, 후보가 낸 그 쪽의 글 흐름(`<streams>/<report>_p<page>.txt`, 후보의 읽는 차례대로 이은 글)에서 찾는다.
글은 NFKC 뒤 공백을 다 지우고 가운뎃점류를 하나로 모아 견준다. 후보의 블록 경계는 보지 않는다(흐름만 본다).

지표 (쪽마다, 합계는 단위 수 가중):
  found        머리 글이 흐름에 있는 단위 수. missing = 단위 수 - found
  dup          머리 글이 흐름에 두 번 이상 나오는 단위 수 (겹쳐 뽑음)
  kendall_tau  찾은 단위들의 (정답 차례, 흐름 속 첫 자리) 순위 상관. 1 이면 차례가 다 맞음
  adj_pair     정답에서 이웃한 두 단위(둘 다 찾음) 중 흐름에서도 앞뒤가 맞는 쌍의 비율
  intact       머리 → 가운데 글들 → 끝 글이 그 차례로 이어지고, 그 사이에 다른 단위의 머리 글이 끼지 않은 단위 수
               (문단이 끊기거나 다른 단과 섞이지 않았다). 분모는 단위 수

python3 order_score.py --gold <정답 폴더> --streams <흐름 폴더> [--json <파일>]
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

DOTS = re.compile(r"[∙·•‧ㆍ・]")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\s+", "", s)
    return DOTS.sub("·", s)


def positions(stream, key):
    out, i = [], stream.find(key)
    while i >= 0:
        out.append(i)
        i = stream.find(key, i + 1)
    return out


def kendall(pairs):
    """pairs: 정답 차례대로 늘어놓은 흐름 자리 목록. 같은 자리는 없다고 본다."""
    n = len(pairs)
    if n < 2:
        return None
    c = d = 0
    for i in range(n):
        for j in range(i + 1, n):
            if pairs[j] > pairs[i]:
                c += 1
            elif pairs[j] < pairs[i]:
                d += 1
    return round((c - d) / (n * (n - 1) / 2), 4)


def score_page(gold, stream_text):
    stream = norm(stream_text)
    units = gold["units"]
    starts = []
    for u in units:
        p = positions(stream, norm(u["s"]))
        starts.append(p)
    first = [p[0] if p else None for p in starts]
    found = [f for f in first if f is not None]
    tau = kendall(found)
    adj_ok = adj_n = 0
    for a, b in zip(first, first[1:], strict=False):
        if a is not None and b is not None:
            adj_n += 1
            adj_ok += b > a
    intact = 0
    broken = []
    for k, u in enumerate(units):
        if first[k] is None:
            broken.append({"unit": k, "s": u["s"], "why": "머리 없음"})
            continue
        cur = first[k] + len(norm(u["s"]))
        ok = True
        for key in [*u.get("m", []), *([u["e"]] if u.get("e") else [])]:
            j = stream.find(norm(key), first[k])
            if j < 0:
                ok = False
                broken.append({"unit": k, "s": u["s"], "why": f"없음: {key}"})
                break
            cur = max(cur, j + len(norm(key)))
        if ok:
            inter = [j for j, f in enumerate(first) if j != k and f is not None and first[k] < f < cur - 1]
            if inter:
                ok = False
                broken.append({"unit": k, "s": u["s"], "why": "다른 단위가 끼어듦: " + ",".join(units[j]["s"] for j in inter)})
        intact += ok
    return {
        "units": len(units),
        "found": len(found),
        "missing": len(units) - len(found),
        "dup": sum(1 for p in starts if len(p) > 1),
        "kendall_tau": tau,
        "adj_pair": round(adj_ok / adj_n, 4) if adj_n else None,
        "adj_n": adj_n,
        "intact": intact,
        "broken": broken,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--streams", required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    res = {}
    for g in sorted(Path(a.gold).glob("*.json")):
        gold = json.loads(g.read_text(encoding="utf-8"))
        sp = Path(a.streams) / f"{g.stem}.txt"
        if not sp.is_file():
            print(f"흐름 없음: {sp}", file=sys.stderr)
            continue
        res[g.stem] = score_page(gold, sp.read_text(encoding="utf-8"))
    n = sum(r["units"] for r in res.values())
    tot = {
        "pages": len(res),
        "units": n,
        "found": sum(r["found"] for r in res.values()),
        "missing": sum(r["missing"] for r in res.values()),
        "dup": sum(r["dup"] for r in res.values()),
        "intact": sum(r["intact"] for r in res.values()),
        "intact_rate": round(sum(r["intact"] for r in res.values()) / n, 4) if n else None,
        "kendall_tau_mean": round(sum(r["kendall_tau"] or 0 for r in res.values()) / len(res), 4) if res else None,
        "adj_pair": round(
            sum((r["adj_pair"] or 0) * r["adj_n"] for r in res.values()) / max(1, sum(r["adj_n"] for r in res.values())), 4
        ),
    }
    print("| 쪽 | 단위 | 찾음 | 빠짐 | 겹침 | Kendall τ | 이웃 쌍 | 온전 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, r in res.items():
        print(f"| {k} | {r['units']} | {r['found']} | {r['missing']} | {r['dup']} | {r['kendall_tau']} | {r['adj_pair']} | {r['intact']} |")
    print(
        f"| **합계** | {tot['units']} | {tot['found']} | {tot['missing']} | {tot['dup']} | {tot['kendall_tau_mean']} (평균) | "
        f"{tot['adj_pair']} | {tot['intact']} ({tot['intact_rate']}) |"
    )
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps({"pages": res, "all": tot}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
