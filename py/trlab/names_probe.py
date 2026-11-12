"""TRX7 — 사람 이름 영문 표기 고정 판 N0·N1·N2 (MARC-TICK-510, 이슈 383).

표본(prework/gold-trx7/names.json, 얼림 전): 지어낸 한국 이름 14(성 12 + 두 음절 이름, 실제 인물 0)를 서명 줄 4 + 직함 문맥 문장 8(이름 둘인 문장 3)에
넣는다. 항목 {id, kind: sign|body, ko, names:[{kr, table_en, rule_ok_re}]} — table_en 은 N2 표(꼴을 섞음: Given-Name Surname · Surname Given-name ·
이니셜 1), rule_ok_re 는 N1 규칙(이름 먼저, 음절 하이픈·대문자, 성 뒤)에 맞는 표기의 정규식(로마자 변이 허용).
판: 공통 지시문 두 줄 + N0 없음 · N1 규칙 한 줄 · N2 표(그 문장의 이름만). 답 꼴은 세 판 같게 {translation, person_names:[str]} —
이름을 따로 내게 하는 것 자체가 표기를 굳힐 수 있다(보고 한 줄). person_names 가 translation 안에 없으면 nospan.
셈: 출현(문장 × 이름)마다 3회 표기 집합 → 일관율 3/3 · 2/3, 규칙 지킴(N1) = rule_ok_re 맞는 표기 / 출현×3, 표 지킴(N2) = table_en 과 같은 표기 / 출현×3.
"""

import json
import re

COMMON = ("Translate this Korean ESG-report sentence into publication-ready English.\n"
          "Return JSON with the English translation and the list of person names exactly as they appear in your translation.")
# N1·N2 는 얼린 뒤(freeze.txt)에 쓴다 — first-run.txt 에 sha 를 적는다.
N1_RULE = ("Romanize Korean personal names as given name first then surname, given-name syllables hyphenated and both capitalized "
           "(e.g., Ga-Na Do); never initials.")
N2_HEAD = "Use exactly these name spellings (Korean -> English):"

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["translation", "person_names"],
          "properties": {"translation": {"type": "string"}, "person_names": {"type": "array", "items": {"type": "string"}}}}
WS = re.compile(r"\s+")


def build_prompt(variant, item):
    """→ (instructions, input, schema)."""
    lines = [COMMON]
    if variant == "N1":
        lines.append(N1_RULE)
    elif variant == "N2":
        lines.append(N2_HEAD)
        lines += [f"- {n['kr']} -> {n['table_en']}" for n in item["names"]]
    elif variant != "N0":
        raise ValueError(variant)
    return "\n".join(lines), item["ko"], SCHEMA


def read_answer(raw):
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("translation"), str):
        return None
    names = [str(x) for x in d.get("person_names") or []]
    return {"translation": d["translation"], "person_names": names}


def norm(s):
    return WS.sub(" ", (s or "").strip())


def renderings(item, ans):
    """문장의 이름 차례대로 그 회차의 표기 → [{kr, en, nospan}] (답 없음 → en None). person_names 가 모자라면 None."""
    out = []
    names = [norm(x) for x in ans["person_names"]] if ans else []
    tr = norm(ans["translation"]) if ans else ""
    for i, n in enumerate(item["names"]):
        en = names[i] if i < len(names) else None
        out.append({"kr": n["kr"], "en": en, "nospan": bool(en) and en not in tr})
    return out


def score(items, runs):
    """items = {id: item}, runs = {id: {rep: answer|None}} → 출현별 3회 표기 집합과 비율(서명/본문 따로)."""
    occ = []
    for iid, item in items.items():
        reps = runs.get(iid, {})
        rs = {rep: renderings(item, a) for rep, a in reps.items()}
        for i, n in enumerate(item["names"]):
            ens = [rs[rep][i]["en"] for rep in sorted(rs)]
            nospan = sum(1 for rep in rs if rs[rep][i]["nospan"])
            got = [e for e in ens if e]
            c = max((got.count(e) for e in set(got)), default=0)
            rule = sum(1 for e in got if re.fullmatch(n["rule_ok_re"], e))
            table = sum(1 for e in got if e == n["table_en"])
            occ.append({"id": iid, "kind": item["kind"], "kr": n["kr"], "reps": len(rs), "ens": ens, "distinct": len(set(got)),
                        "agree": c, "all3": len(rs) >= 3 and c == len(rs), "two3": len(rs) >= 3 and c >= 2, "rule_ok": rule,
                        "table_ok": table, "nospan": nospan, "missing": len(ens) - len(got)})

    def rate(rows, key, denom):
        return {"n": sum(r[key] for r in rows), "d": denom(rows)}

    def block(rows):
        return {"occ": len(rows), "all3": rate(rows, "all3", len), "two3": rate(rows, "two3", len),
                "rule_ok": rate(rows, "rule_ok", lambda rs: sum(len([e for e in r["ens"] if e]) for r in rs)),
                "table_ok": rate(rows, "table_ok", lambda rs: sum(len([e for e in r["ens"] if e]) for r in rs)),
                "nospan": rate(rows, "nospan", lambda rs: sum(len([e for e in r["ens"] if e]) for r in rs)),
                "missing": sum(r["missing"] for r in rows)}

    return {"n_occ": len(occ), "all": block(occ), "sign": block([o for o in occ if o["kind"] == "sign"]),
            "body": block([o for o in occ if o["kind"] == "body"]), "occ": occ}
