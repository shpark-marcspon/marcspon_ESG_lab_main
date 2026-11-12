"""M-I2 URL 검사기 — 오프라인, 항목 URL 하나마다 `check{…}`(발주서 EVA §2 (5)).

① form      : http/https 이고 host 가 있음
② root_only : 경로가 비었거나 `/` 이거나 마지막 조각이 `index`·`main`·`home`(확장자 무관) — 기관 첫 화면, 특정 글 아님
③ whitelist : 규제 갈래만 — host 가 판(`scope.whitelist`)의 목록 host 와 같거나 그 하위(`www.` 접음). 컨트로버시는 None
④ date      : `in | out | date_missing | date_unreadable` — 항목 날짜가 `dateRange` 앞끝보다 앞이면 out
⑤ repeat    : 같은 실행 안 **다른 토픽**에서 같은 URL(정규화: host 소문자·`www.` 접음·끝 `/`·조각 뺌)이 나온 수
⑥ evidence  : 인용 주석이 있으면 `cited | not_cited`, 없으면 `unknown` · `searched`(검색 호출 유무, 모르면 None)
⑦ open      : 늘 `not_checked`(밖 HTTP 0 — 켤지는 사용자 몫, 발주서 §7 3)

화이트리스트는 esg-app `colab_155/whitelists.py` 를 **글로 읽어**(import 안 함) 판마다 URL host 를 뽑는다.
"""
from __future__ import annotations

import datetime as _dt
import re
import statistics
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..common.match import ratio
from ..common.units import Source, Unit, check_units
from .score import host_of

PANELS = {"COLAB17": "cell17", "CELL71": "cell71", "CELL73": "cell73"}
_BLOCK = re.compile(r'^WHITELIST_(\w+): Final = """\\\n(.*?)^"""', re.S | re.M)
_URL = re.compile(r"https?://[^\s)\]>\"'，,]+")
_ROOT_NAMES = {"index", "main", "home", "default"}
_RANGE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def load_whitelists(path: str | Path) -> dict[str, set[str]]:
    """`whitelists.py` 글 → {판 이름: host 집합}."""
    text = Path(path).read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for m in _BLOCK.finditer(text):
        name = PANELS.get(m.group(1))
        if name:
            out[name] = {h for h in (host_of(u) for u in _URL.findall(m.group(2))) if h}
    return out


def in_whitelist(host: str | None, hosts: set[str]) -> bool:
    """host 끝 맞춤 — 같거나 `.<목록 host>` 로 끝남(부분 문자열 아님: `evil-x.go.kr` ≠ `x.go.kr`)."""
    if not host:
        return False
    return any(host == w or host.endswith("." + w) for w in hosts)


def is_root(url: str) -> bool:
    sp = urlsplit(url.strip())
    segs = [s for s in sp.path.split("/") if s]
    if not segs:
        return True
    last = segs[-1].split(".", 1)[0].lower()
    return last in _ROOT_NAMES


def norm_url(url: str) -> str:
    sp = urlsplit(url.strip())
    h = host_of(url) or ""
    path = sp.path.rstrip("/")
    return f"{h}{path}" + (f"?{sp.query}" if sp.query else "")


def range_start(date_range: str | None) -> _dt.date | None:
    m = _RANGE.match(date_range or "")
    return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _date_check(item: dict[str, Any], start: _dt.date | None) -> str:
    st = item.get("date_status")
    if st == "missing":
        return "date_missing"
    if st != "ok":
        return "date_unreadable"
    if start is None:
        return "in"
    return "out" if _dt.date.fromisoformat(item["date"]) < start else "in"


def check_units_urls(units: Iterable[Unit], whitelists: dict[str, set[str]]) -> list[Unit]:
    """단위마다 항목 URL 검사를 `sources[].check` 에 채운 새 단위 목록(같은 차례)."""
    us = check_units(units)
    seen: dict[str, set[str]] = {}
    for u in us:
        for it in u.payload.get("items") or []:
            for url in it.get("urls") or []:
                seen.setdefault(norm_url(url), set()).add(u.group)
    out = []
    for u in us:
        p = u.payload
        branch = p.get("branch")
        scope = p.get("scope") or {}
        wl = whitelists.get(scope.get("whitelist") or "")
        start = range_start(scope.get("dateRange"))
        cites = p.get("citations")
        cset = {norm_url(c) for c in cites} if cites else None
        srcs = []
        for it in p.get("items") or []:
            for url in it.get("urls") or []:
                h = host_of(url)
                nu = norm_url(url)
                srcs.append(Source("web", url, {
                    "form": h is not None,
                    "root_only": is_root(url) if h else None,
                    "whitelist": (in_whitelist(h, wl) if wl is not None else None) if branch == "regulation" else None,
                    "whitelist_panel": scope.get("whitelist") if branch == "regulation" else None,
                    "date": _date_check(it, start),
                    "repeat": len(seen.get(nu, set()) - {u.group}),
                    "evidence": ("cited" if nu in cset else "not_cited") if cset is not None else "unknown",
                    "searched": p.get("searched"),
                    "open": "not_checked",
                }))
        out.append(Unit(module=u.module, unit=u.unit, key=u.key, group=u.group, payload=u.payload,
                        sources=tuple(srcs), status=u.status, origin=u.origin))
    return out


def table_row(units: Iterable[Unit], branch: str) -> dict[str, Any]:
    """한 자리 × 갈래의 M-I2 칸(분모 = URL 수)."""
    bu = [u for u in units if u.payload.get("branch") == branch]
    cs = [s.check for u in bu for s in u.sources if s.check is not None]
    n = len(cs)
    per_topic = [len(u.sources) for u in bu]
    known = [u for u in bu if u.payload.get("searched") is not None]
    cited_known = [c for c in cs if c["evidence"] != "unknown"]
    wl = [c for c in cs if c["whitelist"] is not None]
    return {
        "URL": n,
        "① 꼴 rate": ratio(sum(c["form"] for c in cs), n),
        "② 특정 글 rate": ratio(sum(c["root_only"] is False for c in cs), n),
        "루트만": sum(bool(c["root_only"]) for c in cs),
        "③ 화이트리스트 안 rate": ratio(sum(bool(c["whitelist"]) for c in wl), len(wl)) if branch == "regulation" else None,
        "④ 기간 안 rate": ratio(sum(c["date"] == "in" for c in cs), n),
        "기간 밖": sum(c["date"] == "out" for c in cs),
        "날짜 없음·못 읽음": sum(c["date"] in ("date_missing", "date_unreadable") for c in cs),
        "⑤ 되풀이 없음 rate": ratio(sum(c["repeat"] == 0 for c in cs), n),
        "⑥ 인용 안 rate": ratio(sum(c["evidence"] == "cited" for c in cited_known), len(cited_known)),
        "토픽당 URL 중앙": statistics.median(per_topic) if per_topic else None,
        "검색 호출 있던 칸 rate": ratio(sum(bool(u.payload["searched"]) for u in known), len(known)),
        "⑦ 열림": "not_checked" if n else None,
    }


def headline(units: Iterable[Unit]) -> dict[str, Any]:
    """규제 URL 루트만 · 화이트리스트 밖 · 기간 밖 /n."""
    cs = [s.check for u in units if u.payload.get("branch") == "regulation" for s in u.sources if s.check]
    n = len(cs)
    return {"n": n, "root_only": ratio(sum(bool(c["root_only"]) for c in cs), n),
            "outside_whitelist": ratio(sum(c["whitelist"] is False for c in cs), n),
            "out_of_range": ratio(sum(c["date"] == "out" for c in cs), n)}


ORDER_TOP = ("IR0", "IRL2", "IRD3")


def run_mi2(out_dir: Path) -> dict[str, Any]:
    """자리 여섯 × 갈래 둘 → `out_dir/mi2/summary.{json,md}`. 맨 위 줄 = 규제 URL 루트만·화이트리스트 밖·기간 밖(IR0 → IRL2 → IRD3)."""
    from ..common import report as R
    from .cli import ROOT, SITES, units_for

    wl = load_whitelists(ROOT / "esg-app" / "apps" / "api-py" / "esgapi" / "materiality" / "engines" / "colab_155" / "whitelists.py")
    rows, heads = [], {}
    for s in SITES:
        us = check_units_urls(units_for(s), wl)
        heads[s] = headline(us)
        for b in ("regulation", "controversy"):
            rows.append({"gold": R.NO_GOLD, "자리": s, "갈래": b, **table_row(us, b)})
    top = []
    for s in ORDER_TOP:
        h = heads[s]
        top.append(f"{s} 규제 URL {h['n']} — 루트만 {h['root_only']['num']}/{h['n']} · 화이트리스트 밖 "
                   f"{h['outside_whitelist']['num']}/{h['n']} · 기간 밖 {h['out_of_range']['num']}/{h['n']}")
    notes = top + [
        "② = 특정 글(루트·첫 화면 아님) · ③ 규제 갈래만(판 = scope.whitelist, IR0 = cell17, IRL·IRL2·IRD = cell73) · "
        "④ 기간 안(날짜 없음·못 읽음은 따로 셈) · ⑤ 다른 토픽에 같은 URL 없음 · ⑥ 인용 주석이 있는 응답만(지금 0 → 분모 없음).",
        "⑦ 열림 검사는 끔(not_checked) — 밖 HTTP 0. 켤지는 사용자 몫.",
        "IR0·IRD 의 기간은 결과에 칸이 없어 처음 값 2024-01-01~오늘까지 로 둔다(추정).",
    ]
    R.write(out_dir / "mi2", "summary", rows, R.NO_GOLD, title="M-I2 IRO URL 검사(오프라인)", notes=notes,
            extra={"headline": heads})
    return {"headline": heads, "top": top}
