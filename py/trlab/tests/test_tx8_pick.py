"""TRX8 tx8_pick — 덮어쓰기 넷 · G1 자리 상한 · 후보 ①②③ · 보고 이름 0. 지어낸 자료, 네트워크 0, 새 책 파일 0."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from trlab import thumb_vision as TV
from trlab import tx4_pick as X
from trlab import tx8_pick as P8
from trlab.common import Block


def test_env_and_overrides():
    # import 뒤 속성 넷이 덮어써져 있고, 깨끗한 프로세스에서는 env setdefault 로 TRX2~4 자리가 out/trx8·gold-trx8 이 된다
    assert X.OUT4 == P8.OUT8 and X.TOTAL_CAP4 == 0.6 and X.BEST_BASE == ("K1", "K3")
    assert "TRX8/" in X.SITE_PREFIX4["sol"]
    code = ("import trlab.tx8_pick as T, trlab.glyphs as g, trlab.tx3_pick as t;"
            "print(str(g.GOLD2), str(g.OUT2), str(t.OUT3), str(T.GOLD8), str(T.OUT8))")
    py_dir = str(Path(__file__).resolve().parents[2])  # 이 저장소의 py/ — 워크트리 경로를 박지 않는다
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONPATH": py_dir}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=py_dir)
    gold2, out2, out3, gold8, out8 = out.stdout.split()
    assert gold2 == gold8 and out2 == out8 and out3 == out8, out.stderr


def test_g1_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(P8, "OUT8", tmp_path)
    (tmp_path / "calls.jsonl").write_text(json.dumps({"tag": "TRX2/G1/x-ko", "model": X.SOL, "costUsd": 0.095}) + "\n")
    monkeypatch.setattr(P8.glyphs, "load_blocks", lambda k, l: [])
    monkeypatch.setattr(P8.G, "find_pages", lambda b: [3])
    monkeypatch.setattr(P8.G, "estimate", lambda n: (1000, 500, 0.01))
    called = []
    monkeypatch.setattr(P8.G, "run", lambda k, l: called.append(k))
    r = P8.g1("nbx")
    assert r["status"] == "over_cap" and not called          # 0.095 + 0.01 > 0.1 — 보내지 않음
    assert P8.ledger()["G1"]["usd"] == 0.095


def blk(id, page, text, size=20.0):
    return Block(id, "heading", page, 1, text, (40, 40, 200, 60), size, "body", 600.0, 800.0, "", [])


def test_cand1_alpha_on_revert():
    items = [{"text": t, "level": 2, "printed_page": None} for t in ("Alpha", "Beta", "Gamma")]
    bs = [blk("p3-b1", 3, "Alpha"), blk("p5-b1", 5, "Beta"), blk("p9-b1", 9, "Beta"), blk("p6-b1", 6, "Gamma")]
    ids = {b.id: b for b in bs}
    cand = {"rows": {b.id: {"page": b.page, "size": 20.0} for b in bs}, "pages": {}}
    beta = [("p3-b1", 3, "ai"), ("p5-b1", 5, "ai"), ("p6-b1", 6, "ai")]
    k5 = {1: {"idx": 1, "page": 9}, 2: {"idx": 2, "page": 6}}
    pk, changed, bad = P8.cand1_merge(beta, [1, 2], k5, items, cand, ids)
    assert changed == [1, 2] and pk[1] == ("p9-b1", 9, "k5")    # 되돌린 항목만 α 블록으로
    assert bad == [2] and pk[2] == beta[2]                       # 9 → 6 은 차례를 거슬러 base
    pk2, changed2, bad2 = P8.cand1_merge(beta, [], k5, items, cand, ids)
    assert pk2 == beta and not changed2 and not bad2             # 되돌림 0 이면 β 그대로


def test_cand2_drop_common_words():
    w = lambda text, li=0, bold=False: (text, 9.0, bold, (0, 0, 0), li, 20.0)
    per = {p: {"top": [w("회사"), w("기후" if p <= 3 else "환경", 0, p == 2)], "bot": []} for p in range(1, 11)}
    common = P8.common_words(per, 10)
    assert common == {("top", 0, "회사")}                        # 10/10 쪽 같은 띠·줄 → 빠짐, 「기후」 3/10 은 남음
    kept = P8.drop_words(per[1]["top"], "top", common)
    assert [x[0] for x in kept] == ["기후"]
    per8 = {p: {"top": [w("회사") if p <= 8 else w("다른")], "bot": []} for p in range(1, 11)}
    assert P8.common_words(per8, 10) == set()                     # 8/10 은 문턱 90% 아래
    assert ("top", 1, "회사") not in P8.common_words({p: {"top": [w("회사", 1 if p == 1 else 0)], "bot": []} for p in range(1, 11)}, 10)


def test_cand3_sheets_30(tmp_path, monkeypatch):
    monkeypatch.setattr(P8, "OUT8", tmp_path)
    monkeypatch.setattr(X, "OUT4", tmp_path)   # save_run 도 tmp 로(실제 out/trx8/runs 에 가짜 책을 남기지 않게)
    arrays = [np.full((30, 20, 3), 200, dtype=np.uint8) for _ in range(70)]
    sheets = P8.sheets30("nbx", arrays)
    assert [(s["first"], s["last"]) for s in sheets] == [(1, 30), (31, 60), (61, 70)]
    assert all(s["path"].startswith(str(tmp_path / "thumb30")) for s in sheets)
    items = [{"text": "A", "level": 2, "printed_page": None}]
    monkeypatch.setattr(X, "load4", lambda k, l, b: {"items": items, "blocks": [blk("p1-b1", 1, "A")]})
    monkeypatch.setattr(P8, "px_tokens30", lambda: (1e-3, 100))
    sent = []

    def fake_send(site, model, instr, msg, schema, tag, usd, **extra):
        sent.append((site, tag, extra["images"]))
        return json.dumps({"items": [{"idx": 0, "page": 1, "basis": "layout"}], "section_starts": [1], "labels_read": [1]}), {"costUsd": 0.01, "inputTokens": 1}
    monkeypatch.setattr(X, "send", fake_send)
    monkeypatch.setattr(X, "reserve", lambda s, u: False)
    r = P8.k5sol30("nbx", arrays=arrays)
    assert r["status"] == "over_cap" and not sent                # reserve 넘으면 보내지 않음
    monkeypatch.setattr(X, "reserve", lambda s, u: True)
    monkeypatch.setattr(X, "prep4", lambda *a, **k: {"items": items, "blocks": [blk("p1-b1", 1, "A")], "win": {"dp": [{"block": "p1-b1", "page": 1}], "windows": [[1]], "mids": [None]},
                                                      "cand": {"rows": {"p1-b1": {"page": 1, "size": 20.0}}, "pages": {}}, "key": "nbx", "lang": "ko", "toc_pages": []})
    monkeypatch.setattr(X, "grade", lambda P, pred: {"start_ab": 100, "end_ab": 100, "block": 100, "divider_start_ab": None, "wrong_start": [], "rows": [], "match": []})
    r = P8.k5sol30("nbx", arrays=arrays)
    assert r["status"] == "ok" and sent == [("sol", "TRX8/k5sol30/nbx-ko-N", 3)]
    assert (tmp_path / "k5" / "nbx-ko-N-sol30.json").exists() and r["images"] == 3


def test_report_names_free(tmp_path, monkeypatch):
    from trlab import tx8_report as RP
    monkeypatch.setattr(P8, "OUT8", tmp_path)
    secret = "비밀회사이름항목"
    summary = {"books": {"nb3-ko": {"n_gold": 2, "cols": {"c3": {"start_ab": 50.0, "end_ab": 50.0, "block": 50.0, "divider_start_ab": None, "wrong_start": [1]}}}},
               "wrong_kinds": {"nb3-ko-N": {"best4b": [{"n": 1, "kind": "닻 틀림", "text": secret}]}},
               "ledger": {"G1": {"usd": 0.0, "n": 0}, "luna": {"usd": 0.0, "n": 0}, "sol": {"usd": 0.0, "n": 0}, "total": {"usd": 0.0, "n": 0}}}
    md = RP.md_of(summary)
    assert secret not in md
    assert "n 1" in md
