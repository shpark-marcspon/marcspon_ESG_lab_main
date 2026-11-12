"""리스크 코드 규칙 후보 + AI 해석(발주서 MB2 §2-7). 표준 라이브러리만. 규칙·θ 는 돌리기 전에 고정.

근거: 리스크 8~90% 가 단순 추세·맥락 없음(DV[00:23:32]~), 임계값을 못 정해 「10% 이상을 크게 봄」(DV[00:28:24]~[00:28:50]),
엔진 ③ 「코드 규칙 후보 → 주석이 설명하면 거름 → AI 해석」. θ 는 실험 가정이다(「이상값」 뜻 MARC-ENT-70 은 열린 채).
규칙(전 테마): 해마다 |Δ| ≥ θ → `jump`(끝 해) · 세 해 증감 부호가 엇갈리고 둘 다 |Δ| ≥ θ → `mixed_trend`(끝 해) · 값 빈칸 → `blank`(그 해).
B0(코드만, 호출 0): 후보 가운데 메모가 빈 것 → flag. B(AI): 후보·메모·회사 머리를 주고 cid 마다 flag|explained|not_risk.
"""
from __future__ import annotations

import json
from typing import Any

THETA = 0.10  # 인터뷰 말 그대로 「10% 이상」
SENSITIVITY = (0.10, 0.20, 0.30)
VERDICTS = ("flag", "explained", "not_risk")
UNANSWERED = "unanswered"


def _rows(inp: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for theme in inp["data"].values() for r in theme]


def candidates(inp: dict[str, Any], theta: float = THETA) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in _rows(inp):
        ys = sorted(r["values"])
        vs = [r["values"][y] for y in ys]
        note = (r.get("메모") or "").strip() or None
        base = {"id": r["ID"], "theme": r.get("테마"), "note": note}
        for y, v in zip(ys, vs):
            if v is None or v == "":
                out.append({**base, "cid": f"{r['ID']}:blank:{y}", "rule": "blank", "years": [y], "values": [None]})
        deltas = []
        for i in range(1, len(ys)):
            p, v = vs[i - 1], vs[i]
            if p in (None, "") or v in (None, "") or float(p) == 0:
                deltas.append(None)
                continue
            d = (float(v) - float(p)) / abs(float(p))
            deltas.append(d)
            if abs(d) >= theta - 1e-12:
                out.append({**base, "cid": f"{r['ID']}:jump:{ys[i]}", "rule": "jump", "years": [ys[i - 1], ys[i]],
                            "values": [p, v], "delta": round(d, 4)})
        for i in range(1, len(deltas)):
            a, b = deltas[i - 1], deltas[i]
            if a is not None and b is not None and a * b < 0 and abs(a) >= theta - 1e-12 and abs(b) >= theta - 1e-12:
                out.append({**base, "cid": f"{r['ID']}:mixed_trend:{ys[i + 1]}", "rule": "mixed_trend",
                            "years": ys[i - 1:i + 2], "values": vs[i - 1:i + 2], "delta": [round(a, 4), round(b, 4)]})
    return out


def b0_flags(cands: list[dict[str, Any]]) -> set[str]:
    return {c["id"] for c in cands if not c["note"]}


SYSTEM = (
    "너는 ESG 공시 데이터의 리스크를 보는 검토자다. 코드가 규칙(전년 대비 변동률 |Δ| ≥ θ, 증감이 엇갈림, 값 빈칸)으로 고른 "
    "후보마다, 그것이 설명이 필요한 데이터 리스크인지 판단한다. 판단은 셋 중 하나다: "
    "flag = 설명 없이 공시하면 이해관계자가 의문을 가질 리스크, explained = 메모가 그 변동·빈칸을 설명한다, "
    "not_risk = 리스크가 아니다(규칙이 잘못 걸림). 후보에 없는 것을 새로 만들지 말고, 모든 cid 에 답하고, 이유는 한 문장으로."
)


def render_request(cands: list[dict[str, Any]], company: dict[str, Any], theta: float = THETA) -> tuple[str, str]:
    head = {k: company.get(k) for k in ("name", "sasbIndustry", "materialTopics", "reportingYear")}
    items = [{k: c.get(k) for k in ("cid", "id", "theme", "rule", "years", "values", "delta", "note")} for c in cands]
    user = (f"회사: {json.dumps(head, ensure_ascii=False)}\n규칙 θ = {theta:.0%}\n후보({len(items)}개, note = 그 행의 메모):\n"
            f"{json.dumps(items, ensure_ascii=False, indent=1)}\n\n"
            '답은 JSON 하나: {"items": [{"cid": "...", "verdict": "flag|explained|not_risk", "reason": "..."}]}')
    return SYSTEM, user


def check_answer(answer: dict[str, Any] | None, cands: list[dict[str, Any]]) -> dict[str, Any]:
    """없는 cid·겹친 cid·모르는 판정은 버림, 빠진 cid 는 미응답(not_risk 로 세지 않음)."""
    known = {c["cid"] for c in cands}
    got: dict[str, str] = {}
    dropped = {"unknown_cid": 0, "duplicate_cid": 0, "bad_verdict": 0}
    seen: set[str] = set()
    for it in (answer or {}).get("items", []):
        cid = it.get("cid")
        if cid not in known:
            dropped["unknown_cid"] += 1
            continue
        if cid in seen:
            dropped["duplicate_cid"] += 1
            got.pop(cid, None)
            continue
        seen.add(cid)
        if it.get("verdict") not in VERDICTS:
            dropped["bad_verdict"] += 1
            continue
        got[cid] = it["verdict"]
    verdicts = {cid: got.get(cid, UNANSWERED) for cid in sorted(known)}
    flagged = {c["id"] for c in cands if verdicts[c["cid"]] == "flag"}
    return {"verdicts": verdicts, "flagged": sorted(flagged), "dropped": dropped,
            "unanswered": sum(1 for v in verdicts.values() if v == UNANSWERED)}


def as_conv(ids: set[str] | list[str]) -> dict[str, Any]:
    """flag 를 낸 ID 집합 → `score._risk` 가 읽는 단위 꼴(인사이트 행 한 줄에 ID 하나)."""
    from evalrig.common.units import Origin, Unit
    from evalrig.dataverify import convert_156

    o = Origin(engine="v2", schema="mb2.riskrules", run="mb2", converter="riskrules@1")  # 공통 ENGINES = v1·v2 — 랩 판은 v2 쪽
    return {"reviewStatus": "done", "units": [
        Unit(module="dataverify", unit=convert_156.INSIGHT_UNIT, key=f"r{n}", group="", origin=o,
             payload={"indicatorText": i, "id": i, "year": {"status": "absent"}, "verdict": "flag", "area": ""})
        for n, i in enumerate(sorted(ids))]}
