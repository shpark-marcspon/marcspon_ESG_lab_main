"""의미 관계 중 코드로 만드는 넷: 본문 언급 · 쪽 참조 · 같은 지표 · 범위 한정. AI 0.

- mentions_cell: 문단의 숫자(쉼표 뺀 셋째 자리 이상이거나 소수, 연도 꼴 1900~2100 뺌)가 표 칸 값과 같다. 표 다섯 넘게 걸리는 숫자는 버린다.
- page_ref: 「P.11-12」「p.45」「45쪽」 → 그 쪽의 제목·표.
- same_metric: 지표 사전의 낱말을 함께 가진 문단↔표, 표↔표. 문단끼리는 잇지 않는다(얽힘이 너무 커짐).
- scopes: 각주 꼴 블록(「1)」「※」「*」로 시작) → 같은 쪽의 가장 가까운 앞 표.
"""

import re
from collections import defaultdict

NUM = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.,])\d+\.\d+|(?<![\d.,])\d{3,}(?![\d,])")
PAGEREF = re.compile(r"[Pp]\.\s?(\d{1,2})(?:\s*[-~]\s*(\d{1,2}))?(?:\s*,\s*(\d{1,2})(?:\s*[-~]\s*(\d{1,2}))?)?|(\d{1,2})\s?쪽")
FOOT = re.compile(r"^\s*(?:\d{1,2}\)|※|\*)")

# 지표 사전(작게 시작) — 이름: 낱말 목록. 낱말 하나라도 글에 있으면 그 지표.
METRICS = {
    "scope1": ["Scope 1", "직접 배출"], "scope2": ["Scope 2", "간접 배출"], "scope3": ["Scope 3"],
    "ghg_total": ["온실가스 배출량", "온실가스 배출원"], "ghg_intensity": ["배출량 집약도", "원단위"],
    "energy": ["에너지 사용량", "에너지 관리"], "renewable": ["재생에너지"], "ppa": ["PPA"], "rec": ["REC"],
    "process_gas": ["공정가스", "PFCs", "RCS"], "zev": ["무공해차"],
    "water_withdrawal": ["취수량", "용수 사용량", "용수 취수"], "water_reuse": ["재이용"],
    "water_restore": ["수자원 환원", "Water Positive"], "wastewater": ["폐수", "방류"],
    "waste": ["폐기물 발생", "폐기물 배출", "폐기물 처리"], "waste_recycle": ["재활용률", "폐기물 재활용"],
    "landfill_zero": ["매립 제로"], "recycled_plastic": ["재활용 플라스틱", "재생 플라스틱"],
    "ewaste": ["폐전자제품", "폐제품 수거", "폐제품"], "packaging": ["포장재"],
    "air": ["대기오염물질", "미세먼지", "NOx", "SOx"], "chemical": ["화학물질", "유해물질"],
    "biodiversity": ["생물다양성", "생태계"], "power_efficiency": ["소비전력", "에너지 효율", "저전력"],
    "pcf": ["PCF", "탄소발자국"],
    "safety": ["LTIR", "중대재해", "산업재해", "재해율"], "headcount": ["임직원 수", "임직원 현황"],
    "turnover": ["이직률", "퇴직"], "women": ["여성 관리자", "여성 임원", "여성"], "training": ["교육 시간", "교육시간", "인당 교육"],
    "human_rights": ["인권 교육", "인권 실사", "인권 영향"], "supplier_audit": ["제3자 검증", "현장점검", "협력회사 점검"],
    "supplier_ghg": ["협력회사 온실가스", "감축 목표를 수립"], "supplier_payment": ["대금 지급", "현금성 결제"],
    "community": ["사회공헌", "봉사"], "privacy": ["개인정보"], "security": ["정보보호", "보안"],
    "ai_ethics": ["AI 윤리", "인공지능 경영시스템", "ISO/IEC 42001"], "quality": ["품질", "제품 안전", "리콜"],
    "compliance": ["준법", "부패", "윤리 위반"], "board": ["이사회", "사외이사"], "revenue": ["매출", "영업이익"],
}


def _nums(text):
    out = set()
    for m in NUM.findall(text):
        v = m.replace(",", "")
        if re.fullmatch(r"(19|20)\d\d", v):
            continue
        out.add(v)
    return out


def _metrics(text):
    low = text.lower()
    return {k for k, ws in METRICS.items() if any(w.lower() in low for w in ws)}


def code_edges(units):
    edges = []
    tables = [u for u in units if u.kind == "table"]
    # mentions_cell
    cell_index = defaultdict(set)
    for t in tables:
        for r in t.rows:
            for c in r:
                for v in _nums(c):
                    cell_index[v].add(t.id)
    for u in units:
        if u.kind != "paragraph":
            continue
        for v in _nums(u.text):
            ts = cell_index.get(v, ())
            if 0 < len(ts) <= 5:
                edges.extend((u.id, t, "mentions_cell") for t in ts)
    # page_ref
    by_page = defaultdict(list)
    for u in units:
        if u.kind in ("heading", "table"):
            by_page[u.page].append(u.id)
    for u in units:
        for m in PAGEREF.finditer(u.text):
            g = m.groups()
            pages = set()
            for a, b in ((g[0], g[1]), (g[2], g[3])):
                if a:
                    lo = int(a)
                    hi = int(b) if b else lo
                    if b and hi < lo:  # 「P.11-12」 꼴은 그대로, 「P.111-12」 같은 것은 버림
                        continue
                    pages.update(range(lo, min(hi, lo + 4) + 1))
            if g[4]:
                pages.add(int(g[4]))
            for p in pages:
                if p != u.page:
                    edges.extend((u.id, t, "page_ref") for t in by_page.get(p, [])[:6])
    # same_metric
    tm = {t.id: _metrics(t.text) for t in tables}
    for u in units:
        if u.kind != "paragraph":
            continue
        um = _metrics(u.text)
        if not um:
            continue
        for t in tables:
            shared = um & tm[t.id]
            if shared and (len(shared) >= 2 or len(um) == 1):
                edges.append((u.id, t.id, "same_metric"))
    for i, a in enumerate(tables):
        for b in tables[i + 1:]:
            if len(tm[a.id] & tm[b.id]) >= 2:
                edges.append((a.id, b.id, "same_metric"))
    # scopes
    last_table = {}
    for u in units:
        if u.kind == "table":
            last_table[u.page] = u.id
        elif u.kind == "paragraph" and FOOT.match(u.text) and u.page in last_table:
            edges.append((u.id, last_table[u.page], "scopes"))
    return edges


# ---- v2 (2026-10-29, 관계 표본 채점 뒤 · 검색 채점 전): 표본에서 틀린 꼴 셋을 막는다 ----
INDEX_SECTIONS = {"s2", "s53", "s54", "s55"}  # 목차 · GRI · TCFD · SASB 대조표 — 쪽 번호·코드만 든 표
OTHER_DOC = re.compile(r"(사업보고서|보고서|웹사이트|홈페이지)\s*\S{0,6}\s*$")


def _toc_like(u):
    """쪽 번호 차림표 꼴: 행 대부분이 「이름 | 두 자리 이하 숫자」."""
    if u.kind != "table" or not u.rows:
        return False
    n = sum(1 for r in u.rows if len([c for c in r if c]) == 2 and re.fullmatch(r"\d{1,2}", [c for c in r if c][-1]))
    return n >= len(u.rows) / 2


def code_edges_v2(units):
    U = {u.id: u for u in units}
    out = []
    for a, b, rel in code_edges(units):
        if rel in ("mentions_cell", "same_metric") and (
                U[a].section_id in INDEX_SECTIONS or U[b].section_id in INDEX_SECTIONS or _toc_like(U[a]) or _toc_like(U[b])):
            continue
        if rel == "mentions_cell":
            # 숫자 넷째 자리 이상(소수점 빼고 셈)이고, 같은 지표 낱말도 함께 있어야 한다 — 짧은 수는 우연 일치가 많음
            shared = _nums(U[a].text) & {x for r in U[b].rows for c in r for x in _nums(c)}
            if not any(len(v.replace(".", "")) >= 4 for v in shared) or not (_metrics(U[a].text) & _metrics(U[b].text)):
                continue
        if rel == "page_ref":
            m = PAGEREF.search(U[a].text)
            if m and OTHER_DOC.search(U[a].text[:m.start()]):
                continue
        if rel == "same_metric":
            # 지표 낱말이 표의 행 머리(첫 칸)에 있어야 한다
            heads = " ".join(r[0] for r in U[b].rows if r).lower() if U[b].kind == "table" else ""
            terms = [w.lower() for k in _metrics(U[a].text) & _metrics(U[b].text) for w in METRICS[k]]
            if not any(t in heads for t in terms):
                continue
        out.append((a, b, rel))
    return out
