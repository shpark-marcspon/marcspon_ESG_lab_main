"""TRX9 ② — 영역 응답(`run.SCHEMA` 꼴 `items`·`tables[grid]`)의 수치 검사. 정답을 보지 않는다. 실호출 없음. PIL 0.

규칙(얼린 뒤 고치지 않음 — `out/trx9/first-run.txt` 의 sha16. 규칙은 RAS1 보고의 「1000배 오독」 꼴을 읽은 「반쯤 본」 상태로 적었다):
  (R1·R2·R3 의 열 셈은 머리 행(0행)과 연도 꼴 칸(쉼표·소수점 없는 네 자리 1900~2100)을 숫자 칸에서 뺀다 — 연도 머리가 섞여 헛걸리지 않게.)
  R1 소수점 섞임 — 한 표의 한 열에서 숫자 칸이 3 이상이고, 소수점 있는 칸이 **2 이상**·없는 칸이 1 이상이며,
     소수점 칸의 소수 자리수가 모두 같을 때 그 열(정수 열에 `0.5` 하나는 소수점 칸이 1 이라 안 걸림).
  R2 자리수 뜀 — 같은 열의 이웃 숫자 칸 둘(빈 칸은 건너뜀)이 둘 다 소수점 없고 정수부 자릿수 차 ≥ 3.
  R3 합계 불일치 — 행 머리(첫 칸) 또는 열 머리(첫 행 칸)가 합계 꼴(`합계|소계|총계`, 칸 전체가 `계`, `총`으로 시작, `total`/`sum`
     대소문자 무시)인 줄의 숫자 칸이, 같은 방향의 나머지 숫자 칸(다른 합계 줄은 뺌, 숫자 칸 ≥ 2) 합과 0.5% 넘게(합이 0 이면 0.5 넘게) 다름.
  R4 두 번째 읽기 불일치 — 같은 영역의 응답 둘(기본 지시문 · 소수점 지시문)에서 표 수가 다르거나 어느 표의 행·열 수가 다르거나(꼴 다름),
     꼴이 같은데 숫자 칸(어느 쪽이든 숫자로 읽히는 칸)의 글이 하나라도 다름. 둘 다 표가 없으면 R4 는 보지 않는다.
  R5 단위 어림 — 응답 글(items + 격자 칸)에 `tCO₂|tCO2|톤|ton` 이 있고 어느 숫자 칸이 ≥ 1,000,000 (표가 없으면 items 의 숫자).
     도메인 선험(한 조직의 배출량 표에 백만 톤이 넘는 칸은 드물다)이라 보고에도 그렇게 적는다.
  no_table — 응답의 `tables` 가 빔(응답 둘이면 둘 다 빔). 되묻기 후보로 세지 않고 갈래로만 적는다(발주서 §6 물음 3).
결과: {"status": "pass" | "flag" | "no_table", "flags": ["R1:t0:c2", …], "sol": bool(걸림 → 되묻기 후보)}.
숫자 칸 = 빈칸 뺀 글이 `[-+−△▲]?\\(?\\d[\\d,]*(\\.\\d+)?\\)?%?` 와 온전히 맞는 칸. 값은 쉼표를 떼고 읽는다(`286,284` → 286284 — 바로 이 애매함이 R1·R4·R5 의 과녁).
"""

import re

NUM_CELL = re.compile(r"[-+−△▲]?\(?(\d[\d,]*)(?:\.(\d+))?\)?%?")
TOTAL_HEAD = re.compile(r"합계|소계|총계|^계$|^총|total|sum", re.IGNORECASE)
UNIT = re.compile(r"tCO₂|tCO2|톤|ton", re.IGNORECASE)
R3_TOL = 0.005
R5_MIN = 1_000_000
R2_JUMP = 3
R1_MIN_CELLS = 3
# ②b 의 둘째 luna 지시문(기본 `run.INSTR` 뒤에 한 줄 더함). 정답을 보기 전에 얼림(발주서 §6 물음 2).
DECIMAL_HINT = "\n- 숫자의 소수점(.)과 쉼표(,)는 보이는 그대로 구분해 적는다. 소수점 아래 세 자리(`286.284` 꼴)가 흔하다."


def num(cell):
    """숫자 칸이면 (값, 소수 자리수 | None, 정수부 자릿수), 아니면 None."""
    s = re.sub(r"\s+", "", cell or "")
    m = NUM_CELL.fullmatch(s)
    if not m:
        return None
    ip, fp = m.group(1).replace(",", ""), m.group(2)
    if not ip.isdigit():
        return None
    v = float(ip + ("." + fp if fp else ""))
    return v, (len(fp) if fp is not None else None), len(ip.lstrip("0") or "0")


def columns(grid):
    w = max((len(r) for r in grid), default=0)
    return [[(r[j] if j < len(r) else "") for r in grid] for j in range(w)]


def is_year(cell):
    s = re.sub(r"\s+", "", cell or "")
    return bool(re.fullmatch(r"(19|20)\d\d", s))


def data_nums(col):
    """열의 (행 번호, 숫자) — 머리 행과 연도 꼴 칸은 뺀다."""
    return [(i, n) for i, c in enumerate(col) if i > 0 and not is_year(c) and (n := num(c))]


def r1(grid):
    out = []
    for j, col in enumerate(columns(grid)):
        ns = [n for _, n in data_nums(col)]
        if len(ns) < R1_MIN_CELLS:
            continue
        dec = [n[1] for n in ns if n[1] is not None]
        if len(dec) >= 2 and len(dec) < len(ns) and len(set(dec)) == 1:
            out.append(f"c{j}")
    return out


def r2(grid):
    out = []
    for j, col in enumerate(columns(grid)):
        ns = [n for _, n in data_nums(col)]
        for a, b in zip(ns, ns[1:]):
            if a[1] is None and b[1] is None and abs(a[2] - b[2]) >= R2_JUMP:
                out.append(f"c{j}")
                break
    return out


def _is_total(cell):
    return bool(TOTAL_HEAD.search(re.sub(r"\s+", "", cell or "")))


def _mismatch(total, parts):
    if len(parts) < 2:
        return False
    s = sum(parts)
    return abs(total - s) > (abs(total) * R3_TOL if total else 0.5)


def r3(grid):
    out = []
    if not grid:
        return out
    total_rows = [i for i, r in enumerate(grid) if r and _is_total(r[0])]
    cols = columns(grid)
    for i in total_rows:
        for j, col in enumerate(cols):
            if j == 0:
                continue
            t = num(col[i])
            if not t:
                continue
            parts = [n for k, n in data_nums(col) if k != i and k not in total_rows]
            if _mismatch(t[0], [p[0] for p in parts]):
                out.append(f"r{i}:c{j}")
    total_cols = [j for j, c in enumerate(grid[0]) if _is_total(c)]
    for j in total_cols:
        for i, row in enumerate(grid):
            if i == 0 or j >= len(row):
                continue
            t = num(row[j])
            if not t:
                continue
            parts = [num(row[k]) for k in range(len(row)) if k != j and k != 0 and k not in total_cols and not is_year(row[k])]
            if _mismatch(t[0], [p[0] for p in parts if p]):
                out.append(f"c{j}:r{i}")
    return out


def _grids(resp):
    return [t.get("grid") or [] for t in (resp or {}).get("tables") or []]


def r4(resp_a, resp_b):
    """꼴 다름 → ["shape"], 꼴 같고 숫자 칸 글 다름 → ["t<i>:r<r>:c<c>", …]."""
    ga, gb = _grids(resp_a), _grids(resp_b)
    if not ga and not gb:
        return []
    if len(ga) != len(gb):
        return ["shape"]
    out = []
    for ti, (a, b) in enumerate(zip(ga, gb)):
        if len(a) != len(b) or any(len(ra) != len(rb) for ra, rb in zip(a, b)):
            return ["shape"]
        for ri, (ra, rb) in enumerate(zip(a, b)):
            for ci, (x, y) in enumerate(zip(ra, rb)):
                if (num(x) or num(y)) and re.sub(r"\s+", "", x or "") != re.sub(r"\s+", "", y or ""):
                    out.append(f"t{ti}:r{ri}:c{ci}")
    return out


def _texts(resp):
    items = list((resp or {}).get("items") or [])
    cells = [c for g in _grids(resp) for row in g for c in row]
    return items, cells


def r5(resp):
    items, cells = _texts(resp)
    if not UNIT.search(" ".join(items + cells)):
        return []
    pool = cells if cells else [w for it in items for w in it.split()]
    return ["unit"] if any((n := num(c)) and n[0] >= R5_MIN for c in pool) else []


def check(resp, resp2=None):
    """응답 하나(또는 둘)를 검사해 {"status", "flags", "sol"}. resp2 가 있으면 R4 도 본다(R1~R3·R5 는 resp 에)."""
    flags = []
    grids = _grids(resp)
    for ti, g in enumerate(grids):
        flags += [f"R1:t{ti}:{x}" for x in r1(g)]
        flags += [f"R2:t{ti}:{x}" for x in r2(g)]
        flags += [f"R3:t{ti}:{x}" for x in r3(g)]
    if resp2 is not None:
        flags += [f"R4:{x}" for x in r4(resp, resp2)]
    flags += [f"R5:{x}" for x in r5(resp)]
    no_table = not grids and (resp2 is None or not _grids(resp2))
    if no_table and not flags:
        return {"status": "no_table", "flags": [], "sol": False}
    if not flags:
        return {"status": "pass", "flags": [], "sol": False}
    return {"status": "flag", "flags": flags, "sol": True}


def rules_hit(flags):
    return sorted({f.split(":")[0] for f in flags})
