"""다단 편집 쪽의 읽는 순서 복원 (재귀 XY 절단).

상자들을 x 축과 y 축에 투영해 빈 틈을 찾는다. 단 사이 틈(x)이 y 틈의 절반 이상이면 x 로 먼저 가른다
(단이 더 강한 구조다). 그렇지 않으면 y 로 가른다.
x 로 가르면 단(왼쪽→오른쪽), y 로 가르면 위→아래다. 전폭 제목 아래에 두 단이 있는 쪽은
먼저 y 로 갈린 뒤 아래쪽이 x 로 갈린다. y 축은 가장 넓은 틈의 절반 이상인 틈에서만 자른다
(좁은 틈까지 다 자르면 좌우 단의 문단이 줄 단위로 섞인다).
쪽 맨 아래 띠(쪽 번호, 내비게이션)는 본문과 섞이지 않게 맨 뒤로 보낸다.
"""
MIN_GAP_X = 8.0
MIN_GAP_Y = 3.0
FOOTER_ZONE = 0.92   # 쪽 높이의 이 비율 아래에서 시작하는 상자는 꼬리말로 본다
COLUMN_MIN_HEIGHT = 0.4  # 단으로 세려면 가른 영역이 쪽 높이의 이 비율 이상이어야 한다


def _gaps(indices, boxes, lo, hi):
    """(정렬된 인덱스, [(틈 크기, 틈 뒤 첫 원소의 정렬 위치)])"""
    order = sorted(indices, key=lambda i: boxes[i][lo])
    gaps, reach = [], boxes[order[0]][hi]
    for k, i in enumerate(order[1:], 1):
        g = boxes[i][lo] - reach
        if g > 0:
            gaps.append((g, k))
        reach = max(reach, boxes[i][hi])
    return order, gaps


def _cut(order, gaps, threshold):
    cuts = sorted(k for g, k in gaps if g >= threshold)
    return [order[a:b] for a, b in zip([0] + cuts, cuts + [len(order)])]


def reading_order(boxes, page_height=None):
    """boxes: [x0, y0, x1, y1] 목록. (읽는 순서대로 늘어놓은 인덱스 목록, 추정 단 수)."""
    state = {"columns": 1 if boxes else 0}

    def walk(indices):
        if len(indices) <= 1:
            return list(indices)
        xo, xg = _gaps(indices, boxes, 0, 2)
        yo, yg = _gaps(indices, boxes, 1, 3)
        best_x = max((g for g, _ in xg), default=0.0)
        best_y = max((g for g, _ in yg), default=0.0)
        if best_x >= MIN_GAP_X and best_x >= 0.5 * best_y:
            groups = _cut(xo, xg, MIN_GAP_X)
            if page_height:
                height = max(boxes[i][3] for i in indices) - min(boxes[i][1] for i in indices)
                if height >= COLUMN_MIN_HEIGHT * page_height:
                    state["columns"] = max(state["columns"], len(groups))
            return [i for g in groups for i in walk(g)]
        if best_y >= MIN_GAP_Y:
            return [i for g in _cut(yo, yg, max(MIN_GAP_Y, 0.5 * best_y)) for i in walk(g)]
        if best_x >= MIN_GAP_X:
            return [i for g in _cut(xo, xg, MIN_GAP_X) for i in walk(g)]
        return sorted(indices, key=lambda i: (boxes[i][1], boxes[i][0]))

    all_idx = list(range(len(boxes)))
    footer = []
    if page_height:
        footer = [i for i in all_idx if boxes[i][1] >= FOOTER_ZONE * page_height]
        if len(footer) == len(all_idx):
            footer = []
    foot = set(footer)
    body = [i for i in all_idx if i not in foot]
    order = walk(body) + sorted(footer, key=lambda i: (round(boxes[i][1] / 6), boxes[i][0]))
    return order, state["columns"]
