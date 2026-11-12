"""그린워싱 정답 셋 — ① 지은 표본(git) · ② SG1 주장 변환(git 밖). 머리는 EVA 공통 `evalrig.gold@1`.

① `sample_gold()`: `greenwash/tests/sample_text.py` 의 지은 네 쪽, 쪽마다 정규화한 쪽 글 전체를
   `split_sentences_kor` 로 가른 **모든 문장**에 라벨 하나(High|Mid|Low|NonRisk). 같은 (쪽, sha12) 가
   여러 번 나오면 한 줄로 접고 `occurrences` 에 센다(쪽 4 의 되풀이 문장). coverage exhaustive.
② `from_sg1(queries, claim_expected, document)`: SG1 의 `type:"greenwash"` 주장(권마다 15)을 (쪽, 문장, 판정)으로.
   판정 unsupported → `suspect`, supported → `supported`, partially_supported → `partial`(재현 분모 밖 — 감독 답).
   등급은 없다(`absent`). coverage partial. 실재 글이 들어 있으므로 산출은 git 밖(`out/evb/gold/`)에만.
"""
from __future__ import annotations

import re
from typing import Any

from evalrig.common import gold as common_gold
from evalrig.greenwash import units as U

AUTHOR = "runner-EVB (MARC-TICK-553)"
MADE_ON = "2026-11-09"
REVIEW_NONE = {"status": "none", "by": None, "at": None, "note": "러너가 지음. 사람(컨설턴트) 검토 0", "sample": None}
PENDING = [
    {"what": "컨설턴트가 쓰는 그린워싱 정리 엑셀과 그 정답", "question": "MARC-ENT-607", "slot": "consultant"},
    {"what": "정확도 합격 기준·정답셋·기존 컨설팅 오류 기록", "question": "MARC-ENT-8", "slot": "consultant"},
]
CATEGORY_NOTE = "카테고리 목록 미수령(MARC-ENT-612) — 모든 줄 category absent"

RULES = {
    "R1": "High — 범위 전체를 단정하는 검증 없는 환경 주장(「100%」「모든」「무공해」「탄소중립 기업」처럼 회사·제품 전체를 못박음).",
    "R2": "Mid — 뜻이 넓은 친환경 꼬리표(eco·그린·청정·친환경을 향한·유기농)나 내용·근거가 없는 달성 주장.",
    "R3": "Low — 환경 개선의 의지·계획·노력 선언(추구·노력·지향·~하고자·계획·확대해 나가겠습니다). 수치·검증 없음.",
    "R4": "NonRisk — 환경 주장이 아니거나, 수치·기준·검증이 붙은 사실·목표(기준연도·감축률이 다 있는 목표 포함).",
}
# 지은 표본의 라벨표: (쪽, 문장에 든 글자) → (라벨, 규칙, accept). 표에 없는 문장은 R4 NonRisk.
# 표의 줄은 그 쪽에서 적어도 한 문장에 걸려야 한다(시험 T3 — 낡은 줄 0).
SAMPLE_LABELS: list[tuple[int, str, str, str, list[str]]] = [
    (1, "탄소 배출을 줄이는 경영을 추구", "Low", "R3", []),
    (1, "2020년 대비 40% 줄이는 것을 목표로", "NonRisk", "R4", ["Low"]),
    (1, "모든 사업장에서 재생에너지 사용을 확대", "Low", "R3", []),
    (1, "제3자 검증을 받았습니다", "NonRisk", "R4", []),
    (1, "친환경을 향한 걸음", "Mid", "R2", ["Low"]),
    (2, "eco프리 라인과 유기농 원료 라인", "Mid", "R2", []),
    (2, "100% 무공해 공정을 갖춘 탄소중립 기업", "High", "R1", []),
    (2, "지속가능한 성장 기반", "NonRisk", "R4", ["Mid"]),
    (2, "재활용 원료 비율을 높이고자", "Low", "R3", []),
    (3, "감축 경로를 수립하였습니다", "Mid", "R2", ["Low"]),
    (3, "재생에너지 전환을 지속적으로 추진", "Low", "R3", ["NonRisk"]),
    (3, "재활용률을 높이기 위해 노력", "Low", "R3", []),
    (3, "모든 제품은 친환경 제품", "High", "R1", []),
    (3, "온실가스 감축에 기여하고자", "Low", "R3", []),
    (3, "2025년에 도입할 계획", "Low", "R3", ["NonRisk"]),
    (3, "net-zero", "Mid", "R2", []),
    (4, "감축 설비에 대한 투자를 확대", "Low", "R3", []),
    (4, "저탄소 원료의 사용 비율", "NonRisk", "R4", ["Mid"]),
    (4, "순환경제 체계를 만들어 가는 것을 지향", "Low", "R3", []),
]


def sample_sentences() -> list[tuple[int, str]]:
    """(쪽, 문장) — 정규화한 쪽 글 전체(2,500자 자르기 없음)를 `split_sentences_kor` 로."""
    from greenwash.heuristics import normalize_text_for_llm, split_sentences_kor
    from greenwash.tests.sample_text import page_texts

    out = []
    for p, text in enumerate(page_texts(), start=1):
        for s in split_sentences_kor(normalize_text_for_llm(text)):
            out.append((p, s))
    return out


def _label_for(page: int, sentence: str) -> tuple[str, str, list[str], int | None]:
    hits = [(i, row) for i, row in enumerate(SAMPLE_LABELS) if row[0] == page and row[1] in sentence]
    if len(hits) > 1:
        raise ValueError(f"쪽 {page} 한 문장에 라벨 줄 둘: {[h[0] for h in hits]}")
    if not hits:
        return "NonRisk", "R4", [], None
    i, (_p, _sub, label, rule, accept) = hits[0]
    return label, rule, list(accept), i


def sample_gold() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    seen: dict[tuple[int, str], dict[str, Any]] = {}
    used: set[int] = set()
    for page, s in sample_sentences():
        key = (page, U.sha12(s))
        if key in seen:
            seen[key]["occurrences"] += 1
            continue
        label, rule, accept, row = _label_for(page, s)
        if row is not None:
            used.add(row)
        it = {"page": page, "sentence": U.norm(s), "sha12": key[1], "label": label, "level": label,
              "category": dict(U.ABSENT), "accept": accept, "occurrences": 1, "rule": rule}
        seen[key] = it
        items.append(it)
    stale = [SAMPLE_LABELS[i][:2] for i in range(len(SAMPLE_LABELS)) if i not in used]
    if stale:
        raise ValueError(f"걸리지 않는 라벨 줄: {stale}")
    conventions = {
        "labels": list(U.LEVELS),
        "risk": list(U.RISK_LEVELS),
        "rules": RULES,
        "notes": [
            "문장 = 정규화한 쪽 글 전체(2,500자 자르기 없음 — 쪽 4 의 잘림 뒤 문장도 라벨)를 split_sentences_kor 로 가른 것. 표 꼴 줄도 한 문장.",
            "같은 (쪽, sha12) 문장은 한 줄로 접고 occurrences 에 센다.",
            "level 은 1차 결과의 등급(High·Mid·Low)과 같은 값, NonRisk 는 1차 결과 items 에 없는 문장(Level docstring).",
            CATEGORY_NOTE,
        ],
        "accept": "items[].accept — 판단이 갈리는 자리에서 같이 맞다고 볼 등급. 돌리기 전에 정했다.",
    }
    return common_gold.new(
        set_id="greenwash.synthetic-sample",
        module="greenwash",
        unit=U.UNIT,
        provenance={"kind": "runner_made", "by": AUTHOR, "at": MADE_ON,
                    "from": ["py/greenwash/tests/sample_text.py(지은 글 네 쪽)", "evalrig.greenwash.gold.SAMPLE_LABELS"]},
        review=dict(REVIEW_NONE),
        pending=[dict(p) for p in PENDING],
        coverage="exhaustive",
        synthetic=True,
        conventions=conventions,
        meta={"pages": 4, "sentences": sum(it["occurrences"] for it in items)},
        items=items,
    )


SG1_VERDICT = {"unsupported": "suspect", "supported": "supported", "partially_supported": "partial"}
_SELF = re.compile(r"^p(\d+)-")


def from_sg1(queries: list[dict[str, Any]], claim_expected: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """SG1 한 권 → 정답 셋. `document` 는 그 권의 docparse document.json(머리의 source 만 쓴다)."""
    claims = [q for q in queries if q.get("type") == "greenwash"]
    items = []
    for q in claims:
        m = _SELF.match(q.get("self") or "")
        if not m:
            raise ValueError(f"{q.get('id')}: self 꼴 모름")
        exp = claim_expected[q["id"]]
        label = SG1_VERDICT[exp["verdict"]]
        items.append({"page": int(m.group(1)), "sentence": U.norm(q["text"]), "sha12": U.sha12(q["text"]),
                      "label": label, "level": dict(U.ABSENT), "category": dict(U.ABSENT), "accept": [],
                      "occurrences": 1, "claimId": q["id"], "hasNumber": bool(exp.get("has_number"))})
    if set(claim_expected) != {q["id"] for q in claims}:
        raise ValueError("claim-expected 와 queries 의 주장 id 가 다름")
    src = document["source"]
    conventions = {
        "labels": ["suspect", "supported", "partial"],
        "notes": [
            "SG1 주장 판정(unsupported/supported/partially_supported)을 옮긴 것 — 등급 없음(level absent).",
            "셀 수 있는 것: suspect 를 결과가 위험(High·Mid)으로 냈는가(분모 = suspect 수), supported 를 위험으로 냈는가(분모 = supported 수). "
            "partial 은 두 분모 밖, 수만 따로 낸다(감독 답 11-09).",
            "전체 훑기가 아니다(coverage partial) — 정밀은 unmeasurable.",
            CATEGORY_NOTE,
        ],
    }
    return common_gold.new(
        set_id=f"greenwash.sg1-claims.{src['sha256'][:12]}",
        module="greenwash",
        unit=U.UNIT,
        provenance={"kind": "agent_draft", "by": AUTHOR, "at": MADE_ON,  # 판정 원본은 SG1 밴드가 지은 것 — 사람 확인 기록 없음
                    "from": ["SG1 queries.json(type greenwash)", "SG1 check/claim-expected.json"]},
        review=dict(REVIEW_NONE),
        pending=[dict(p) for p in PENDING],
        coverage="partial",
        synthetic=False,
        conventions=conventions,
        meta={"source": {"sha256": src["sha256"], "pageCount": src.get("page_count")},
              "counts": {k: sum(1 for it in items if it["label"] == k) for k in conventions["labels"]}},
        items=items,
    )


def check(gs: dict[str, Any]) -> dict[str, Any]:
    """공통 `check`(머리·등록된 items 꼴) + 이 모듈의 약속: (쪽, sha12) 유일, sha12 = norm(sentence) 의 것."""
    common_gold.check(gs)
    if gs["module"] != "greenwash" or gs["unit"] != U.UNIT:
        raise common_gold.GoldError(f"module/unit 이 그린워싱 것이 아님: {gs['module']} {gs['unit']}")
    seen = set()
    for i, it in enumerate(gs["items"]):
        k = (it["page"], it["sha12"])
        if k in seen:
            raise common_gold.GoldError(f"items[{i}] (쪽, sha12) 겹침 {k}")
        seen.add(k)
        if U.sha12(it["sentence"]) != it["sha12"]:
            raise common_gold.GoldError(f"items[{i}] sha12 가 문장과 다름")
    return gs
