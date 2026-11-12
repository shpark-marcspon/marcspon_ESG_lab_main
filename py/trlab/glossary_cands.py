"""T-b 용어집 후보 모으기(AI 0). 사람이 여기서 골라 정답 용어집을 짓는다.

(1) 국문판 제목 블록 안의 「영문 + 국문」 꼴  (2) T-a 토픽 짝 제목(정답 파일)  (3) 짝 지은 표의 칸 짝(짧은 칸)
(4) 국문 끝말 정규식(…위원회·…협의회·…부문·…본부·…팀·…실·…센터·…재단·…제도·…프로그램·…상 …) +
    짝 블록 안 영문 n-gram(1~6 낱말)의 공기 Dice 로 고른 대응.
명령: python -m trlab.glossary_cands <키> → OUT/glossary/<키>-cands.json
"""

import json
import re
import sys
from collections import Counter, defaultdict

from trlab.align import load_pairs
from trlab.common import GOLD, OUT, is_hangul, load, split_bilingual

SUFFIX = re.compile(r"([가-힣A-Za-z0-9·]{1,20}(?:위원회|협의회|협의체|부문|본부|사업부|팀|실|센터|연구소|재단|제도|프로그램|캠페인|아카데미|"
                    r"상|법인|지주|은행|증권|보험|카드|그룹|사무국|원|처|부|단|시스템|플랫폼|펀드|챌린지|포럼|어워드|이니셔티브))(?![가-힣])")
STOP_END = {"원", "처", "부", "단", "상", "실"}  # 짧은 끝말은 두 글자 이상 앞말이 있을 때만
WORD = re.compile(r"[A-Za-z][A-Za-z0-9&'’\-\.]*")


def ngrams(text, nmax=6):
    w = WORD.findall(text)
    out = set()
    for i in range(len(w)):
        for n in range(1, nmax + 1):
            g = w[i:i + n]
            if len(g) < n:
                break
            if not g[0][0].isupper():
                break
            out.add(" ".join(g))
    return out


def kr_terms(text):
    out = set()
    for m in SUFFIX.finditer(text):
        t = m.group(1)
        if t[-1] in STOP_END and len(t) < 3:
            continue
        out.add(t)
    return out


def pair_texts(key):
    ko = {b.id: b for b in load(key, "ko")}
    en = {b.id: b for b in load(key, "en")}
    for p in load_pairs(key):
        yield p, " ".join(ko[i].text for i in p["ko"]), " ".join(en[i].text for i in p["en"]), ko, en


def dice_table(key, min_df=2):
    """KR 용어 → [(EN n-gram, dice, 같이 나온 짝 수)]."""
    df_k, df_e, co = Counter(), Counter(), Counter()
    for _, kt, et, _, _ in pair_texts(key):
        ks, es = kr_terms(kt), ngrams(et)
        df_k.update(ks)
        df_e.update(es)
        for k in ks:
            for e in es:
                co[(k, e)] += 1
    best = defaultdict(list)
    for (k, e), c in co.items():
        if c < min_df or df_k[k] < min_df:
            continue
        d = 2 * c / (df_k[k] + df_e[e])
        best[k].append((e, round(d, 3), c))
    return {k: sorted(v, key=lambda x: (-x[1], -len(x[0])))[:3] for k, v in best.items()}, df_k


def collect(key):
    c = []
    # (1) 국문 제목 블록의 「영문 + 국문」
    for b in load(key, "ko"):
        if b.kind == "heading" and b.region == "body":
            lat, ko = split_bilingual(b.text)
            if lat and ko and len(lat) > 3 and len(ko) <= 25:
                c.append({"src": "bilingual-heading", "kr": ko, "en": lat, "ev": [b.id]})
    # (2) T-a 토픽 짝 제목
    for lang_k, lang_e in (("ko", "en"),):
        gk, ge = GOLD / "toc" / f"{key}-ko.json", GOLD / "toc" / f"{key}-en.json"
        if gk.exists():
            a, b = json.load(open(gk))["items"], json.load(open(ge))["items"]
            for x, y in zip(a, b):
                if is_hangul(x["text"]):
                    c.append({"src": "toc-pair", "kr": x["text"], "en": y["text"], "ev": [x["start_block"], y["start_block"]]})
    # (3) 표 칸 짝
    for p, _, _, ko, en in pair_texts(key):
        if "cells" not in p:
            continue
        a, b = ko[p["ko"][0]].rows, en[p["en"][0]].rows
        for r, col in p["cells"]:
            kt, et = a[r][col].strip(), b[r][col].strip()
            if is_hangul(kt) and 2 <= len(kt) <= 20 and not re.search(r"\d", kt) and et and not is_hangul(et):
                c.append({"src": "table-cell", "kr": kt, "en": et, "ev": [p["ko"][0], p["en"][0]]})
    # (4) 끝말 + Dice
    dt, df_k = dice_table(key)
    for k, v in dt.items():
        if v and v[0][1] >= 0.3:
            c.append({"src": "suffix-dice", "kr": k, "en": v[0][0], "dice": v[0][1], "df": df_k[k], "alts": v[1:]})
    # 같은 KR 은 근거를 모아 하나로
    agg = {}
    for x in c:
        a = agg.setdefault(x["kr"], {"kr": x["kr"], "en": Counter(), "src": set(), "ev": [], "df": 0})
        a["en"][x["en"]] += 1
        a["src"].add(x["src"])
        a["ev"] += x.get("ev", [])
        a["df"] = max(a["df"], x.get("df", 0))
    out = [{"kr": a["kr"], "en": a["en"].most_common(3), "src": sorted(a["src"]), "ev": a["ev"][:4], "df": a["df"]}
           for a in agg.values()]
    out.sort(key=lambda a: (-len(a["src"]), -a["df"]))
    return out


def main(argv):
    key = argv[0]
    (OUT / "glossary").mkdir(parents=True, exist_ok=True)
    out = collect(key)
    json.dump(out, open(OUT / "glossary" / f"{key}-cands.json", "w"), ensure_ascii=False, indent=1)
    print(key, "후보", len(out), Counter(s for a in out for s in a["src"]))


if __name__ == "__main__":
    main(sys.argv[1:])
