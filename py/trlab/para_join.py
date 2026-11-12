"""TRX6 — 글자 → 줄 → 단(기하), 줄 짝 규칙 R1~R7, 파서 흉내 판 G, 묶기. 순수 함수(파일·네트워크 0).

C1 기하(정답 얼림 전): page_glyphs(pypdfium2 loose box) → lines(|Δy| ≤ 0.2 × 크기 → x 정렬 → x 틈 > 2.0 × 이웃 큰 글자 크기면
다른 줄 → 최빈 크기의 0.7 배 아래 글자(위첨자)는 따로; 같은 자리에 겹쳐 그린 같은 글자는 하나) → columns(x0 묶음 gap 1.0 × 크기, x 범위가 겹치는 묶음은 합침). C2 규칙: neighbors(짝) → judge(R1~R6, cut_by) → join(묶음) → col_break_flags(R7 표시) · G 파서 흉내.

줄 = {id, x0, x1, y, size, bold_share, color, text, n, col}. 좌표 pt, 왼쪽 위 원점. text 는 공백 없이 이은 글.
"""

import ctypes
import math
import re
from collections import Counter

import pypdfium2.raw as C

from trlab.glyphs import is_bold as _glyph_bold

SMALL = 0.7  # 줄 최빈 크기의 이 배 아래 글자는 따로 줄(위첨자) — 발주서 「같은 크기 ±0.5pt」 대신(한글 7.92 + 라틴 8.71 이 한 인쇄 줄)
Y_TOL = 0.2  # 같은 줄의 |Δy| / 크기(0.3 이면 다른 단의 2pt 어긋난 줄이 한 행에 들어 평균 y 가 흐른다)
X_GAP = 2.0  # 다른 줄로 가르는 x 틈 / 크기(2단의 같은 높이 줄 — cos 단 사이 틈 20pt / 8pt = 2.5 가 경계라 2.0, 발주서 2.5 와 다름)
COL_GAP = 1.0  # 단 묶음의 x0 틈 / 크기
COL_OVERLAP = 0.5  # x 범위가 좁은 쪽 너비의 이만큼 겹치면 같은 단


# ---------- 글자 ----------
def page_glyphs(page):
    """쪽 하나의 글자 [(x0, y0, x1, y1, size, weight, font, (r,g,b), code)] — loose box, 왼쪽 위 원점. 공백·지어낸 글자는 뺌.
    glyphs.page_chars 와 같은 읽기이되 상자 네 값을 둔다(★8)."""
    H = page.get_height()
    tp = page.get_textpage()
    raw = tp.raw
    out = []
    try:
        n = C.FPDFText_CountChars(raw)
        rect, mat = C.FS_RECTF(), C.FS_MATRIX()
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        name, flags = ctypes.create_string_buffer(256), ctypes.c_int()
        styles = {}
        for i in range(n):
            obj = C.FPDFText_GetTextObject(raw, i)
            if not obj:
                continue
            code = C.FPDFText_GetUnicode(raw, i)
            if code <= 32:
                continue
            key = ctypes.addressof(obj.contents)
            st = styles.get(key)
            if st is None:
                C.FPDFText_GetMatrix(raw, i, mat)
                fs = C.FPDFText_GetFontSize(raw, i)
                size = fs * math.sqrt(abs(mat.a * mat.d - mat.b * mat.c))
                weight = C.FPDFText_GetFontWeight(raw, i)
                ln = C.FPDFText_GetFontInfo(raw, i, name, 256, flags)
                font = name.value.decode("utf-8", "replace").split("+", 1)[-1] if ln else ""
                C.FPDFText_GetFillColor(raw, i, r, g, b, a)
                st = styles[key] = (round(size, 2), int(weight), font, (r.value, g.value, b.value))
            C.FPDFText_GetLooseCharBox(raw, i, rect)
            out.append((rect.left, H - rect.top, rect.right, H - rect.bottom, *st, code))
    finally:
        tp.close()
    return out


# ---------- 줄 ----------
def _rows(glyphs):
    """|Δy| ≤ Y_TOL × (글자 크기와 행 최빈 크기 중 작은 것)인 행에 넣는다(여럿이면 y 가 가장 가까운 행). 크기는 묶음 기준이 아니다
    (한 인쇄 줄에 한글 7.9 + 라틴 8.7 이 섞인다). y 차례."""
    rows = []
    for gl in sorted(glyphs, key=lambda t: ((t[1] + t[3]) / 2, t[0])):
        cy, sz = (gl[1] + gl[3]) / 2, gl[4]
        best, bd = None, None
        for row in rows[-12:]:
            d = abs(row["y"] - cy)
            if d <= Y_TOL * min(sz, row["sizes"].most_common(1)[0][0]) and (bd is None or d < bd):
                best, bd = row, d
        if best is None:
            rows.append({"y": cy, "sum_y": cy, "n": 1, "g": [gl], "sizes": Counter([round(sz, 1)])})
        else:
            best["g"].append(gl)
            best["sum_y"] += cy
            best["n"] += 1
            best["y"] = best["sum_y"] / best["n"]
            best["sizes"][round(sz, 1)] += 1
    return rows


def _line_of(g):
    n = len(g)
    sizes = Counter(round(t[4], 2) for t in g)
    col = Counter(t[7] for t in g).most_common(1)[0][0]
    return {"x0": round(min(t[0] for t in g), 1), "x1": round(max(t[2] for t in g), 1),
            "y": round(sum((t[1] + t[3]) / 2 for t in g) / n, 1),
            "size": sizes.most_common(1)[0][0],
            "bold_share": round(sum(_glyph_bold(t[5], t[6]) for t in g) / n, 3),
            "color": "#%02x%02x%02x" % tuple(int(v) & 255 for v in col),
            "text": "".join(chr(t[8]) for t in g), "n": n}


def dedupe(glyphs):
    """같은 자리(0.5pt)에 겹쳐 그린 같은 글자는 하나만(탐색 띠가 둘씩 그려진 쪽 — common.load_doc 의 접기와 같은 뜻)."""
    seen, out = set(), []
    for t in glyphs:
        sig = (round(t[0] * 2), round(t[1] * 2), t[8])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(t)
    return out


def _split_small(g):
    """줄 안에서 최빈 크기의 SMALL 배 아래 글자(위첨자·각주 표)는 따로 줄로 뗀다. [[글자…], …]"""
    mode = Counter(round(t[4], 2) for t in g).most_common(1)[0][0]
    main = [t for t in g if t[4] >= SMALL * mode]
    small = [t for t in g if t[4] < SMALL * mode]
    out = [main] if main else []
    if small:
        cur = [small[0]]
        for prev, t in zip(small, small[1:]):
            if t[0] - prev[2] > X_GAP * max(prev[4], t[4]):
                out.append(cur)
                cur = []
            cur.append(t)
        out.append(cur)
    return out


def lines(glyphs):
    """글자 → 줄 목록(id L1… 은 y → x 차례라 재생성해도 같다)."""
    out = []
    for row in _rows(dedupe(glyphs)):
        g = sorted(row["g"], key=lambda t: t[0])
        runs, cur = [], [g[0]]
        for prev, t in zip(g, g[1:]):
            if t[0] - prev[2] > X_GAP * max(prev[4], t[4]):
                runs.append(cur)
                cur = []
            cur.append(t)
        runs.append(cur)
        for r in runs:
            for part in _split_small(r):
                out.append(_line_of(part))
    out.sort(key=lambda L: (L["y"], L["x0"]))
    for k, L in enumerate(out, 1):
        L["id"] = f"L{k}"
    return out


# ---------- 단 ----------
def cluster_values(values, gap):
    """정렬해서 이웃 간격이 gap 이하면 한 묶음(docparse.layout.cluster_values 와 같은 셈). 묶음의 (평균, 원소 수)."""
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return [(sum(g) / len(g), len(g)) for g in out]


def columns(ls, base):
    """줄 → 단. x0 를 COL_GAP × base 로 묶고, x 범위가 좁은 쪽 너비의 COL_OVERLAP 이상 겹치는 묶음은 합친다(들여쓰기·
    불릿 줄이 같은 단에 들게). 줄마다 col(1…, 왼쪽부터)을 적고 단 목록 [{col, x0, x1, n}] 을 돌려준다."""
    if not ls:
        return []
    gap = COL_GAP * base
    groups = []
    for L in sorted(ls, key=lambda L: L["x0"]):
        if groups and L["x0"] - groups[-1]["last"] <= gap:
            groups[-1]["lines"].append(L)
            groups[-1]["last"] = L["x0"]
        else:
            groups.append({"lines": [L], "last": L["x0"]})
    cols = [{"x0": min(L["x0"] for L in g["lines"]), "x1": max(L["x1"] for L in g["lines"]), "lines": g["lines"]}
            for g in groups]
    changed = True
    while changed:
        changed = False
        for i in range(len(cols) - 1):
            a, b = cols[i], cols[i + 1]
            ov = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            if ov >= COL_OVERLAP * min(a["x1"] - a["x0"], b["x1"] - b["x0"]):
                a["lines"] += b["lines"]
                a["x0"], a["x1"] = min(a["x0"], b["x0"]), max(a["x1"], b["x1"])
                del cols[i + 1]
                changed = True
                break
    cols.sort(key=lambda c: c["x0"])
    out = []
    for k, c in enumerate(cols, 1):
        for L in c["lines"]:
            L["col"] = k
        out.append({"col": k, "x0": round(c["x0"], 1), "x1": round(c["x1"], 1), "n": len(c["lines"])})
    return out


# ---------- 줄 짝 ----------
def is_tiny(L):
    """위첨자·낱 부호 줄(글자 ≤ 2, 너비 < 1.5 × 크기) — 짝·셈에서 뺀다."""
    return L["n"] <= 2 and (L["x1"] - L["x0"]) < 1.5 * L["size"]


def neighbors(ls):
    """같은 단 안 바로 이웃한 줄 짝 [(a, b)] — a 의 바로 아래(y 가 큰 쪽) 가운데 x 범위가 좁은 쪽 너비의 0.3 이상 겹치는 가장 가까운 줄.
    tiny 줄은 빼고 본다. 규칙 판정과 셈의 분모가 같은 짝을 쓴다."""
    big = [L for L in ls if not is_tiny(L)]
    pairs = []
    for a in big:
        best = None
        for b in big:
            if b["col"] != a["col"] or b["y"] <= a["y"] + 0.3 * a["size"]:
                continue
            ov = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            if ov < 0.3 * min(a["x1"] - a["x0"], b["x1"] - b["x0"]):
                continue
            if best is None or b["y"] < best["y"]:
                best = b
        if best is not None:
            pairs.append((a["id"], best["id"]))
    return pairs


# ---------- 규칙 ----------
TH = {  # cos 표본으로 정하고 얼린다(out/trx6/first-run.txt)
    "overlap": 0.5,  # R1 x 겹침 / 좁은 너비
    "x0_tol": 1.0,  # R1 |x0 차| / 크기
    "P": 2.5,  # R2 줄 간격 / 크기
    "pitch_mult": 1.12,  # R2r 최빈 pitch 의 배(cos 문단 사이 틈 17.2/14.5 = 1.19, 19.7/14 = 1.41)
    "size_tol": 0.12,  # R3 크기 차 / 큰 쪽 크기(라틴이 많은 줄은 8.7 대 한글 7.9 — 발주서 0.3pt 대신 비율)
    "runin": 1,  # R3 예외: 굵은 짧은 첫 줄(run-in 머리말) 뒤 본문은 잇는다
    "indent": 0.8,  # R4 들여쓰기 / 크기
    "width": 0.85,  # R5 앞 줄 너비 / 단 너비
    "head_ratio": 1.25,  # R6 제목: 크기 ≥ base × 이 비(docparse.blocks.classify 와 같은 비)
    "head_bold_delta": 0.5,  # R6 제목: 굵고 크기 > base + 이 값(pt)
    "cap_ratio": 0.9,  # R6 설명 꼴: 크기 < base × 이 비 이고 머리 기호
    "edge": 0.08,  # R6 탐색 띠: 쪽 가장자리 비(pagemap.EDGE)
    "g": 0.65,  # G 파서 흉내: 틈 / 크기(docparse build_text_blocks 0.65)
}
RULES = ("R1", "R2", "R2r", "R3", "R4", "R5", "R6")
_END = re.compile(r"[.!?」』”’)]\s*$|[.!?]」$")
_BULLET = re.compile(r"^(?:[•·▪\-–※〉›∙‧◦○●■□]|[①-⑳]|[가-힣]\.|\d{1,2}[.)])")  # 영문 대문자 머리는 뺌(고유명사 줄이 흔함)
_CAP = re.compile(r"^(?:〉|\*|주\)|※|단위|\(단위)")


def ends_sentence(text):
    return bool(_END.search(text))


def is_bold(L):
    return L["bold_share"] >= 0.5


def classify(L, base, tables, W, H, th):
    """R6 갈래: heading | table | caption | nav | None(본문 후보)."""
    cx, cy = (L["x0"] + L["x1"]) / 2, L["y"]
    for x0, y0, x1, y1 in tables:
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
            return "table"
    if L["size"] >= th["head_ratio"] * base or (is_bold(L) and L["size"] > base + th["head_bold_delta"]):
        return "heading"
    if L["size"] < th["cap_ratio"] * base and _CAP.match(L["text"]):
        return "caption"
    e = th["edge"]
    if L["size"] < base and (cx < e * W or cx > (1 - e) * W or cy < e * H or cy > (1 - e) * H):
        return "nav"
    return None


def col_width(a, ls, reach=8.0):
    """a 와 왼끝이 같은(|x0 차| ≤ 2 × 크기) 같은 단 근처(|Δy| ≤ reach × 크기) 줄들의 max x1 − a.x0 — R5 의 「단 너비」
    (그림 옆에서 단이 좁아지는 자리를 따라간다)."""
    x1 = max((L["x1"] for L in ls if L["col"] == a["col"] and abs(L["x0"] - a["x0"]) <= 2.0 * a["size"]
              and abs(L["y"] - a["y"]) <= reach * a["size"] and not is_tiny(L)), default=a["x1"])
    return max(x1 - a["x0"], 1.0)


def modal_pitch(ls):
    """같은 크기(±0.3) 이웃 짝의 최빈 줄 간격(0.5pt 반올림). 없으면 None."""
    c = Counter()
    byid = {L["id"]: L for L in ls}
    for a, b in neighbors(ls):
        A, B = byid[a], byid[b]
        if abs(A["size"] - B["size"]) <= 0.3:
            c[round((B["y"] - A["y"]) * 2) / 2] += 1
    return c.most_common(1)[0][0] if c else None


def judge(a, b, ls, base, kinds, th, rules, pitch=None, first_of_group=None):
    """짝 (a 위, b 아래) 를 켜진 규칙으로 판정 → 자른 규칙 이름 목록(비면 잇는다)."""
    cut = []
    s = max(a["size"], b["size"])
    if "R1" in rules:
        ov = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
        if not (ov >= th["overlap"] * min(a["x1"] - a["x0"], b["x1"] - b["x0"]) or abs(a["x0"] - b["x0"]) <= th["x0_tol"] * s):
            cut.append("R1")
    if "R2" in rules and b["y"] - a["y"] > th["P"] * s:
        cut.append("R2")
    if "R2r" in rules and pitch and b["y"] - a["y"] > th["pitch_mult"] * pitch:
        cut.append("R2r")
    if "R3" in rules:
        same = abs(a["size"] - b["size"]) <= th["size_tol"] * s and is_bold(a) == is_bold(b) and a["color"] == b["color"]
        runin = (th["runin"] and is_bold(a) and not is_bold(b) and a["color"] == b["color"]
                 and abs(a["size"] - b["size"]) <= th["size_tol"] * s
                 and (a["x1"] - a["x0"]) < 0.5 * col_width(a, ls) and (first_of_group is None or first_of_group(a)))
        if not (same or runin):
            cut.append("R3")
    if "R4" in rules and ends_sentence(a["text"]):
        if b["x0"] - a["x0"] > th["indent"] * s or _BULLET.match(b["text"]):
            cut.append("R4")
    if "R5" in rules and ends_sentence(a["text"]) and (a["x1"] - a["x0"]) < th["width"] * col_width(a, ls):
        cut.append("R5")
    if "R6" in rules and (kinds.get(a["id"]) or kinds.get(b["id"])):
        cut.append("R6")
    return cut


def join(ls, tables, base, W, H, rules=RULES, th=TH, mode="R"):
    """줄 → 묶음. 돌려줌 {groups: {line_id: group_id}, cut_by: {"a>b": [규칙…]}, kinds: {line_id: 갈래}, flags: [단 넘김 후보]}.
    mode R(규칙) | G(파서 흉내 — 틈 ≤ th.g × 크기, x 겹침 ≥ 0.5, |x0 차| ≤ 2.5 × 크기, 크기 차 ≤ 12%; 표 줄은 따로)."""
    th = dict(TH, **th)
    rules = set(rules)
    byid = {L["id"]: L for L in ls}
    parent = {L["id"]: L["id"] for L in ls}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(y)] = find(x)

    kinds = {L["id"]: classify(L, base, tables, W, H, th) for L in ls} if ("R6" in rules or mode == "G") else {}
    if mode == "G":
        kinds = {k: v for k, v in kinds.items() if v == "table"}
    pitch = modal_pitch(ls) if "R2r" in rules else None  # R 은 R2(절대 P) 와 R2r(쪽 최빈 pitch 비) 둘 다
    has_above = set()
    cut_by = {}
    for a_id, b_id in neighbors(ls):
        a, b = byid[a_id], byid[b_id]
        if mode == "G":
            gap = (b["y"] - 0.6 * b["size"]) - (a["y"] + 0.6 * a["size"])
            ov = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            ok = (gap <= th["g"] * b["size"] and ov >= 0.5 * min(a["x1"] - a["x0"], b["x1"] - b["x0"])
                  and abs(a["x0"] - b["x0"]) <= 2.5 * b["size"] and abs(a["size"] - b["size"]) <= 0.12 * b["size"]
                  and not kinds.get(a_id) and not kinds.get(b_id))
            cut = [] if ok else ["G"]
        else:
            cut = judge(a, b, ls, base, kinds, th, rules, pitch, first_of_group=lambda x: x["id"] not in has_above)
        cut_by[f"{a_id}>{b_id}"] = cut
        if not cut:
            union(a_id, b_id)
            has_above.add(b_id)
    groups = {L["id"]: find(L["id"]) for L in ls}
    flags = col_break_flags(ls, groups, kinds, th) if mode != "G" else []
    if mode == "R7j":
        for f in flags:
            union(f["a"], f["b"])
        groups = {L["id"]: find(L["id"]) for L in ls}
    return {"groups": groups, "cut_by": cut_by, "kinds": kinds, "flags": flags}


def col_break_flags(ls, groups, kinds, th):
    """R7 단 넘김 후보: 단 k 의 마지막 본문 줄 a, 단 k+1 의 첫 본문 줄 b — 같은 꼴(R3) 이고 a 가 끝 부호로 안 끝나거나 너비 ≥ width.
    표시만 한다(R7j 가 잇는다)."""
    cols = {}
    for L in ls:
        if is_tiny(L) or kinds.get(L["id"]):
            continue
        cols.setdefault(L["col"], []).append(L)
    out = []
    ks = sorted(cols)
    for k1, k2 in zip(ks, ks[1:]):
        a = max(cols[k1], key=lambda L: L["y"])
        b = min(cols[k2], key=lambda L: L["y"])
        same = abs(a["size"] - b["size"]) <= th["size_tol"] * max(a["size"], b["size"]) and is_bold(a) == is_bold(b) and a["color"] == b["color"]
        open_end = (not ends_sentence(a["text"])) or (a["x1"] - a["x0"]) >= th["width"] * col_width(a, ls)
        if same and open_end:
            out.append({"kind": "col_break", "a": a["id"], "b": b["id"]})
    return out


def page_break_flag(last_ls, first_ls, th=TH):
    """쪽 넘김 후보: 앞 쪽 마지막 단의 마지막 본문 줄 ↔ 다음 쪽 첫 단의 첫 본문 줄(둘 다 kinds 없는 줄만 넘겨 받는다)."""
    if not last_ls or not first_ls:
        return None
    a = max(last_ls, key=lambda L: (L["col"], L["y"]))
    b = min(first_ls, key=lambda L: (L["col"], L["y"]))
    same = abs(a["size"] - b["size"]) <= th["size_tol"] * max(a["size"], b["size"]) and is_bold(a) == is_bold(b) and a["color"] == b["color"]
    open_end = (not ends_sentence(a["text"])) or (a["x1"] - a["x0"]) >= th["width"] * col_width(a, last_ls)
    return {"kind": "page_break", "a": a["id"], "b": b["id"]} if same and open_end else None
