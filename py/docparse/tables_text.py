"""글자 정렬 기반 표 추출. 괘선을 전혀 쓰지 않고 글자 조각의 위치만으로 행·열을 추정한다.

절차
 1. 조각을 y 중심으로 묶어 글자 줄을 만든다.
 2. 좌우로 나란한 표를 가른다: 한 줄에서 「글자 … 숫자 … 글자 … 숫자」 순서가 되풀이되면
    두 번째 글자 열이 시작하는 x 가 옆 표의 왼쪽 끝이다.
 3. 값(숫자·연도·'-')이 있는 줄이 이어지는 구간을 표로 본다. 사이에 낀 소제목 줄(조각 하나)은 포함한다.
 4. 열 경계: 표의 줄들을 x 축에 투영해 어느 조각도 지나가지 않는 틈을 찾는다(열 경계 추정).
    한 열 안에 같은 줄의 조각이 둘씩 들어가는 일이 잦으면 그 사이에 경계를 더 넣는다(단위 열 처리).
 5. 줄 간격이 좁고 값이 없는 줄은 윗줄의 줄바꿈으로 보고 합친다(여러 줄 머리글·셀 병합).

한계: 열 경계가 추정이므로 결과는 괘선 기반보다 못하다. 병합 셀의 범위는 알 수 없다.
"""
from statistics import median

from .table_model import METHOD_TEXT, STATUS_LOW, Table, estimate_header, prune_empty, pruned_index

MIN_COL_GAP = 2.5


def cluster_rows(chunks):
    """조각을 글자 줄로 묶는다. 각 줄은 x 순으로 정렬된 조각 목록."""
    if not chunks:
        return []
    tol = 0.45 * median(c.y1 - c.y0 for c in chunks)
    rows = []
    for c in sorted(chunks, key=lambda c: c.yc):
        if rows and c.yc - rows[-1]["yc"] <= tol:
            r = rows[-1]
            r["cs"].append(c)
            r["yc"] = sum(x.yc for x in r["cs"]) / len(r["cs"])
        else:
            rows.append({"yc": c.yc, "cs": [c]})
    return [sorted(r["cs"], key=lambda c: c.x0) for r in rows]


def _is_candidate(row):
    return len(row) >= 2 and any(c.value_like for c in row)


def side_by_side_cuts(rows):
    """좌우로 나란한 표 사이의 x 절단 위치 목록.

    한 줄에서 값(숫자) 뒤에 글자 조각이 다시 나오면 그 조각의 왼쪽 끝이 옆 표의 시작 후보다.
    세 줄 이상이 같은 자리를 가리키고, 그 오른쪽에 값이 있는 줄이 두 줄 이상 있어야 자른다
    (값 뒤에 붙는 비고 열은 오른쪽에 값이 없으므로 잘리지 않는다).
    """
    starts = []
    for row in rows:
        seen_value = False
        for k, c in enumerate(row):
            if c.value_like:
                seen_value = k > 0
            elif seen_value:
                starts.append((c.x0, row[k - 1].x1))
                break
    starts.sort()
    cuts, group = [], []
    for s in starts + [None]:
        if s is not None and (not group or s[0] - group[0][0] <= 30):
            group.append(s)
            continue
        if len(group) >= 3:
            left_edge = min(g[0] for g in group)
            before = [g[1] for g in group if g[1] < left_edge]
            cut = ((max(before) if before else left_edge - 2) + left_edge) / 2
            right_value_rows = sum(1 for r in rows if any(c.value_like and c.x0 > cut for c in r)
                                   and any(not c.value_like and c.x0 > cut for c in r))
            if right_value_rows >= 2:
                cuts.append(cut)
        group = [s] if s is not None else []
    return cuts


def _runs(rows):
    """값 있는 줄이 이어지는 구간의 (시작, 끝) 줄 번호 목록."""
    cand = [_is_candidate(r) for r in rows]
    ycs = [sum(c.yc for c in r) / len(r) for r in rows]
    gaps = [b - a for a, b, ca, cb in zip(ycs, ycs[1:], cand, cand[1:]) if ca and cb]
    if not gaps:
        return []
    pitch = median(gaps)
    runs, start, last = [], None, None
    for i, is_c in enumerate(cand):
        if start is None:
            if is_c:
                start = last = i
            continue
        if ycs[i] - ycs[i - 1] > 2.2 * pitch:
            runs.append((start, last))
            start = last = i if is_c else None
            continue
        if is_c:
            last = i
        elif i - last >= 2:  # 값 없는 줄이 두 줄 이어지면 표가 끝난 것 (각주, 본문)
            runs.append((start, last))
            start = last = None
    if start is not None:
        runs.append((start, last))
    return [(a, b) for a, b in runs if sum(cand[a:b + 1]) >= 2]


def column_bounds(rows):
    """줄들을 x 축에 투영해 열 경계 x 목록을 추정한다."""
    multi = [r for r in rows if len(r) >= 2]
    spans = sorted((c.x0, c.x1) for r in multi for c in r)
    if not spans:
        return []
    bounds, reach = [], spans[0][1]
    for x0, x1 in spans[1:]:
        if x0 - reach >= MIN_COL_GAP:
            bounds.append((reach + x0) / 2)
        reach = max(reach, x1)

    def col_of(c):
        x = c.x1 if c.value_like else c.x0
        return sum(1 for b in bounds if x > b)

    # 단위 열 처리: 긴 행 이름이 단위 열 위를 지나가 틈이 메워진 경우, 같은 줄의 두 조각이 한 열에
    # 들어가는 줄이 여럿이면 뒤 조각들의 왼쪽 끝 앞에 경계를 넣는다.
    for _ in range(4):
        seconds = {}
        for r in multi:
            seen = {}
            for c in r:
                k = col_of(c)
                if k in seen:
                    seconds.setdefault(k, []).append((seen[k].x1, c.x0))
                seen[k] = c
        added = False
        for k, pairs in seconds.items():
            if len(pairs) >= max(2, 0.3 * len(multi)):
                left = min(p[1] for p in pairs)
                bounds.append(left - 1.0)
                added = True
        bounds.sort()
        if not added:
            break
    return bounds


def _build(rows, page_notes):
    bounds = column_bounds(rows)
    if not bounds:
        return None
    ncol = len(bounds) + 1
    notes = list(page_notes)
    ycs = [sum(c.yc for c in r) / len(r) for r in rows]
    pitch = median(b - a for a, b in zip(ycs, ycs[1:])) if len(rows) > 1 else 0
    grid, marks, collisions = [], [], 0
    members = []  # grid 와 같은 꼴: 행마다 열마다 조각 목록
    for i, r in enumerate(rows):
        cells = [[] for _ in range(ncol)]
        for c in r:
            # 줄에 조각이 하나뿐이면(소제목) 왼쪽 끝 기준, 값은 오른쪽 끝 기준, 글자는 왼쪽 끝 기준
            x = c.x0 if (len(r) == 1 or not c.value_like) else c.x1
            cells[sum(1 for b in bounds if x > b)].append(c)
        collisions += sum(1 for cs in cells if len(cs) > 1)
        line = [" ".join(c.text for c in cs) for cs in cells]
        wrapped = (grid and pitch and ycs[i] - ycs[i - 1] < 0.75 * pitch and not any(c.value_like for c in r))
        if wrapped:
            grid[-1] = [(a + " " + b).strip() for a, b in zip(grid[-1], line)]
            members[-1] = [a + b for a, b in zip(members[-1], cells)]
            notes.append("wrapped_line_merged") if "wrapped_line_merged" not in notes else None
        else:
            grid.append(line)
            members.append([list(cs) for cs in cells])
        row_no = len(grid) - 1
        for j, cs in enumerate(cells):
            marks.extend({"row": row_no, "col": j, "mark": m} for c in cs for m in c.marks)
    rmap, cmap = pruned_index(grid)
    grid, marks = prune_empty(grid, marks)
    if len(grid) < 2 or len(grid[0]) < 2:
        return None
    # 괘선 근거 없이 글자 위치만으로 세운 격자는 표가 아닌 것(목차, 도표 숫자)도 잡으므로 항상 low_confidence 다.
    status = STATUS_LOW
    if collisions:
        notes.append(f"cells_with_two_chunks:{collisions}")
    cs = [c for r in rows for c in r]
    bbox = [min(c.x0 for c in cs), min(c.y0 for c in cs), max(c.x1 for c in cs), max(c.y1 for c in cs)]
    return Table(bbox=[round(v, 1) for v in bbox], grid=grid, method=METHOD_TEXT, status=status, notes=notes,
                 footnote_marks=marks, header=estimate_header(grid),
                 cell_chunks={(rmap[i], cmap[j]): cs for i, row in enumerate(members) for j, cs in enumerate(row)
                              if cs and i in rmap and j in cmap})


def find_text_tables(chunks):
    """글자 위치만으로 표를 찾는다. (표 목록, 표처럼 보였지만 격자를 세우지 못한 영역의 bbox 목록)"""
    tables, failed = [], []
    if not chunks:
        return tables, failed
    cuts = side_by_side_cuts(cluster_rows(chunks))
    edges = [float("-inf")] + sorted(cuts) + [float("inf")]
    for lo, hi in zip(edges, edges[1:]):
        strip = [c for c in chunks if lo <= c.x0 < hi]
        rows = cluster_rows(strip)
        for a, b in _runs(rows):
            t = _build(rows[a:b + 1], ["side_by_side_split"] if cuts else [])
            if t is not None:
                tables.append(t)
            else:
                cs = [c for r in rows[a:b + 1] for c in r]
                failed.append([min(c.x0 for c in cs), min(c.y0 for c in cs), max(c.x1 for c in cs), max(c.y1 for c in cs)])
    return tables, failed
