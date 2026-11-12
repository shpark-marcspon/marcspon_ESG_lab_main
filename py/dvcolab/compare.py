"""코랩 쪽과 엔진 쪽 결과(`dvcolab.result/1`)를 견준다.

- 사례(calls): id 마다 `outcome` 과 `value` 를 `json.dumps(ensure_ascii=False)` 글로 같음 본다(키 차례·NaN 포함).
- 프롬프트: system·user 를 바이트로 본다. 다르면 줄 단위 hunk 를 내고 `known.classify` 로 알려진 것과 모르는 것을 가른다.
- 상수: json 글로 같음 본다. 코랩 쪽 기대 사례(expectations)가 어긋나면 그것도 모르는 차이다.
- 고리(loops, DV1b): id 마다 칸 다섯(executed·outputs·providerCalls·ending·text)을 json 글로 같음 본다. 알려진 차이는
  0 이 기대값이다(사례를 그렇게 지었다 — cases.py 머리). 한쪽에만 있는 고리도 모르는 차이다.
"""

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

from dvcolab.known import KNOWN, NO_HUNK, Hunk, classify

FORMAT = "dvcolab.result/1"


class ResultFormatError(ValueError):
    pass


def check_result(doc: Any, side: str) -> dict[str, Any]:
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ResultFormatError(f"{side}: 꼴이 {FORMAT} 가 아니다")
    for key in ("calls", "prompts", "constants"):
        if key not in doc:
            raise ResultFormatError(f"{side}: {key} 칸이 없다")
    return doc


def text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass
class Report:
    calls: int = 0
    call_diffs: list[dict[str, Any]] = field(default_factory=list)
    known: list[tuple[str, Hunk]] = field(default_factory=list)
    unknown_hunks: list[Hunk] = field(default_factory=list)
    constant_diffs: list[str] = field(default_factory=list)
    expectation_fails: list[str] = field(default_factory=list)
    loops: int = 0
    loop_diffs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def unknown(self) -> int:
        return (
            len(self.call_diffs)
            + len(self.unknown_hunks)
            + len(self.constant_diffs)
            + len(self.expectation_fails)
            + len(self.loop_diffs)
        )


def hunks(prompt: str, part: str, colab: str, engine: str) -> list[Hunk]:
    a, b = colab.split("\n"), engine.split("\n")
    out: list[Hunk] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append(Hunk(prompt, part, tag, i1 + 1, j1 + 1, tuple(a[i1:i2]), tuple(b[j1:j2])))
    return out


def compare(colab: dict[str, Any], engine: dict[str, Any]) -> Report:
    report = Report()
    right = {c["id"]: c for c in engine["calls"]}
    seen: set[str] = set()
    for c in colab["calls"]:
        report.calls += 1
        seen.add(c["id"])
        e = right.get(c["id"])
        if e is None:
            report.call_diffs.append({"id": c["id"], "colab": c, "engine": None})
        elif (c["outcome"], text(c["value"])) != (e["outcome"], text(e["value"])):
            report.call_diffs.append({"id": c["id"], "colab": c, "engine": e})
    for cid in right:
        if cid not in seen:
            report.call_diffs.append({"id": cid, "colab": None, "engine": right[cid]})
    theirs = {p["id"]: p for p in colab["prompts"]}
    ours_ids = {p["id"] for p in engine["prompts"]}
    report.constant_diffs += [f"프롬프트 {pid}: 엔진 쪽 없음" for pid in theirs if pid not in ours_ids]
    for p in engine["prompts"]:
        q = theirs.get(p["id"])
        if q is None:
            report.constant_diffs.append(f"프롬프트 {p['id']}: 코랩 쪽 없음")
            continue
        for part in ("system", "user"):
            if q[part] == p[part]:
                continue
            for h in hunks(p["id"], part, q[part], p[part]):
                known = classify(h)
                if known is None:
                    report.unknown_hunks.append(h)
                else:
                    report.known.append((known.key, h))
    for key in sorted(set(colab["constants"]) | set(engine["constants"])):
        a, b = colab["constants"].get(key), engine["constants"].get(key)
        if text(a) != text(b):
            report.constant_diffs.append(key)
    report.expectation_fails = [x["id"] for x in colab.get("expectations", []) if not x["held"]]
    compare_loops(report, colab.get("loops", []), engine.get("loops", []))
    return report


LOOP_FIELDS = ("executed", "outputs", "providerCalls", "ending", "text")


def compare_loops(report: Report, colab: list[dict[str, Any]], engine: list[dict[str, Any]]) -> None:
    right = {e["id"]: e for e in engine}
    seen: set[str] = set()
    for c in colab:
        report.loops += 1
        seen.add(c["id"])
        e = right.get(c["id"])
        if e is None:
            report.loop_diffs.append({"id": c["id"], "fields": ["엔진 쪽 없음"]})
            continue
        fields = [key for key in LOOP_FIELDS if text(c.get(key)) != text(e.get(key))]
        if fields:
            report.loop_diffs.append({"id": c["id"], "fields": fields, "colab": c, "engine": e})
    for lid in right:
        if lid not in seen:
            report.loop_diffs.append({"id": lid, "fields": ["코랩 쪽 없음"]})


def known_table(report: Report) -> list[tuple[str, str, int]]:
    counts = {k.key: 0 for k in KNOWN}
    for key, _ in report.known:
        counts[key] += 1
    rows = [(k.what, k.dv1a, counts[k.key]) for k in KNOWN]
    rows += [(what, ref, 0) for what, ref in NO_HUNK]
    return rows


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def render_md(report: Report, meta: dict[str, str]) -> str:
    """diff.md. 알려진 hunk 는 우리 쪽 자리와 규칙만 적는다(코랩 쪽 글을 옮기지 않는다). 모르는 차이는 두 쪽을 다 적는다 —
    고칠 자리를 찾아야 하므로. 이 파일은 git 밖(out/)에만 쓴다."""
    lines = ["# 데이터 검증 코랩 대조", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += [f"- 사례: {report.calls}", f"- 고리 사례: {report.loops}", f"- 알려진 차이: {len(report.known)}",
              f"- 모르는 차이: {report.unknown}", ""]  # fmt: skip
    lines += ["## 알려진 차이", "", "| 항목 | DV1a 다른 자리 | 건수 |", "|---|---|---|"]
    lines += [f"| {_cell(w)} | {r} | {n} |" for w, r, n in known_table(report)]
    lines.append("")
    for key, h in report.known:
        lines.append(f"- {key}: {h.prompt}.{h.part} 엔진 {h.engine_at}줄부터 {len(h.engine)}줄(코랩 {h.colab_at}줄)")
    lines += ["", "## 모르는 차이", ""]
    if not report.unknown:
        lines += ["없음 — 0.", ""]
        return "\n".join(lines)
    for d in report.call_diffs:
        lines.append(f"- 사례 {d['id']}: 코랩 {text(d['colab'])} · 엔진 {text(d['engine'])}")
    for h in report.unknown_hunks:
        lines.append(f"- 프롬프트 {h.prompt}.{h.part} {h.tag} 코랩 {h.colab_at}줄 · 엔진 {h.engine_at}줄")
        lines += [f"  - 코랩: {line}" for line in h.colab]
        lines += [f"  - 엔진: {line}" for line in h.engine]
    lines += [f"- 상수 {key}" for key in report.constant_diffs]
    lines += [f"- 기대 사례 어긋남 {x}" for x in report.expectation_fails]
    for d in report.loop_diffs:
        lines.append(f"- 고리 {d['id']}: 칸 {', '.join(d['fields'])}")
        for key in d["fields"]:
            if "colab" in d:
                lines.append(f"  - {key} 코랩 {text(d['colab'].get(key))} · 엔진 {text(d['engine'].get(key))}")
    lines.append("")
    return "\n".join(lines)
