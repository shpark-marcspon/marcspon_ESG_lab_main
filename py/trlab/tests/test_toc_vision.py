"""목차 쪽 찾기·응답 → 항목·읽기 정확 셈·메시지 꼴(가짜 respond, 네트워크 0)."""

import json
import sys
import types

import semgraph  # noqa: F401 — 가짜 llm 을 끼울 자리

from trlab import toc_vision as tv
from trlab.common import Block


def blk(id, page, text, kind="paragraph", y=100, rows=None):
    return Block(id, kind, page, 1, text, (40, y, 300, y + 20), 9.0, "body", 600.0, 800.0, "", rows or [])


def toc_lines(page, n, start=0):
    return [blk(f"p{page}-b{i + 2}", page, f"Section name {start + i} {10 + start + i}", y=120 + 20 * i) for i in range(n)]


def book():
    bs = [blk("p1-b1", 1, "Cover title of the report")]
    bs += [blk("p2-b1", 2, "Contents")] + toc_lines(2, 8)  # 목차 낱말 + 항목 8
    bs += toc_lines(3, 8, 8)  # 이어진 쪽(낱말 없음, 점수 8 ≥ 0.6 × 13)
    bs += toc_lines(4, 8, 16)  # 셋째 이어진 쪽
    bs += toc_lines(5, 8, 24)  # 넷째 — 최대 3쪽이라 안 붙음
    for p in range(6, 12):
        bs.append(blk(f"p{p}-b1", p, "Plain body text without trailing numbers " * 3))
    return bs


def test_find_pages_word_and_continuation_max3():
    assert tv.find_pages(book()) == [2, 3, 4]


def test_find_pages_not_found():
    bs = [blk(f"p{p}-b1", p, "Plain body text") for p in range(1, 12)]
    assert tv.find_pages(bs) == []


def test_accuracy_both_null_is_ok():
    gold = {"items": [{"text": "OVERVIEW", "printed_page": None, "level": 1},
                      {"text": "Company Profile", "printed_page": 7, "level": 2},
                      {"text": "Missing entry here", "printed_page": 9, "level": 2}]}
    items = [{"text": "OVERVIEW", "printed_page": None, "level": 1, "group": ""},
             {"text": "Company profile", "printed_page": 8, "level": 2, "group": "OVERVIEW"},
             {"text": "Extra entry not in gold", "printed_page": 50, "level": 2, "group": ""}]
    a = tv.accuracy(gold, items)
    assert a["found"] == 2 and a["found_pct"] == 66.7
    assert a["printed_ok"] == 1 and a["printed_ok_pct"] == 50.0  # null=null 맞음, 7≠8 틀림
    assert a["level_ok_pct"] == 100.0 and a["items"] == 3


def test_run_with_fake_respond(tmp_path, monkeypatch):
    pngs = []
    for p in (2, 3, 4):
        f = tmp_path / f"p{p:04d}.png"
        f.write_bytes(b"\x89PNG fake")
        pngs.append(f)
    seen = {}

    def fake_respond(model, instr, text, schema, out, tag, effort="low"):
        seen.update(model=model, text=text, tag=tag, effort=effort)
        raw = json.dumps({"items": [{"text": "Section name 0", "printed_page": 10, "level": 2, "group": ""}]})
        row = {"tag": tag, "model": model, "inputTokens": 100, "outputTokens": 10, "costUsd": 0.001}
        with open(out / "calls.jsonl", "a") as fh:
            fh.write(json.dumps(row) + "\n")
        return raw, row

    llm = types.SimpleNamespace(respond=fake_respond, cost=lambda m, i, c, o: 0.001)
    monkeypatch.setitem(sys.modules, "semgraph.llm", llm)
    monkeypatch.setattr(sys.modules["semgraph"], "llm", llm, raising=False)
    monkeypatch.setattr(tv, "OUT2", tmp_path)
    monkeypatch.setattr(tv, "load_blocks", lambda k, l: book())
    monkeypatch.setattr(tv, "render", lambda k, l, pages: pngs)
    monkeypatch.setattr(tv, "gold_path", lambda k, l: tmp_path / "none.json")
    res = tv.run("x", "ko")
    assert res["status"] == "ok" and res["pages"] == [2, 3, 4]
    msg = seen["text"]
    assert isinstance(msg, list) and msg[0]["role"] == "user"
    imgs = [c for c in msg[0]["content"] if c["type"] == "input_image"]
    assert len(imgs) == 3 and all(c["detail"] == "high" and c["image_url"].startswith("data:image/png;base64,") for c in imgs)
    assert msg[0]["content"][0]["type"] == "input_text"
    assert res["items"][0]["printed_page"] == 10 and seen["effort"] == "low" and seen["model"] == "gpt-6.1-sol"
    # 쪽 수가 calls.jsonl 줄에 남는다(다음 예상용)
    assert json.loads(open(tmp_path / "calls.jsonl").read().splitlines()[-1])["pages"] == 3


def test_over_cap_does_not_send(tmp_path, monkeypatch):
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX2/G1/a-ko", "costUsd": 0.399, "inputTokens": 3000,
                                                      "outputTokens": 1000, "pages": 1}) + "\n")
    monkeypatch.setattr(tv, "OUT2", tmp_path)
    monkeypatch.setattr(tv, "load_blocks", lambda k, l: book())
    res = tv.run("x", "ko")
    assert res["status"] == "over_cap"
