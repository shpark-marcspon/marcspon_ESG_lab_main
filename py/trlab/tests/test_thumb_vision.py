"""K5 썸네일 격자 — 지어낸 배열, 가짜 respond(네트워크 0)."""

import json
import struct

import numpy as np

from trlab import thumb_vision as T
from trlab import tx4_pick as X
from trlab.common import Block


def pages(n, h=30, w=20):
    return [np.full((h, w, 3), 200, dtype=np.uint8) for _ in range(n)]


def test_compose_size_and_label_pixels():
    img = T.compose(pages(7), 1, cell_w=20, cols=6)
    assert img.shape == (2 * (30 + T.LABEL_H) + 3 * T.GAP, 6 * 20 + 7 * T.GAP, 3)
    strip = img[T.GAP + 30:T.GAP + 30 + T.LABEL_H, T.GAP:T.GAP + 20]
    assert (strip == 0).any()  # 라벨 띠에 숫자 그림
    no = T.compose(pages(7), 1, cell_w=20, cols=6, label=False)
    assert not (no[T.GAP + 30:T.GAP + 30 + T.LABEL_H] == 0).any()


def test_grid_two_sheets_ranges_and_png(tmp_path):
    res = T.grid("x", "ko", cell_w=20, cols=6, rows=10, arrays=pages(61), out_dir=tmp_path)
    assert [(s["first"], s["last"]) for s in res] == [(1, 60), (61, 61)]
    b = open(res[1]["path"], "rb").read()
    assert b[:8] == b"\x89PNG\r\n\x1a\n" and b[12:16] == b"IHDR"
    w, h = struct.unpack(">II", b[16:24])
    assert (w, h) == (res[1]["w"], res[1]["h"]) and res[1]["px"] == w * h


def blk(id, page, text, size):
    return Block(id, "heading", page, 1, text, (40, 40, 200, 60), size, "body", 600.0, 800.0, "", [])


def test_messages_call_and_blocks(tmp_path, monkeypatch):
    sheets = T.grid("x", "ko", cell_w=20, arrays=pages(65), out_dir=tmp_path / "thumb")
    items = [{"text": "Climate", "level": 1, "printed_page": None}, {"text": "Water", "level": 2, "printed_page": None}]
    msg = T.messages(sheets, items, 65)
    c = msg[0]["content"]
    imgs = [x for x in c if x["type"] == "input_image"]
    assert len(imgs) == 2 and all(x["detail"] == "high" for x in imgs)
    assert "image 2 covers pages 61..65" in c[0]["text"] and "Report of 65 pages" in c[0]["text"]
    # 가짜 respond — 장부·답·labels_read 보존
    bs = [blk("p3-b1", 3, "Climate", 30.0), blk("p3-b2", 3, "Other", 12.0), blk("p9-b1", 9, "Note", 10.0),
          blk("p9-b2", 9, "Small", 25.0)]
    cand = {"rows": {b.id: {"page": b.page, "size": b.font_size, "text": b.text} for b in bs},
            "pages": {str(p): {"divider": False} for p in range(1, 66)}}
    monkeypatch.setattr(X, "OUT4", tmp_path)
    monkeypatch.setattr(X, "load4", lambda k, l, b=False: {"items": items, "blocks": bs, "cand": cand})
    monkeypatch.setattr(T, "grid", lambda k, l: sheets)
    seen = {}

    def fake(model, instr, text, schema, out, tag, effort="low"):
        seen.update(model=model, text=text, tag=tag)
        return json.dumps({"items": [{"idx": 0, "page": 3, "basis": "layout"}, {"idx": 1, "page": 9, "basis": "guess"}],
                           "section_starts": [3, 9], "labels_read": [1, 2, 3]}), {
            "tag": tag, "model": model, "inputTokens": 5000, "outputTokens": 100, "costUsd": 0.001}
    import semgraph.llm as L
    monkeypatch.setattr(L, "respond", fake)
    res = T.call("x-ko", X.SOL)
    assert res["status"] == "ok" and res["answer"]["labels_read"] == [1, 2, 3]
    assert len([x for x in seen["text"][0]["content"] if x["type"] == "input_image"]) == 2
    row = json.loads(open(tmp_path / "calls.jsonl").read().splitlines()[-1])
    assert row["images"] == 2 and row["px"] == sum(s["px"] for s in sheets) and row["tag"] == "TRX4/k5sol/x-ko"
    ids = {b.id: b for b in bs}
    assert T.to_block(items[0], 3, cand, ids) == ("p3-b1", "k5")  # 글이 맞음(≥ 0.55)
    assert T.to_block(items[1], 9, cand, ids) == ("p9-b2", "k5")  # 안 맞음 → 가장 큰 후보
    assert T.to_block(items[1], 40, cand, ids) == (None, "k5-page")
