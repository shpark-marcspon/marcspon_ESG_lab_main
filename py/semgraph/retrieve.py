"""견주는 갈래: ⓐ 벡터 · ⓑ BM25 · ⓒ RRF(ⓐ,ⓑ) · ⓓ0 ⓒ+구조 따라가기 · ⓓ ⓒ+구조+의미 따라가기.

따라가기(값은 결과를 보기 전에 정해 둔다 — 2026-10-29 설계):
  씨앗 = ⓒ 상위 SEEDS 개, 씨앗 무게 = 1/순위.
  이웃 점수 = 씨앗 무게 × 관계 무게 × 1/√(가운데 마디 차수), 두 걸음은 곱해서 한 번 더.
  최종 = ⓒ 점수(최대로 나눔) + LAMBDA × 넓힘 점수(최대로 나눔). 같은 k 로 자른다.
"""

import math
from collections import defaultdict

SEEDS = 5
LAMBDA = 0.5
RRF_K = 60

STRUCT_W = {"next_block": 0.6, "title_path": 0.5, "section_contains": 0.3, "on_page": 0.3}
SEM_W = {"mentions_cell": 0.7, "page_ref": 0.7, "same_metric": 0.6, "scopes": 0.6,
         "supports": 0.8, "target_of": 0.8, "assures": 0.8, "explains": 0.8, "scopes_ai": 0.8, "same_metric_ai": 0.7}


def ranks(scores):
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    return order


def rrf(*orders):
    s = defaultdict(float)
    for order in orders:
        for r, i in enumerate(order):
            s[i] += 1 / (RRF_K + r + 1)
    return s


class Graph:
    def __init__(self, edges, weights):
        self.adj = defaultdict(list)
        for a, b, rel in edges:
            w = weights.get(rel)
            if w:
                self.adj[a].append((b, w, rel))
                self.adj[b].append((a, w, rel))

    def deg(self, n):
        return max(1, len(self.adj[n]))

    def spread(self, seeds, hops=2):
        """seeds: {마디: 무게}. 단위 마디(가운데 마디 sec:·page: 가 아닌 것)에 쌓인 점수를 돌려준다."""
        acc = defaultdict(float)
        frontier = dict(seeds)
        for _ in range(hops):
            nxt = defaultdict(float)
            for n, s in frontier.items():
                for m, w, _rel in self.adj[n]:
                    v = s * w / math.sqrt(self.deg(n) if ":" in n else 1) / math.sqrt(self.deg(m) if ":" in m else 1)
                    nxt[m] = max(nxt[m], v)
            for m, v in nxt.items():
                if ":" not in m and m not in seeds:
                    acc[m] = max(acc[m], v)
            frontier = nxt
        return acc


def expand(c_scores, ids, graph, hops=2, lam=LAMBDA):
    top = sorted(c_scores, key=lambda i: -c_scores[i])
    seeds = {ids[i]: 1 / (r + 1) for r, i in enumerate(top[:SEEDS])}
    acc = graph.spread(seeds, hops)
    cmax = max(c_scores.values()) or 1
    amax = max(acc.values(), default=0) or 1
    idx = {u: i for i, u in enumerate(ids)}
    final = defaultdict(float)
    for i, s in c_scores.items():
        final[i] += s / cmax
    for u, s in acc.items():
        final[idx[u]] += lam * s / amax
    return sorted(final, key=lambda i: -final[i])
