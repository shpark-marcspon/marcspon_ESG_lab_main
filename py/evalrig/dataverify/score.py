"""데이터 검증 채점기 — EVA 공통 `match.ratio`·`unmeasurable` 위(발주서 EVB §2-10). 셈은 코드, 모델 0.

검산(가상 기업 정답):
- 짝 규칙: 정답 (ID, 연도) 가 결과 FAIL 단위의 items × {year} 에 든다.
- 재현 = 맞힌 FAIL 정답 / FAIL 정답(항목이 하나라도 풀린 FAIL 단위 전부에서) · 종류별 재현.
- 정밀 = 정답 FAIL(또는 accept 가 FAIL 을 받는 칸)을 하나라도 품은 완전 해석 FAIL 단위 / 완전 해석 FAIL 단위.
  완전 해석 = items 가 있고 unresolved 가 빔. 부분 미해석(items 있고 unresolved 있음)은 정밀 분모 밖 — 수는
  `failUnitsPartiallyUnresolved`, 그중 정답 FAIL 을 품은 수는 `partialWithGoldFail`(MARC-ISSUE-623).
  항목이 하나도 안 풀린 FAIL 단위 수는 `failUnitsUnresolved`(정밀·재현 밖, 따로).
리스크 짝(리스크 정답): 인사이트 행의 ID 로 — flag_no_note 재현 · explained 를 낸 비(거짓 양성) · 「주석 없음만 냄」 짝 비.
엑셀↔PDF 정답: 1차에 대사가 없어 `not_applicable`(정답 meta.v1Score).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from evalrig.common import gold as common_gold
from evalrig.common import match as M
from evalrig.dataverify import units as DU  # noqa: F401 — import 때 items 꼴을 공통에 등록


def score(gs: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    common_gold.check(gs)
    head = {"setId": gs["setId"], "coverage": gs["coverage"], "reviewStatus": conv.get("reviewStatus"),
            "unresolved": conv.get("unresolved")}
    v1 = (gs.get("meta") or {}).get("v1Score")
    if v1 and v1.get("status") == "not_applicable":
        return {**head, **v1}
    labels = set(gs.get("conventions", {}).get("labels", []))
    if "flag_no_note" in labels:
        return {**head, **_risk(gs, conv)}
    return {**head, **_checks(gs, conv)}


def _checks(gs: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    fails = [u.payload for u in conv["units"] if u.unit.endswith("check_year@1") and u.payload["verdict"] == "FAIL"]
    gold_fail = {(it["indicator"], it["year"]): it for it in gs["items"] if it["verdict"] == "FAIL"}
    accept_fail = {(it["indicator"], it["year"]) for it in gs["items"] if "FAIL" in it.get("accept", [])}

    def cells(u):
        return {(i, u["year"]) for i in u["items"]}

    resolved = [u for u in fails if u["items"]]  # 재현은 항목이 하나라도 풀린 단위 전부에서
    full = [u for u in resolved if not u.get("unresolved")]  # 정밀 분모: 완전 해석만(623)
    partial = [u for u in resolved if u.get("unresolved")]
    hit = set()
    for u in resolved:
        hit |= cells(u) & set(gold_fail)
    good = sum(1 for u in full if cells(u) & (set(gold_fail) | accept_fail))
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for k, it in gold_fail.items():
        by_kind[it["kind"]][1] += 1
        by_kind[it["kind"]][0] += k in hit
    out = {
        "recall": M.ratio(len(hit), len(gold_fail)),
        "precision": M.ratio(good, len(full)) if common_gold.precision_allowed(gs) else M.unmeasurable("partial"),
        "byKind": {k: M.ratio(*v) for k, v in sorted(by_kind.items())},
        "failUnits": len(fails),
        "failUnitsUnresolved": len(fails) - len(resolved),
        "failUnitsPartiallyUnresolved": len(partial),
        "partialWithGoldFail": sum(1 for u in partial if cells(u) & set(gold_fail)),
    }
    if conv.get("reviewStatus") != "done":
        out["note"] = f"review.status={conv.get('reviewStatus')} — 점수는 실패 전까지의 검산만"
    return out


def _risk(gs: dict[str, Any], conv: dict[str, Any]) -> dict[str, Any]:
    flagged = {u.payload["id"] for u in conv["units"] if u.unit.endswith("insight_row@1") and u.payload["id"]}
    lab = {it["indicator"]: it for it in gs["items"]}
    n = defaultdict(int)
    f = defaultdict(int)
    for i, it in lab.items():
        n[it["verdict"]] += 1
        f[it["verdict"]] += i in flagged
    pairs = defaultdict(dict)
    for it in gs["items"]:
        if it.get("pair"):
            pairs[it["pair"]][it["verdict"]] = it["indicator"] in flagged
    only_bare = sum(1 for p in pairs.values() if p.get("flag_no_note") and not p.get("explained"))
    return {
        "flagNoNoteRecall": M.ratio(f["flag_no_note"], n["flag_no_note"]),
        "explainedFlagged": M.ratio(f["explained"], n["explained"]),
        "noneFlagged": M.ratio(f["none"], n["none"]),
        "onlyNoNotePairs": M.ratio(only_bare, len(pairs)),
        "insightRows": sum(1 for u in conv["units"] if u.unit.endswith("insight_row@1")),
    }

