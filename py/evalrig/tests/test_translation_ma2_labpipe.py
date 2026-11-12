"""MA2 labpipe — 지은 자료만(네트워크 0, esg-app 파일을 읽지 않음 — 지은 prompts 모듈)."""
import json
import types
from dataclasses import dataclass

import pytest

from evalrig.translation import labpipe as L


# ---------- 지은 prompts 모듈(1차 prompts.py 의 이름·조립 틀만 흉내) ----------
def _fake_prompts():
    P = types.SimpleNamespace()
    P.SUBJECT_PRONOUN_PROMPT = "\nSubject pronouns — MANDATORY:\nUse we after the first name.\n"
    P.FRAGMENTARY_CONTENT_PROMPT = "\nHeadings: head noun first.\n"
    P.SUBJECT_FRAGMENT_RULES_BLOCK = "\n".join([
        f"\n\nSubject pronoun consistency guidance:\n{P.SUBJECT_PRONOUN_PROMPT.strip()}",
        f"\n\nFragmentary content (heading/table/chart) translation rules:\n{P.FRAGMENTARY_CONTENT_PROMPT.strip()}",
    ]).removeprefix("\n\n")
    P.STYLE_A_PROMPT = "Style A."

    def build_company_style_prompt(part, name, subj, isd, peer, *, subject_fragment_rules=P.SUBJECT_FRAGMENT_RULES_BLOCK):
        return f"style {part} {name} {subj} {isd}\n\n{subject_fragment_rules}"

    P.build_company_style_prompt = build_company_style_prompt
    P.page_system_prompt = lambda style, a, isd: f"BASE {isd}\n\n{style}\n\n{a}"
    P.table_batch_user = lambda g, payload: f"[G]\n{g}\n[I]\n{payload}"
    return P


@dataclass
class _B:
    id: str
    page: int
    order: int
    kind: str
    text: str
    region: str = "body"


GLOSS = [{"kr": "가상위원회", "en": "Virtual Committee"}, {"kr": "물관리", "en": "water stewardship"}]


def _blocks(spec):
    """spec = [(쪽, 국문 글자 수)] → 블록(쪽마다 하나)."""
    return [{"id": f"p{p}-b1", "page": p, "order": 1, "kind": "paragraph", "ko": "가" * n} for p, n in spec]


# ---------- ① 입력 ----------
def test_input_blocks_body_only_and_range():
    bs = [_B("p1-b1", 1, 1, "paragraph", "가"), _B("p2-b2", 2, 2, "paragraph", "나"), _B("p2-b1", 2, 1, "heading", "다"),
          _B("p2-b9", 2, 9, "paragraph", "꼬리", region="footer"), _B("p3-b1", 3, 1, "paragraph", "라"),
          _B("p2-b5", 2, 5, "paragraph", "  ")]
    got = L.input_blocks(bs, 2, 3)
    assert [b["id"] for b in got] == ["p2-b1", "p2-b2", "p3-b1"]


# ---------- ② 구획 ----------
def test_sections_from_toc_starts():
    items = [{"start_page": 3}, {"start_page": 11}, {"start_page": 11}, {"start_page": 13}, {"start_page": 20}]
    assert L.sections(items, 10, 15) == [(10, 10), (11, 12), (13, 15)]
    assert L.sections([{"start_page": 3}], 10, 15) == [(10, 15)]  # 범위 안 시작 없음 = 한 구획


def test_toc_calls_cut_at_page_boundary():
    bs = _blocks([(1, 5000), (2, 5000), (3, 5000), (4, 13000), (5, 100)])
    calls = L.toc_calls(bs, [(1, 5)], max_chars=12000)
    assert [c["pages"] for c in calls] == [[1, 2], [3], [4], [5]]  # 쪽 안을 자르지 않음, 넘는 쪽은 혼자
    assert all(c["ko_chars"] <= 12000 or len(c["pages"]) == 1 for c in calls)
    assert [c["call"] for c in calls] == [0, 1, 2, 3]


def test_toc_calls_page_not_split_across_calls():
    bs = [{"id": f"p{p}-b{k}", "page": p, "order": k, "kind": "paragraph", "ko": "가" * 4000} for p in (1, 2) for k in (1, 2)]
    calls = L.toc_calls(bs, [(1, 2)], max_chars=12000)
    assert [c["pages"] for c in calls] == [[1], [2]]
    assert [c["ids"] for c in calls] == [["p1-b1", "p1-b2"], ["p2-b1", "p2-b2"]]  # 한 쪽의 블록은 한 호출에


def test_toc_calls_never_cross_section():
    bs = _blocks([(1, 10), (2, 10), (3, 10)])
    calls = L.toc_calls(bs, [(1, 1), (2, 3)])
    assert [(c["section"], c["pages"]) for c in calls] == [(0, [1]), (1, [2, 3])]


def test_page_calls_one_per_page():
    bs = _blocks([(1, 10), (2, 10), (3, 10)])
    calls = L.page_calls(bs, [(1, 1), (2, 3)])
    assert [(c["section"], c["pages"]) for c in calls] == [(0, [1]), (1, [2]), (1, [3])]


# ---------- ③ 지시·답 읽기 ----------
def test_we_subject_block_byte_same():
    P = _fake_prompts()
    assert L.subject_rules(P, "we") == P.SUBJECT_FRAGMENT_RULES_BLOCK
    assert L.subject_block(P, P.SUBJECT_PRONOUN_PROMPT) == P.SUBJECT_FRAGMENT_RULES_BLOCK  # third 틀 = 1차 틀
    default = P.page_system_prompt(P.build_company_style_prompt("general", "N", "S", L.IS, None), P.STYLE_A_PROMPT, L.IS)
    assert L.system_prompt(P, "N", "S", "we") == default


def test_third_swaps_only_subject_piece():
    P = _fake_prompts()
    t = L.subject_rules(P, "third")
    assert t != P.SUBJECT_FRAGMENT_RULES_BLOCK
    assert t.endswith(P.FRAGMENTARY_CONTENT_PROMPT.strip()) and L.THIRD_SUBJECT.strip() in t
    assert '"we" / "our" / "us"' in L.THIRD_SUBJECT and "never" in L.THIRD_SUBJECT


def test_user_prompt_glossary_only_present_keys():
    P = _fake_prompts()
    u = L.user_prompt(P, [{"id": "a", "ko": "가상위원회 운영"}], GLOSS)
    assert "가상위원회 -> Virtual Committee" in u and "물관리" not in u
    assert json.loads(u.split("[I]\n", 1)[1]) == {"items": [{"id": "a", "text": "가상위원회 운영"}]}


def test_read_items_branches():
    raw = json.dumps({"items": [{"id": "a", "translation": " A "}, {"id": "a", "translation": "A2"}, {"id": "z", "translation": "Z"},
                                {"id": "c", "translation": ""}]})
    r = L.read_items(raw, ["a", "b", "c"])
    assert r["parsed"] and r["en"] == {"a": "A"}
    assert r["missing"] == ["b", "c"] and r["extra"] == ["z"] and r["dup"] == ["a"] and r["empty"] == ["c"]
    bad = L.read_items("not json", ["a"])
    assert not bad["parsed"] and bad["missing"] == ["a"]


# ---------- ④ 숫자 규칙 ----------
@pytest.mark.parametrize("ko,en,miss", [
    ("배출량은 12,345 톤이다.", "Emissions were 12345 tons.", []),  # 쉼표 무시
    ("비율은 12.50%이다.", "The ratio was 12.5%.", []),  # 끝 0 무시
    ("매출 3조 2,345억 원", "sales of KRW 3.2 trillion", []),  # 조·억 붙은 수 뺌
    ("3천 명과 25만 명", "3,000 and 250,000 people", []),  # 천·만 붙은 수 뺌
    ("2023~2025 기간", "the period from 2023 to 2025", []),  # 연도 범위 꼴 뺌
    ("2023-25 계획", "the plan", []),
    ("주석 1) 참조", "see note", []),  # 한 글자 수 뺌
    ("감축량 47 톤, 2024년", "a reduction of 74 tons in 2024", ["47"]),
])
def test_number_rule(ko, en, miss):
    assert L.missing_numbers(ko, en) == miss


def test_number_rule_scale_suffix_with_space():
    assert L.ko_numbers("12 억 원과 34") == ["34"]


# ---------- ④ 문장 수 거름 ----------
def test_omit_check_excludes_short_en():
    ko = "가나다라 마바입니다. 사아자차 카타입니다."
    assert L.omit_check(ko, "Short.", "paragraph")["applies"] is False  # EN < 8자
    r = L.omit_check(ko, "This is only one sentence here.", "paragraph")
    assert r["applies"] and r["flagged"]
    assert L.omit_check(ko, "x", "table")["applies"] is False


def test_code_checks_and_flags():
    b = {"id": "a", "page": 1, "order": 1, "kind": "paragraph", "ko": "가상위원회는 물관리를 47 곳에서 했다."}
    c = L.code_checks(b, "The committee managed water at 47 sites 남음.", GLOSS)
    assert c["term"] == ["가상위원회", "물관리"] and c["num"] == [] and c["hangul"]
    assert L.flags_of(c) == ["term", "hangul"]
    assert L.flags_of(L.code_checks(b, None, GLOSS)) == ["missing"]


# ---------- ⑤ 숫자 되묻기 ----------
def test_num_reask_read():
    _, text, schema = L.build_num_reask("가 47 나 58", "a b", ["47", "58"])
    assert "(1) 47" in text and "(2) 58" in text and schema["required"] == ["missing", "reason"]
    r = L.read_num_reask(json.dumps({"missing": [2, 9], "reason": "x"}), ["47", "58"])
    assert r["missing"] == ["58"] and r["oob"] == [9]


# ---------- ⑦ 구획 안/사이 ----------
def test_split_inside_between():
    secs = [(1, 2), (3, 4)]
    rows = [{"term": "가", "page": 1, "kind": "exact", "key": "alpha board", "pair": "x"},
            {"term": "가", "page": 2, "kind": "exact", "key": "alpha council", "pair": "y"},
            {"term": "나", "page": 1, "kind": "exact", "key": "beta", "pair": "x"},
            {"term": "나", "page": 3, "kind": "exact", "key": "gamma", "pair": "z"}]
    flags = [{"kr": "가", "stage": "S1", "renderings": [["alpha board", 1, [1]], ["alpha council", 1, [2]]]},
             {"kr": "나", "stage": "S1", "renderings": [["beta", 1, [1]], ["gamma", 1, [3]]]}]
    r = L.split_inside_between(flags, rows, secs)
    assert r["inside"] == ["가"] and r["between"] == ["나"]


def test_split_merges_contained_keys_after_s1():
    secs = [(1, 2), (3, 4)]
    rows = [{"term": "가", "page": 1, "kind": "exact", "key": "board", "pair": "x"},
            {"term": "가", "page": 2, "kind": "exact", "key": "the board of directors", "pair": "y"},
            {"term": "가", "page": 3, "kind": "exact", "key": "bod", "pair": "z"}]
    f = {"kr": "가", "stage": "S4", "renderings": [["the board of directors", 2, [1, 2]], ["bod", 1, [3]]]}
    assert L.split_inside_between([f], rows, secs)["between"] == ["가"]  # board ⊂ board of directors 로 접혀 구획 1 은 한 키
    assert L.residual_incons_blocks([f], rows) == {"z": ["가"]}


# ---------- ⑧ 고침 대상 ----------
def test_fix_targets_exclude_chk_only_short_and_cap():
    bs = [{"id": f"b{i}", "page": i, "order": 1} for i in range(1, 7)]
    en = {"b1": "Long enough text", "b2": "Fig. 1", "b3": "Long enough text", "b4": "Long enough text",
          "b5": "Long enough text", "b6": "Long enough text"}
    reasons = {"b1": {"chk": [{"category": "term"}]}, "b2": {"chk": [{"category": "other"}]}, "b3": {"term": ["가"]},
               "b4": {"num": ["47"], "chk": [{}]}, "b5": {"incons": ["가"]}, "b6": {}}
    r = L.fix_targets(bs, en, reasons, cap=3)
    assert r["excluded"] == {"b2": "chk_only_short"}
    assert r["targets"] == ["b3", "b4", "b5"] and r["over_cap"] == ["b1"]  # 우선: term > num/omit > incons > chk
    r2 = L.fix_targets(bs, {**en, "b2": "Fig. 1"}, {"b2": {"term": ["가"], "chk": [{}]}})
    assert r2["targets"] == ["b2"]  # 이슈만이 아니면 짧아도 고침


def test_fix_input_lists_reasons():
    b = {"id": "a", "ko": "가상위원회 47"}
    t = L.fix_input(b, "The committee", GLOSS, {"term": ["가상위원회"], "num": ["47"]})
    assert '"Virtual Committee"' in t and "value 47" in t and '"id": "a"' in t


# ---------- ⑨ · 쪽 잇기 ----------
def test_residual_counts():
    bs = [{"id": "a", "page": 1, "order": 1, "kind": "paragraph", "ko": "가상위원회"},
          {"id": "b", "page": 1, "order": 2, "kind": "heading", "ko": "제목"}]
    r = L.residual(bs, {"a": "Virtual Committee", "b": "Title 남"}, GLOSS, unfixed=["a"])
    assert r["by_kind"] == {"hangul": 1} and r["blocks"] == 2 and r["unfixed"] == 1


def test_page_join_order():
    bs = [{"id": "b", "page": 1, "order": 2}, {"id": "a", "page": 1, "order": 1}, {"id": "c", "page": 2, "order": 1},
          {"id": "d", "page": 2, "order": 2}]
    assert L.page_join(bs, {"a": "A.", "b": "B.", "c": "C."}) == {1: "A. B.", 2: "C."}


def test_fix_targets_skip_block_without_en():
    bs = [{"id": "a", "page": 1, "order": 1}, {"id": "b", "page": 1, "order": 2}]
    r = L.fix_targets(bs, {"a": "Long enough text"}, {"a": {"term": ["가"]}, "b": {"term": ["가"]}})
    assert r["targets"] == ["a"]  # EN 이 없는 블록(번역 빠짐)은 고칠 수 없다 — 대상 밖
