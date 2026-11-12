"""TRX6 para_join — 지어낸 글자 → 줄(같은 y 묶음, x 틈으로 2단 가름, 공백 없음, 위첨자 뗌) · 단 묶음 · R1~R6 켜고 끌 때 짝 판정 ·
R7 표시만 · R7j 잇기 · G 흉내 g 에 따라 갈림 · cut_by. 네트워크·파일 0, 실제 보고서 문장 0."""

from trlab import para_join as pj


def glyph(x, y, code, size=8.0, weight=400, font="Fake-Regular", rgb=(0, 0, 0)):
    return (x, y - size / 2, x + size * 0.9, y + size / 2, size, weight, font, rgb, ord(code))


def word(x, y, text, size=8.0, **kw):
    return [glyph(x + i * size * 0.95, y, ch, size, **kw) for i, ch in enumerate(text)]


def line(i, y, x0, x1, text, size=8.0, bold=0.0, color="#000000", col=1):
    return {"id": f"L{i}", "x0": x0, "x1": x1, "y": y, "size": size, "bold_share": bold, "color": color,
            "text": text, "n": len(text), "col": col}


# ---------- 글자 → 줄 → 단 ----------
def test_lines_group_by_y_split_by_x_gap_and_superscript():
    g = word(100, 100, "가나다") + word(400, 100.3, "라마바") + word(100, 114, "사아.") + [glyph(123.6, 111, "1", size=4.0)]
    g += word(100, 114, "사아.")  # 겹쳐 그린 같은 글자는 하나
    ls = pj.lines(g)
    assert [L["text"] for L in ls] == ["가나다", "라마바", "1", "사아."]  # x 틈 > 2.0 × 크기 → 2단 가름, 위첨자 따로, 공백 없음
    assert [L["id"] for L in ls] == ["L1", "L2", "L3", "L4"] and ls[2]["size"] == 4.0 and ls[3]["n"] == 3
    assert pj.lines(g) == ls  # 재생성해도 같다
    cols = pj.columns(ls, 8.0)
    assert ls[0]["col"] == ls[3]["col"] != ls[1]["col"] and len(cols) == 3  # 위첨자는 x 범위가 안 겹쳐 제 단


def test_lines_mix_sizes_in_one_printed_line():
    g = word(100, 100, "한글") + word(116, 99.6, "AB", size=8.7) + word(132, 100, "이다")
    ls = pj.lines(g)
    assert len(ls) == 1 and ls[0]["text"] == "한글AB이다" and ls[0]["size"] == 8.0


def test_columns_merge_overlapping_x_ranges():
    ls = [line(1, 100, 100, 400, "가"), line(2, 114, 108, 400, "나"), line(3, 128, 120, 300, "다"), line(4, 100, 500, 800, "라")]
    cols = pj.columns(ls, 8.0)
    assert [L["col"] for L in ls] == [1, 1, 1, 2] and len(cols) == 2


# ---------- 규칙 ----------
BASE, W, H = 8.0, 600.0, 800.0


def run(ls, rules=pj.RULES, th=None, mode="R", tables=()):
    return pj.join(ls, list(tables), BASE, W, H, rules=rules, th=th or {}, mode=mode)


def same_group(res, a, b):
    return res["groups"][a] == res["groups"][b]


def test_R1_same_column_and_R2_pitch():
    ls = [line(1, 100, 100, 400, "가나다라"), line(2, 114, 100, 400, "마바사아"), line(3, 140, 100, 400, "자차카타")]
    r = run(ls)
    assert same_group(r, "L1", "L2") and not same_group(r, "L2", "L3") and r["cut_by"]["L2>L3"] == ["R2", "R2r"]
    assert same_group(run(ls, rules=("R1", "R3")), "L2", "L3")  # R2 끄면 잇는다
    assert same_group(run(ls, th={"P": 4.0, "pitch_mult": 3.0}), "L2", "L3")  # P 경계
    ls2 = [line(1, 100, 100, 400, "가나다라"), line(2, 114, 120, 420, "마바사아")]  # 겹침 크고 x0 차 20 > 1.0 × 크기 → R1 은 겹침으로 통과
    assert same_group(run(ls2), "L1", "L2")
    ls3 = [line(1, 100, 100, 200, "가나"), line(2, 114, 170, 420, "마바사아")]  # 겹침 30 < 0.5 × 100, x0 차 70
    r = run(ls3)
    assert "R1" in r["cut_by"]["L1>L2"] and same_group(run(ls3, rules=("R2",)), "L1", "L2")


def test_R3_style_and_runin():
    body = dict(size=8.0, color="#111111")
    ls = [line(1, 100, 100, 400, "가나다라", **body), line(2, 114, 100, 400, "마바사아", size=9.5, color="#111111"),
          line(3, 128, 100, 400, "자차카타", color="#ff0000"), line(4, 142, 100, 400, "파하", **body)]
    r = run(ls)
    assert "R3" in r["cut_by"]["L1>L2"] and "R3" in r["cut_by"]["L2>L3"] and "R3" in r["cut_by"]["L3>L4"]
    r = run(ls, rules=("R1", "R2"))
    assert same_group(r, "L1", "L2") and same_group(r, "L3", "L4")
    # run-in: 굵은 짧은 첫 줄 + 같은 색 본문은 잇고, runin=0 이면 자른다
    ls = [line(1, 100, 100, 160, "머리말", bold=1.0), line(2, 114, 100, 400, "본문입니다본문")]
    assert same_group(run(ls), "L1", "L2") and not same_group(run(ls, th={"runin": 0}), "L1", "L2")


def test_R4_sentence_end_plus_indent_or_bullet():
    ls = [line(1, 100, 100, 400, "문장끝입니다."), line(2, 114, 110, 400, "들여쓴줄"), line(3, 128, 100, 400, "•불릿항목"),
          line(4, 142, 100, 400, "그냥이어진줄입니다")]
    r = run(ls, rules=("R4",))
    assert r["cut_by"]["L1>L2"] == ["R4"] and r["cut_by"]["L2>L3"] == []  # L2 는 끝 부호가 없다
    ls2 = [line(1, 100, 100, 400, "문장끝입니다."), line(2, 114, 100, 400, "•불릿항목")]
    assert run(ls2, rules=("R4",))["cut_by"]["L1>L2"] == ["R4"]
    ls3 = [line(1, 100, 100, 400, "문장끝입니다."), line(2, 114, 100, 400, "같은자리새문장")]
    assert run(ls3, rules=("R4",))["cut_by"]["L1>L2"] == []  # 끝 부호만으로는 안 자른다


def test_R5_short_last_line_with_period():
    ls = [line(1, 100, 100, 400, "꽉찬줄입니다"), line(2, 114, 100, 220, "짧은끝줄."), line(3, 128, 100, 400, "다음문단첫줄입니다"),
          line(4, 142, 100, 220, "짧지만끝부호없음"), line(5, 156, 100, 400, "이어진줄")]
    r = run(ls, rules=("R5",))
    assert r["cut_by"]["L2>L3"] == ["R5"] and r["cut_by"]["L4>L5"] == []
    assert run(ls, rules=("R5",), th={"width": 0.3})["cut_by"]["L2>L3"] == []  # 너비 비 낮추면 통과
    assert run(ls, rules=("R1",))["cut_by"]["L2>L3"] == []  # R5 끄면 잇는다


def test_R6_heading_table_caption_nav_alone():
    ls = [line(1, 100, 100, 300, "큰제목", size=10.5), line(2, 114, 100, 400, "본문첫줄"), line(3, 128, 100, 400, "본문둘째"),
          line(4, 142, 100, 400, "〉표제목", size=6.5), line(5, 156, 100, 400, "표칸글"), line(6, 760, 100, 150, "쪽수", size=7.0),
          line(7, 114, 100, 400, "굵고큰", size=8.6, bold=1.0)]
    ls[6]["y"] = 170
    r = run(ls, tables=[(90.0, 150.0, 410.0, 162.0)])
    k = r["kinds"]
    assert (k["L1"], k["L4"], k["L5"], k["L6"], k["L7"]) == ("heading", "caption", "table", "nav", "heading")
    assert "R6" in r["cut_by"]["L1>L2"] and same_group(r, "L2", "L3") and "R6" in r["cut_by"]["L3>L4"]
    assert len({r["groups"][x] for x in ("L1", "L4", "L5", "L6")}) == 4
    r2 = run(ls, rules=("R1", "R2", "R2r"))  # R6 빼면 제목이 본문에 붙는다
    assert same_group(r2, "L1", "L2")


def test_R7_flags_only_and_R7j_joins():
    ls = [line(4, 86, 100, 300, "왼단앞줄", col=1), line(1, 100, 100, 300, "왼단마지막줄이끝부호없이", col=1),
          line(2, 100, 400, 600, "오른단첫줄", col=2), line(3, 114, 400, 600, "오른단둘째", col=2)]
    r = run(ls)
    assert r["flags"] == [{"kind": "col_break", "a": "L1", "b": "L2"}] and not same_group(r, "L1", "L2")
    assert same_group(run(ls, mode="R7j"), "L1", "L2")
    ls[1]["text"], ls[1]["x1"] = "짧게끝남.", 180.0
    assert run(ls)["flags"] == []


def test_G_mimic_depends_on_g():
    ls = [line(1, 100, 100, 400, "가나다라"), line(2, 117, 100, 400, "마바사아")]  # 틈 = 17 − 9.6 = 7.4 = 0.925 × 크기
    assert not same_group(run(ls, mode="G", th={"g": 0.65}), "L1", "L2")
    assert same_group(run(ls, mode="G", th={"g": 1.0}), "L1", "L2")
    assert run(ls, mode="G", th={"g": 0.65})["cut_by"]["L1>L2"] == ["G"]


def test_cut_by_lists_every_failed_rule():
    ls = [line(1, 100, 100, 220, "짧은끝줄.", size=8.0), line(2, 130, 170, 420, "•다른색굵은", size=9.6, bold=1.0, color="#ff0000")]
    r = run(ls)
    assert set(r["cut_by"]["L1>L2"]) >= {"R1", "R2", "R3", "R4", "R6"}  # L2 는 굵고 커서 R6 제목
