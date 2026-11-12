"""evalrig.common.report — md 결정성·메타 줄·% 칸."""
import json

import pytest

from evalrig.common import gold as G
from evalrig.common import report as R
from evalrig.common.match import ratio


def _gold(set_id="synthetic-r", frozen=True):
    gs = G.new(
        set_id=set_id, module="translation", unit="synthetic.r@1",
        provenance={"kind": "runner_made", "by": "test", "from": [], "at": "2026-11-09"},
        review={"status": "none", "by": None, "at": None, "note": None, "sample": None},
        pending=[{"what": "가상", "question": "MARC-ENT-0", "slot": "consultant"}],
        items=[{"a": 1}], coverage="exhaustive", synthetic=True,
    )
    if frozen:
        gs["frozen"] = {"sha16": G.sha16(gs), "at": "2026-11-09"}
    return gs


ROWS = [
    {"gold": "synthetic-r", "자리": "A", "재현%": ratio(1, 3), "n": 3, "bleu": 12.3456},
    {"gold": "NO_GOLD", "자리": "B", "재현%": ratio(0, 0), "n": 0, "bleu": None},
]


def test_header_line_always(tmp_path):
    jp, mp = R.write(tmp_path, "t", ROWS, [_gold()])
    md = mp.read_text(encoding="utf-8")
    gs = _gold()
    assert f"`synthetic-r` · sha16 `{gs['frozen']['sha16']}` · provenance `runner_made` · review `none`" in md
    assert "pending consultant:MARC-ENT-0" in md
    assert "| synthetic-r(runner_made/none) | A | 33.3% (1/3) | 3 | 12.346 |" in md
    assert "| 정답 없음 | B | — (0/0) | 0 | — |" in md
    doc = json.loads(jp.read_text(encoding="utf-8"))
    assert doc["gold"][0]["setId"] == "synthetic-r" and len(doc["evalrigSha16"]) == 16
    assert "generatedAt" in doc and "generatedAt" not in md


@pytest.mark.parametrize("bad,msg", [(None, "비었음"), ([], "비었음"), ([{"setId": "x"}], "메타 틀림")])
def test_missing_gold_meta_raises(tmp_path, bad, msg):
    with pytest.raises(ValueError, match=msg):
        R.write(tmp_path, "t", [{"gold": "NO_GOLD", "n": 1}], bad)  # NO_GOLD 줄만이라도 메타 빠짐은 실패


def test_unfrozen_gold_raises(tmp_path):
    with pytest.raises(ValueError, match="sha16"):
        R.write(tmp_path, "t", ROWS, [_gold(frozen=False)])


def test_tampered_frozen_gold_raises(tmp_path):
    gs = _gold()
    gs["items"].append({"a": 2})  # 얼린 뒤 고침
    with pytest.raises(ValueError, match="얼린 뒤"):
        R.write(tmp_path, "t", ROWS, [gs])
    bad = _gold()
    bad["provenance"]["kind"] = "guess"
    with pytest.raises(ValueError, match="메타 틀림"):
        R.write(tmp_path, "t", ROWS, [bad])


def test_pct_names_and_escape(tmp_path):
    for c in ("recall", "정밀", "f1", "재현 비"):
        with pytest.raises(ValueError, match="ratio"):
            R.write(tmp_path, "t", [{"gold": "synthetic-r", c: 0.5}], [_gold()])
    _, mp = R.write(tmp_path, "t", [{"gold": "synthetic-r", "a|b": {"x": "p|q"}}], [_gold()])
    md = mp.read_text(encoding="utf-8")
    assert "a\\|b" in md and "p\\|q" in md


def test_row_gold_must_be_known(tmp_path):
    with pytest.raises(ValueError, match="gold"):
        R.write(tmp_path, "t", [{"gold": "other", "n": 1}], [_gold()])
    with pytest.raises(ValueError):
        R.write(tmp_path, "t", [{"n": 1}], [_gold()])


def test_pct_column_needs_ratio(tmp_path):
    with pytest.raises(ValueError, match="ratio"):
        R.write(tmp_path, "t", [{"gold": "synthetic-r", "재현%": 0.5}], [_gold()])
    with pytest.raises(ValueError, match="ratio"):
        R.write(tmp_path, "t", [{"gold": "synthetic-r", "hit_rate": 1}], [_gold()])


def test_no_gold_sentinel(tmp_path):
    _, mp = R.write(tmp_path, "t", [{"gold": "NO_GOLD", "n": 1}], R.NO_GOLD)
    assert "정답 셋 없음 — 코드 지표만" in mp.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        R.write(tmp_path, "t", [{"gold": "synthetic-r", "n": 1}], R.NO_GOLD)


def test_unmeasurable_cell(tmp_path):
    from evalrig.common.match import unmeasurable
    _, mp = R.write(tmp_path, "t", [{"gold": "synthetic-r", "정밀%": unmeasurable("partial")}], [_gold()])
    assert "셀 수 없음(partial)" in mp.read_text(encoding="utf-8")


def test_coverage_synthetic_in_header(tmp_path):
    _, mp = R.write(tmp_path, "t", ROWS, [_gold()])
    assert "coverage `exhaustive` · synthetic `true`" in mp.read_text(encoding="utf-8")


def test_md_deterministic(tmp_path):
    _, a = R.write(tmp_path / "1", "t", ROWS, [_gold()], notes=["메모 하나"])
    _, b = R.write(tmp_path / "2", "t", ROWS, [_gold()], notes=["메모 하나"])
    assert a.read_bytes() == b.read_bytes()


def test_duplicate_setid_raises(tmp_path):
    with pytest.raises(ValueError, match="중복"):
        R.write(tmp_path, "t", ROWS, [_gold(), _gold()])
