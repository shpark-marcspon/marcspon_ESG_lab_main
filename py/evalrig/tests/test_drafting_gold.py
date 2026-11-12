"""evalrig.drafting.gold — 지은 SG1 꼴 파일."""
import json

from evalrig.common import gold as G
from evalrig.drafting.gold import build, passages, read_facts


def _files(tmp_path, jsonl=False):
    facts = {"D02": {"facts": [{"metric": "가상 지표", "value": "1,234", "unit": "t", "year": "2030", "match": ["1,234"]}]},
             "D01": {"facts": [{"metric": "m", "value": "v", "unit": "u", "year": "y", "match": ["567"]},
                               {"metric": "m2", "value": "v", "unit": "u", "year": "y", "match": ["890", "8.9"]}]},
             "D03": {"facts": []}}
    fp = tmp_path / ("facts.part.jsonl" if jsonl else "facts.json")
    if jsonl:
        fp.write_text("".join(json.dumps({"k": k, "v": v}) + "\n" for k, v in facts.items()), encoding="utf-8")
    else:
        fp.write_text(json.dumps(facts), encoding="utf-8")
    q = tmp_path / "q.json"
    q.write_text(json.dumps([{"id": k, "type": "drafting", "text": f"질의 {k}"} for k in ("D01", "D02", "D03")]), encoding="utf-8")
    c = tmp_path / "core.json"
    c.write_text(json.dumps({"D01": [{"id": "P1", "label": "core"}, {"id": "P9", "label": "related"}], "D02": [{"id": "P2", "label": "core"}]}), encoding="utf-8")
    p = tmp_path / "p.json"
    p.write_text(json.dumps([{"id": "P1", "text": "단락 567"}, {"id": "P2", "text": "단락 1,234"}]), encoding="utf-8")
    return {"facts": fp, "queries": q, "core": c, "passages": p}


def test_build_meta_and_items(tmp_path):
    for jsonl in (False, True):
        d = tmp_path / str(jsonl)
        d.mkdir()
        paths = _files(d, jsonl)
        gs = build(0, paths, at="2026-01-01", rel_to=tmp_path)
        assert G.check(gs)
        assert gs["setId"] == "drafting-sg1-sam" and gs["provenance"]["kind"] == "agent_draft_checked"
        assert gs["review"]["status"] == "agent_sampled" and gs["review"]["sample"] == {"ok": 118, "n": 127}
        assert gs["coverage"] == "partial" and gs["pending"][0]["question"] == "MARC-ENT-607"
        assert [(i["leaf"], i["idx"]) for i in gs["items"]] == [("D01", 0), ("D01", 1), ("D02", 0)]  # 질의 차례, 빈 질의 뺌
        assert gs["meta"]["leaves"]["D01"] == {"text": "질의 D01", "core": ["P1"]}
        assert all(isinstance(f, dict) and len(f["sha256"]) == 16 for f in gs["provenance"]["from"])


def test_build_deterministic(tmp_path):
    paths = _files(tmp_path)
    a = G.dumps(build(3, paths, at="2026-01-01", rel_to=tmp_path))
    b = G.dumps(build(3, paths, at="2026-01-01", rel_to=tmp_path))
    assert a == b and '"setId": "drafting-sg1-b4"' in a
    b4 = build(3, paths, at="2026-01-01", rel_to=tmp_path)
    assert b4["provenance"]["kind"] == "agent_draft"
    assert b4["review"] == {"status": "none", "by": None, "at": None, "note": "SG1i 별도 에이전트 초안, 표본 검토 없음", "sample": None}
    assert "세 책 합친 15 질의" in build(1, paths, at="2026-01-01", rel_to=tmp_path)["review"]["note"]


def test_readers(tmp_path):
    paths = _files(tmp_path, jsonl=True)
    assert set(read_facts(paths["facts"])) == {"D01", "D02", "D03"}
    assert passages(paths) == {"P1": "단락 567", "P2": "단락 1,234"}
