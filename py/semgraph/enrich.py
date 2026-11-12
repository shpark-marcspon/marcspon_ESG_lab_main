"""ⓔ 파싱 보강 색인 — 블록 글 앞에 이름표를 붙인다(AI 0). 섹션 제목 · 가까운 앞 제목 · (표만) 바로 앞 문단 앞 120자.
검색 순위에만 쓰고, 예산 셈과 AI 에 넣을 글은 원래 블록 글 그대로다."""


def enriched_texts(doc, units):
    sec = {s["id"]: (s["title"] or "") for s in doc["sections"]}
    out, head, prev = [], {}, None
    for u in units:
        if u.kind == "heading":
            head[u.section_id] = u.text
        tag = [sec.get(u.section_id, ""), head.get(u.section_id, "") if u.kind != "heading" else ""]
        if u.kind == "table" and prev is not None and prev.section_id == u.section_id:
            tag.append(prev.text[:120])
        out.append(" · ".join(t for t in tag if t) + "\n" + u.text)
        if u.kind != "table":
            prev = u
    return out
