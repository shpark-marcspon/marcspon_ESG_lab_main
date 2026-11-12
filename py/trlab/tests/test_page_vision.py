"""H3 쪽 이미지 고르기 — 가짜 respond."""

import json
import sys
import types

from trlab import page_vision as PV
from trlab import tx3_pick as T
from trlab.common import Block


def blk(id, page, text, size, order=1):
    return Block(id, "heading", page, order, text, (50, 50, 300, 80), size, "body", 600.0, 800.0, "", [])


def cand():
    bs = [blk("p4-b1", 4, "Running header", 9.0), blk("p4-b2", 4, "Climate Action", 20.0, 2), blk("p5-b1", 5, "Photo", 30.0)]
    rows = {b.id: {"page": b.page, "size": b.font_size, "text": b.text} for b in bs}
    return bs, {"rows": rows, "pages": {}}


def test_to_block_title_largest_none():
    bs, c = cand()
    ids = {b.id: b for b in bs}
    assert PV.to_block({"page_idx": 2, "title": "Climate Action"}, [3, 4, 5], c, ids) == ("p4-b2", 4, "h3")
    assert PV.to_block({"page_idx": 3, "title": "Something else entirely"}, [3, 4, 5], c, ids) == ("p5-b1", 5, "h3-largest")
    assert PV.to_block({"page_idx": 0, "title": ""}, [3, 4, 5], c, ids) == (None, None, "h3-none")
    assert PV.to_block({"page_idx": 1, "title": "x"}, [3, 4, 5], c, ids) == (None, 3, "h3-page")


def test_item_pages():
    assert PV.item_pages([7, 8], 8) == [7, 8]
    assert PV.item_pages(list(range(10, 22)), 15) == [14, 15, 16]


def test_messages_images_detail_high(tmp_path):
    pngs = {}
    for p in (3, 4, 5):
        f = tmp_path / f"p{p:04d}.png"
        f.write_bytes(b"\x89PNG fake")
        pngs[p] = f
    msg = PV.messages({"text": "Climate Action", "level": 2}, [3, 4, 5], pngs)
    c = msg[0]["content"]
    assert c[0]["type"] == "input_text" and '"Climate Action"' in c[0]["text"] and "3, 4, 5" in c[0]["text"]
    imgs = [x for x in c if x["type"] == "input_image"]
    assert len(imgs) == 3 and all(x["detail"] == "high" for x in imgs)
    assert PV.DPI == 100


def test_run_with_fake_respond(tmp_path, monkeypatch):
    bs, c = cand()
    seen = []

    def fake_respond(model, instr, text, schema, out, tag, effort="low"):
        seen.append((model, tag, len([x for x in text[0]["content"] if x["type"] == "input_image"])))
        raw = json.dumps({"page_idx": 2, "title": "Climate Action"})
        return raw, {"tag": tag, "model": model, "inputTokens": 2300, "outputTokens": 20, "costUsd": 0.0003}

    llm = types.SimpleNamespace(respond=fake_respond, cost=lambda m, i, cc, o: 0.0003)
    monkeypatch.setitem(sys.modules, "semgraph.llm", llm)
    monkeypatch.setattr(sys.modules["semgraph"], "llm", llm, raising=False)
    monkeypatch.setattr(T, "OUT3", tmp_path)
    items = [{"text": "Climate Action", "level": 2, "printed_page": 4}]
    win = {"windows": [[3, 4, 5]], "dp": [{"page": 4, "block": "p4-b2", "score": 1.0}], "mids": [4]}
    P = {"items": items, "win": win, "blocks": bs, "cand": c, "g1": {"pages": [2]}}
    monkeypatch.setattr(T, "prep", lambda k, l: P)
    monkeypatch.setattr(T, "grade", lambda k, l, pred, b: {"start_ab": 100.0, "end_ab": 100.0, "block": 100.0,
                                                          "divider_start_ab": None, "wrong_start": []})
    monkeypatch.setattr(PV, "render", lambda k, l, pages: {p: tmp_path / "x.png" for p in pages})
    (tmp_path / "x.png").write_bytes(b"\x89PNG fake")
    res = PV.run("x", "ko", workers=1)
    assert seen == [("gpt-6-luna", "TRX3/h3/x-ko/0", 3)]
    assert res["answers"]["0"]["block"] == "p4-b2" and res["pred"]["items"][0]["start_page"] == 4
    row = json.loads(open(tmp_path / "calls.jsonl").read().splitlines()[-1])
    assert row["pages"] == 3 and "px" in row
