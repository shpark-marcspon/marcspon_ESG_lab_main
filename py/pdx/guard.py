"""③ 혼합 응답에 검사·되돌림을 붙인다 (PDX1 경계 E1·E2). 실호출 없이 저장된 표 격자(`<run>/tables`)로.

검사는 정답을 보지 않는다. 모델이 낸 표 하나마다:
  cell   칸 글의 낱말(빈칸으로 끊음)마다 그 쪽 글자 층(docparse 블록 글 + 표 칸 글, 각주 표시를 뗀 칸은 표시를 붙인 글도)의
         부분 문자열인가 (`cell_check` 와 같은 정규화. 세로 병합 칸은 docparse 글에서 이어져 있지 않아 칸 통째로는 못 본다)
  mark   칸에 위첨자 문자(각주 표시)가 들어갔나 — docparse 는 각주 표시를 칸 글과 따로 둔다.
         (PDX2 G0) 칸 **끝**의 위첨자(`VOCs³⁾`·`73², ³`)는 떼어 낸 글로 나머지 검사를 하고 그 글을 남긴다.
         떼고도 위첨자가 남는 칸(글 가운데 위첨자)만 `mark` 로 건다
  number 숫자 칸(`-1,234.5%` 꼴)의 값이 그 쪽 docparse 글의 숫자 하나와 같은가(docparse 는 각주 표시를 칸 글과 따로 둔다)
  ragged 행마다 열 수가 같은가
  cover  docparse 표 하나의 글자가 그와 겹치는 모델 표들(나눠 낸 것은 묶어)의 격자에 빠짐(5% 넘게)·겹침(5% 넘게)
         없이 쓰였나 — 걸리면 그 모델 표들 모두. (PDX2 G0) 빠짐 셈에서는 docparse 표의 「한 칸만 찬 행」(소제목 꼴,
         2열 이상 표)의 글을 뺀다 — docparse 가 표 여럿과 그 사이 소제목을 한 표로 묶은 것을 모델이 나눠 낸 경우
  numcol 숫자 열(본문 칸의 60% 이상이 숫자)의 숫자 행(칸의 절반 이상이 숫자)에 한글·영문이 든 칸이 있나
걸린 표만 docparse 의 표로 되돌린다: 그 표 영역과 겹치는(작은 쪽 넓이의 절반 이상) docparse 표, 없으면 뺀다.
`--second` 를 주면 걸린 표를 먼저 두 번째 후보(같은 쪽의 겹치는 표)로 바꾸고, 그것도 걸리면 docparse 로 (E2 luna→sol).

  python3 -I guard.py --tables <run>/tables --docs samsung=<document.json>,... --out <산출> [--second <run2>/tables] [--layout 1|2]
      [--rules e]   (e: 경계 E 에서 얼린 옛 규칙 그대로 — G0 앞뒤 견주기용. 기본은 g0)
--layout 은 --docs 의 document.json 이 그 판(`parser.layout`, 없으면 1)으로 뽑힌 것인지 확인만 한다(섞이지 않게).

산출: <out>/tables/<report>_p<page>.json (table_score 로 채점), <out>/guard.json (표마다 걸린 까닭·되돌린 곳).
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)  # python -I 는 스크립트 폴더를 경로에 넣지 않는다
from cell_check import norm, page_text  # noqa: E402

NUM = re.compile(r"[-+−△▲]?\d[\d,]*(?:\.\d+)?%?")
LETTER = re.compile(r"[A-Za-z가-힣]")
COVER_TOL = 0.05
SUP = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁽⁾]")
TAIL_SUP = re.compile(r"(?:[\s,]*[⁰¹²³⁴⁵⁶⁷⁸⁹⁽⁾]+)+\s*$")
RULES = ("e", "g0")


def strip_tail(t):
    """칸 끝 위첨자만 뗀 표(새 dict). 글 가운데 위첨자는 그대로 둔다."""
    return {**t, "grid": [[TAIL_SUP.sub("", c) if c else c for c in row] for row in t["grid"]]}


def doc_layout(doc):
    return (doc.get("parser") or {}).get("layout", 1)


def numval(s):
    return re.sub(r"[,%+−△▲-]", "", s)


def overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / small if small > 0 else 0.0


class Page:
    def __init__(self, doc, page, rules="g0"):
        self.rules = rules
        marks = [
            (t["cells"][m["row"]][m["col"]] or "") + m["mark"]
            for t in doc["tables"]
            if t["page"] == page
            for m in t.get("footnote_marks", [])
        ]
        self.text = page_text(doc, page) + norm("|".join(marks))
        raw = [b.get("text", "") for b in doc["blocks"] if b["page"] == page]
        raw += [
            c
            for t in doc["tables"]
            if t["page"] == page
            for row in t["cells"]
            for c in row
            if c
        ]
        self.nums = {numval(m) for x in raw for m in NUM.findall(x)}
        self.dp = [
            {"bbox": t["bbox"], "grid": t["cells"]}
            for t in doc["tables"]
            if t["page"] == page
        ]

    def fix(self, t):
        """g0 규칙: 칸 끝 위첨자를 뗀 표. e 규칙은 그대로."""
        return strip_tail(t) if self.rules == "g0" else t

    def check(self, t):
        cells = [c for row in t["grid"] for c in row if c and c.strip()]
        why = []
        if any(norm(w) not in self.text for c in cells for w in c.split()):
            why.append("cell")
        if any(SUP.search(c) for c in cells):
            why.append("mark")
        if any(
            NUM.fullmatch(c.strip()) and numval(c.strip()) not in self.nums
            for c in cells
        ):
            why.append("number")
        if len({len(r) for r in t["grid"]}) > 1:
            why.append("ragged")
        isnum = [[bool(NUM.fullmatch(c.strip())) for c in r] for r in t["grid"]]
        body = [
            i
            for i in range(1, len(t["grid"]))
            if sum(isnum[i]) >= 0.5 * max(1, sum(bool(c.strip()) for c in t["grid"][i]))
        ]
        for j in range(max((len(r) for r in t["grid"]), default=0)):
            col = [
                (i, t["grid"][i][j].strip())
                for i in body
                if j < len(t["grid"][i]) and t["grid"][i][j].strip()
            ]
            if (
                col
                and sum(isnum[i][j] for i, _ in col) >= 0.6 * len(col)
                and any(LETTER.search(c) for _, c in col)
            ):
                why.append("numcol")
                break
        return why

    def cover(self, ts):
        """docparse 표마다 겹치는 모델 표들을 묶어 글자 빠짐·겹침을 본다. 걸린 모델 표 번호의 집합."""
        bad = set()
        for d in self.dp:
            ks = [k for k, t in enumerate(ts) if overlap(d["bbox"], t["bbox"]) >= 0.5]
            if not ks:
                continue
            want = Counter(norm("".join(c for row in d["grid"] for c in row if c)))
            got = Counter(
                norm("".join(c for k in ks for row in ts[k]["grid"] for c in row if c))
            )
            need = want
            if self.rules == "g0":
                need = Counter(
                    norm(
                        "".join(
                            c
                            for row in d["grid"]
                            if not (
                                len(row) >= 2
                                and sum(bool(x and x.strip()) for x in row) == 1
                            )
                            for c in row
                            if c
                        )
                    )
                )
            if sum((need - got).values()) > COVER_TOL * max(
                1, sum(need.values())
            ) or sum((got - want).values()) > COVER_TOL * max(1, sum(got.values())):
                bad.update(ks)
        return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--second")
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layout", type=int, choices=(1, 2), default=1)
    ap.add_argument("--rules", choices=RULES, default="g0")
    a = ap.parse_args(argv)
    docs = {
        k: json.loads(Path(v).read_text(encoding="utf-8"))
        for k, v in (x.split("=", 1) for x in a.docs.split(","))
    }
    bad = [k for k, d in docs.items() if doc_layout(d) != a.layout]
    if bad:
        sys.exit(f"--layout {a.layout} 인데 판이 다른 document.json: {bad}")
    out = Path(a.out)
    (out / "tables").mkdir(parents=True, exist_ok=True)
    log = {}
    for f in sorted(Path(a.tables).glob("*.json")):
        rep, pg = f.stem.rsplit("_p", 1)
        pg = int(pg)
        pp = Page(docs[rep], pg, a.rules)
        second = []
        if a.second and (Path(a.second) / f.name).is_file():
            second = json.loads((Path(a.second) / f.name).read_text(encoding="utf-8"))
        second = [pp.fix(t) for t in second]
        final, used_dp, used_2nd, rows = [], set(), set(), []
        ts = [pp.fix(t) for t in json.loads(f.read_text(encoding="utf-8"))]
        cover_bad, cover_2nd = pp.cover(ts), pp.cover(second)
        for i, t in enumerate(ts):
            why = pp.check(t) + (["cover"] if i in cover_bad else [])
            row = {"table": i, "why": why, "to": "keep"}
            if why:
                repl = []
                if a.second:
                    row["second_called"] = True
                    cand = [
                        k
                        for k, s in enumerate(second)
                        if overlap(s["bbox"], t["bbox"]) >= 0.5 and k not in used_2nd
                    ]
                    sw = [
                        pp.check(second[k]) + (["cover"] if k in cover_2nd else [])
                        for k in cand
                    ]
                    ok = [k for k, w in zip(cand, sw, strict=True) if not w]
                    row["second_why"] = sw
                    if cand and len(ok) == len(cand):
                        used_2nd.update(cand)
                        repl, row["to"] = [second[k] for k in cand], "second"
                if not repl:
                    ks = [
                        k
                        for k, d in enumerate(pp.dp)
                        if overlap(d["bbox"], t["bbox"]) >= 0.5 and k not in used_dp
                    ]
                    used_dp.update(ks)
                    repl, row["to"] = (
                        [pp.dp[k] for k in ks],
                        ("docparse" if ks else "drop"),
                    )
                final += repl
            else:
                final.append(t)
            rows.append(row)
        log[f.stem] = rows
        (out / "tables" / f.name).write_text(
            json.dumps(final, ensure_ascii=False), encoding="utf-8"
        )
    n = Counter(r["to"] for rs in log.values() for r in rs)
    why = Counter(w for rs in log.values() for r in rs for w in r["why"])
    pages_2nd = sum(any(r.get("second_called") for r in rs) for rs in log.values())
    summary = {
        "tables": sum(n.values()),
        "to": dict(n),
        "why": dict(why),
        "pages": len(log),
        "pages_second_called": pages_2nd,
    }
    print(json.dumps(summary, ensure_ascii=False))
    (out / "guard.json").write_text(
        json.dumps({"summary": summary, "pages": log}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
