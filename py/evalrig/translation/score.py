"""번역 채점 — 용어 위반(trlab `glossary_check.violations` 그대로) · 남은 한국어 · 빠진 단위 · 검수 단계 채점.

- 용어 위반: 단위를 `{id: key, page: group, ko, en}` 짝으로 바꿔 `violations(pairs, gloss)` 를 부른다. status 가 ok 인
  단위만 넘긴다 — 빈 영문(missing) 단위는 분모·위반에서 빠진다(trlab 은 그런 짝을 위반으로 셈). `hits[].page` 는 글(group).
  엄격 = 걸린(엄격 표기 없음) 단위 / 용어가 든 단위 · 관대 = 관대 표기도 없는 단위 / 용어가 든 단위.
- 남은 한국어: 영문에 한글 음절이 하나라도 있는 단위(또는 산출이 `remainingKorean` 을 비지 않게 적은 단위) / 단위.
- 빠진 단위: 정답 열쇠 중 후보 단위가 없거나 ok 가 아닌 것 / 정답 열쇠.
- 검수 단계 채점: 검수 결과가 있을 때만 — `review_probe.read_answer`·`score_unit` 그대로(TRX7 꼴), 심은 /n · 헛걸림 /n.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ..common.match import ratio
from ..common.units import Unit, check_units

_HANGUL = re.compile(r"[가-힣]")


def as_pairs(units: Iterable[Unit]) -> list[dict[str, Any]]:
    return [{"id": u.key, "page": u.group, "ko": u.payload.get("ko") or "", "en": u.payload.get("en") or ""}
            for u in check_units(units) if u.status == "ok"]


def term_violations(units: Iterable[Unit], gloss: list[dict[str, Any]]) -> dict[str, Any]:
    from trlab.glossary_check import violations

    pairs = as_pairs(units)
    hits, occ, ok_s, ok_l = violations(pairs, gloss)
    with_term = {p["id"] for p in pairs if any(it["kr"] in p["ko"] for it in gloss)}
    strict = {h["pair"] for h in hits}
    lenient = {h["pair"] for h in hits if h["lenient_hit"]}
    return {
        "hits": hits,
        "n_hits": len(hits),
        "occurrences": sum(occ.values()),
        "strict": ratio(len(strict), len(with_term)),
        "lenient": ratio(len(lenient), len(with_term)),
        "units_with_term": len(with_term),
    }


def remaining_korean(units: Iterable[Unit]) -> dict[str, Any]:
    us = [u for u in check_units(units) if u.status == "ok"]
    bad = [u.key for u in us if _HANGUL.search(u.payload.get("en") or "") or (u.payload.get("remainingKorean") or [])]
    return {"rate": ratio(len(bad), len(us)), "keys": bad}


def missing(units: Iterable[Unit], gold_keys: Iterable[str]) -> dict[str, Any]:
    ok = {u.key for u in check_units(units) if u.status == "ok"}
    gk = list(dict.fromkeys(gold_keys))
    miss = [k for k in gk if k not in ok]
    return {"rate": ratio(len(miss), len(gk)), "keys": miss}


def review_rows(records: Iterable[dict[str, Any]], units_by_id: dict[str, dict[str, Any]],
                gloss_by_book: dict[str, list[dict[str, Any]]], variant: str) -> list[dict[str, Any]]:
    """검수 답 기록(`out/trx7/review/<판>-<모델>/*.json` 꼴) → 채점 줄. TRX7 `tx7_pick.score_all` 과 같은 셈."""
    from trlab import review_probe as R

    rows = []
    for d in records:
        if d.get("status") not in ("ok", "parse_failed"):
            rows.append({"id": d["id"], "cat": d["cat"], "split": d["split"], "status": d["status"], "flagged": False,
                         "hit": "miss" if d["cat"] != "clean" else None, "false_alarm": False, "usd": 0.0})
            continue
        u = units_by_id[d["id"]]
        mat = R.materialize(u, gloss_by_book[u["book"]])
        ans = R.read_answer(variant, d["raw"], mat)
        sc = R.score_unit(mat, ans)
        call = d.get("call", {})
        sc.update(status=d["status"], usd=call.get("costUsd", 0.0), inputTokens=call.get("inputTokens"), outputTokens=call.get("outputTokens"))
        rows.append(sc)
    return rows


def review_summary(rows: list[dict[str, Any]], split: str = "test") -> dict[str, Any]:
    rs = [r for r in rows if r.get("split") == split]
    planted = [r for r in rs if r["cat"] not in ("clean", "real")]
    real = [r for r in rs if r["cat"] == "real"]
    clean = [r for r in rs if r["cat"] == "clean"]
    return {
        "planted_span": ratio(sum(r["hit"] == "span" for r in planted), len(planted)),
        "planted_any": ratio(sum(r["hit"] in ("span", "block_only") for r in planted), len(planted)),
        "real_span": ratio(sum(r["hit"] == "span" for r in real), len(real)),
        "false_alarm": ratio(sum(bool(r["false_alarm"]) for r in clean), len(clean)),
    }
