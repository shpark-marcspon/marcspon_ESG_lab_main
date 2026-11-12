"""TRX10 — 블록 체크리스트 검수(TRX7 V2)를 책 한 권(sam) 전량에 (MARC-TICK-514, 이슈 382).

(1) 단위: review_probe.book_pairs("sam") 의 짝 전부(용어집 근거 짝 뺀 1,391 — 제목·표·mixed 포함, 꼴은 kind). 짝 id = "+".join(ko ids).
    units.json 엔 글이 없다(id·쪽·kind·ko/en ids·sha16·길이·용어집 index·문장 수) — 글은 texts() 가 다시 읽어 sha 를 대조한다.
(2) 층화(4.1-mini 300): 층 = kind × EN 길이 띠(<100 · 100~300 · 300~800 · 800+), 크기 비례(최소 5), seed 514. luna 와 같은 짝.
(3) 묶음 호출: 블록 여럿을 호출 글자(지시문 + 입력) ≤ 8,000자로 한 호출. 지시문 = V2_INSTR 그대로, 입력 = [Glossary](묶은 블록 용어집
    합집합) + [Block k] 마다 [Korean source]/[English translation]. 스키마 = V2_SCHEMA 이슈 항목에 block:integer 한 칸(BUNDLE_SCHEMA —
    review_probe 0줄). 답을 block 으로 나눠 블록마다 review_probe.hit_of·score_unit(TRX7 채점 그대로). 1..n 밖 block 은 oob.
(4) 라벨 표본: pick_flags — luna 걸림 블록 40(갈래 × 꼴 층화, 갈래 = 그 블록 이슈의 최빈 category) + 4.1-mini 걸림 블록 20(같이 건 짝 먼저),
    pick_omit — 누락 규칙 걸림 블록 40(EN 길이 띠 층화). seed 514. 이슈·블록 글은 표본 파일에 안 싣는다(id·sha·index 만).
(5) 분포: dist() — 걸림율(전체·꼴별·띠별), 블록당 이슈 0/1/2/3+, 갈래, 쪽당 걸림 중앙값·최대, 지연 중앙값·p90·최대, USD 평균·중앙값, 상태.
"""

import copy
import hashlib
import random
from collections import Counter, defaultdict

from trlab import common
from trlab import omit_rule as OR
from trlab import review_probe as R

SEED = 514
BUNDLE_MAX_CHARS = 8000  # 호출 글자(지시문 + 입력) 상한
STRATA_MIN = 5
EN_BANDS = ((100, "<100"), (300, "100~300"), (800, "300~800"), (None, "800+"))
KINDS = ("paragraph", "heading", "table", "mixed")
FLAG_LABELS = ("real", "context", "noise")

BUNDLE_SCHEMA = copy.deepcopy(R.V2_SCHEMA)
BUNDLE_SCHEMA["properties"]["issues"]["items"]["properties"]["block"] = {"type": "integer"}
BUNDLE_SCHEMA["properties"]["issues"]["items"]["required"] = ["block"] + list(R.V2_SCHEMA["properties"]["issues"]["items"]["required"])


def sha16(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def en_band(n):
    for lim, name in EN_BANDS:
        if lim is None or n < lim:
            return name
    return EN_BANDS[-1][1]


# ---------- (1) 단위 ----------
def pair_id(pr):
    return "+".join(pr["ko"])


def make_units(key="sam", gloss=None):
    """book_pairs 짝 전부 → 단위 목록(글 없음)."""
    from trlab.glossary_check import load_gloss
    gloss = load_gloss(key)["items"] if gloss is None else gloss
    out, seen = [], set()
    for pr in R.book_pairs(key):
        uid = pair_id(pr)
        if uid in seen:
            raise ValueError(f"짝 id 겹침: {uid}")
        seen.add(uid)
        n_ko, n_en = OR.counts(pr["ko_text"], pr["en_text"])
        out.append({"id": uid, "book": key, "page": pr["page"], "kind": pr["kind"], "ko_ids": list(pr["ko"]), "en_ids": list(pr["en"]),
                    "ko_sha": sha16(pr["ko_text"]), "en_sha": sha16(pr["en_text"]), "ko_len": len(pr["ko_text"]), "en_len": len(pr["en_text"]),
                    "en_band": en_band(len(pr["en_text"])), "glossary": R.gloss_idx(pr["ko_text"], gloss), "n_ko_sent": n_ko, "n_en_sent": n_en,
                    "omit_applies": OR.applies(pr["kind"]), "omit_flag": OR.flagged(n_ko, n_en) if OR.applies(pr["kind"]) else None})
    return out


class Texts:
    """책 블록을 한 번 읽어 두고 단위 → 글(sha 대조)."""

    def __init__(self, key="sam", gloss=None):
        from trlab.glossary_check import load_gloss
        self.key = key
        self.gloss = load_gloss(key)["items"] if gloss is None else gloss
        self.ko = {b.id: b for b in common.load(key, "ko")}
        self.en = {b.id: b for b in common.load(key, "en")}

    def texts(self, unit):
        ko = " ".join(self.ko[i].text for i in unit["ko_ids"])
        en = " ".join(self.en[i].text for i in unit["en_ids"])
        if sha16(ko) != unit["ko_sha"] or sha16(en) != unit["en_sha"]:
            raise ValueError(f"{unit['id']}: 글 sha 가 units.json 과 다르다")
        return ko, en

    def mat(self, unit):
        """review_probe.build_prompt("V2", mat) 가 받는 꼴(+ clean 꼴 채점용 칸)."""
        ko, en = self.texts(unit)
        return {"id": unit["id"], "book": self.key, "split": "full", "kind": unit["kind"], "cat": "clean", "page": unit["page"], "ko": ko,
                "en": en, "en_clean": en, "planted": None, "wrong": [],
                "glossary": [{"kr": self.gloss[i]["kr"], "en": self.gloss[i]["en"]} for i in unit["glossary"]]}


def texts(unit, T=None):
    T = Texts(unit["book"]) if T is None else T
    return T.texts(unit)


# ---------- (2) 층화 ----------
def stratum(unit):
    return (unit["kind"], unit["en_band"])


def allocate(sizes, n, min_per=STRATA_MIN):
    """층 크기 → 뽑을 수(크기 비례, 최소 min_per — 층이 그보다 작으면 전부). 가장 큰 나머지 차례로 n 에 맞춘다."""
    total = sum(sizes.values())
    if total == 0:
        return {}
    raw = {k: n * s / total for k, s in sizes.items()}
    alloc = {k: min(sizes[k], max(min_per, int(raw[k]))) for k in sizes}
    keys = sorted(sizes, key=lambda k: (-(raw[k] - int(raw[k])), k))
    i = 0
    while sum(alloc.values()) < min(n, total) and keys:
        k = keys[i % len(keys)]
        if alloc[k] < sizes[k]:
            alloc[k] += 1
        i += 1
        if i > 10 * len(keys) * max(1, n):
            break
    while sum(alloc.values()) > n:  # 최소치 때문에 넘치면 큰 층부터 하나씩 뺌(최소 아래로는 안 뺌)
        big = sorted((k for k in alloc if alloc[k] > min_per), key=lambda k: -alloc[k])
        if not big:
            break
        alloc[big[0]] -= 1
    return alloc


def stratify(units, n=300, seed=SEED, min_per=STRATA_MIN, key=stratum):
    """층별로 seed 고정 뽑기 → 단위 목록(id 차례 = 쪽·id 정렬)."""
    rnd = random.Random(seed)
    by = defaultdict(list)
    for u in sorted(units, key=lambda u: (u["page"], u["id"])):
        by[key(u)].append(u)
    alloc = allocate({k: len(v) for k, v in by.items()}, n, min_per)
    out = []
    for k in sorted(by):
        out += rnd.sample(by[k], alloc[k])
    return sorted(out, key=lambda u: (u["page"], u["id"]))


# ---------- (3) 묶음 ----------
def glossary_union(blocks):
    seen, out = set(), []
    for b in blocks:
        for it in b["glossary"]:
            k = (it["kr"], it["en"])
            if k not in seen:
                seen.add(k)
                out.append({"kr": it["kr"], "en": it["en"]})
    return out


def bundle_input(blocks):
    g = R.glossary_text(glossary_union(blocks))
    parts = [f"[Glossary]\n{g}"]
    for k, b in enumerate(blocks, 1):
        parts.append(f"[Block {k}]\n[Korean source]\n{b['ko']}\n\n[English translation]\n{b['en']}")
    return "\n\n".join(parts)


def bundle_chars(blocks):
    return len(R.V2_INSTR) + len(bundle_input(blocks))


def bundle_plan(blocks, max_chars=BUNDLE_MAX_CHARS, seed=SEED, shuffle=True):
    """blocks(mat 꼴: id·ko·en·glossary) → [[block, …], …]. seed 로 섞은 뒤 차례대로 호출 글자 ≤ max_chars 로 묶는다.
    혼자서도 넘는 블록은 혼자 한 묶음(oversize)."""
    items = list(blocks)
    if shuffle:
        random.Random(seed).shuffle(items)
    out, cur = [], []
    for b in items:
        if cur and bundle_chars(cur + [b]) > max_chars:
            out.append(cur)
            cur = []
        cur.append(b)
    if cur:
        out.append(cur)
    return out


def build_bundle_prompt(blocks):
    if not R.V2_INSTR:
        raise RuntimeError("V2_INSTR 가 비어 있다")
    return R.V2_INSTR, bundle_input(blocks), BUNDLE_SCHEMA


def split_bundle_answer(raw, n_blocks):
    """묶음 답 → {parsed, per_block:[[issue, …] × n], oob:[issue], n_issues}. block 은 1..n."""
    d, ok = R.parse_json(raw)
    if not ok or not isinstance(d, dict):
        return {"parsed": False, "per_block": [[] for _ in range(n_blocks)], "oob": [], "n_issues": 0}
    per, oob = [[] for _ in range(n_blocks)], []
    for it in d.get("issues") or []:
        if not isinstance(it, dict):
            continue
        k = it.get("block")
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= n_blocks:
            oob.append(it)
            continue
        per[k - 1].append({kk: v for kk, v in it.items() if kk != "block"})
    return {"parsed": True, "per_block": per, "oob": oob, "n_issues": sum(len(p) for p in per) + len(oob)}


def score_bundle_block(mat, issues, parsed=True):
    ans = {"parsed": parsed, "issues": issues, "flagged": len(issues) >= 1, "extra": {}}
    return R.score_unit(mat, ans)


# ---------- (4) 라벨 표본 ----------
def primary_cat(cats):
    """블록 이슈 category 목록 → 최빈(동률이면 먼저 나온 것)."""
    if not cats:
        return "none"
    c = Counter(cats)
    top = max(c.values())
    return next(x for x in cats if c[x] == top)


def pick_flags(luna_rows, mini_rows, seed=SEED, n_luna=40, n_mini=20):
    """rows = score 줄 {id, kind, flagged, cats:[category …], status}. luna 걸림 블록에서 (갈래 × 꼴) 층화 n_luna,
    4.1-mini 걸림 블록에서 같은 층화 n_mini — 층 안에서 luna 도 건 짝(같이 건 짝)을 먼저. → [{id, model, stratum}]."""
    rnd = random.Random(seed)
    lf = [r for r in luna_rows if r.get("flagged") and r.get("status") == "ok"]
    mf = [r for r in mini_rows if r.get("flagged") and r.get("status") == "ok"]
    luna_ids = {r["id"] for r in lf}

    def key(r):
        return (primary_cat(r.get("cats", [])), r["kind"])

    out = []
    by = defaultdict(list)
    for r in sorted(lf, key=lambda r: r["id"]):
        by[key(r)].append(r)
    alloc = allocate({k: len(v) for k, v in by.items()}, min(n_luna, len(lf)), min_per=1)
    for k in sorted(by):
        for r in rnd.sample(by[k], alloc.get(k, 0)):
            out.append({"id": r["id"], "model": "gpt-6-luna", "stratum": list(k), "co_flagged": r["id"] in {x["id"] for x in mf}})
    by = defaultdict(list)
    for r in sorted(mf, key=lambda r: r["id"]):
        by[key(r)].append(r)
    alloc = allocate({k: len(v) for k, v in by.items()}, min(n_mini, len(mf)), min_per=1)
    for k in sorted(by):
        pool = by[k][:]
        rnd.shuffle(pool)
        pool.sort(key=lambda r: 0 if r["id"] in luna_ids else 1)  # 같이 건 짝 먼저(안정 정렬 — 그 안 차례는 섞인 대로)
        for r in pool[: alloc.get(k, 0)]:
            out.append({"id": r["id"], "model": "gpt-4.1-mini", "stratum": list(k), "co_flagged": r["id"] in luna_ids})
    return out


def pick_omit(units, seed=SEED, n=40):
    """누락 규칙에 걸린 단위에서 EN 길이 띠 층화 n → [{id, en_band}]."""
    pool = [u for u in units if u.get("omit_flag")]
    picked = stratify(pool, min(n, len(pool)), seed, min_per=STRATA_MIN, key=lambda u: (u["en_band"],))
    return [{"id": u["id"], "en_band": u["en_band"], "n_ko": u["n_ko_sent"], "n_en": u["n_en_sent"]} for u in picked]


# ---------- (5) 분포 ----------
def _q(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/0"


def dist(rows):
    """score 줄 목록 → 분포(분모 명시). rows: {id, page, kind, en_band, status, flagged, n_issues, cats, elapsed_ms, usd}."""
    ok = [r for r in rows if r.get("status") == "ok"]
    flagged = [r for r in ok if r.get("flagged")]
    by_kind = {k: pct(sum(1 for r in flagged if r["kind"] == k), sum(1 for r in ok if r["kind"] == k)) for k in KINDS}
    by_band = {b: pct(sum(1 for r in flagged if r["en_band"] == b), sum(1 for r in ok if r["en_band"] == b)) for _, b in EN_BANDS}
    hist = Counter(min(3, r.get("n_issues", 0)) for r in ok)
    cats = Counter(c for r in flagged for c in r.get("cats", []))
    per_page = Counter(r["page"] for r in flagged)
    pages = {r["page"] for r in ok}
    pp = [per_page.get(p, 0) for p in pages]
    lat = [r["elapsed_ms"] for r in ok if r.get("elapsed_ms") is not None]
    usd = [r["usd"] for r in ok if r.get("usd") is not None]
    return {"n": len(rows), "n_ok": len(ok), "status": dict(Counter(r.get("status") for r in rows)),
            "flagged": pct(len(flagged), len(ok)), "flag_rate": (len(flagged) / len(ok)) if ok else None, "by_kind": by_kind, "by_band": by_band,
            "issues_hist": {"0": hist[0], "1": hist[1], "2": hist[2], "3+": hist[3]}, "cats": dict(cats.most_common()),
            "n_issues": sum(r.get("n_issues", 0) for r in ok),
            "per_page": {"median": _q(pp, 0.5), "max": max(pp) if pp else None, "n_pages": len(pages)},
            "elapsed_ms": {"median": _q(lat, 0.5), "p90": _q(lat, 0.9), "max": max(lat) if lat else None, "sum": sum(lat)},
            "usd": {"mean": (sum(usd) / len(usd)) if usd else None, "median": _q(usd, 0.5), "sum": sum(usd)}}
