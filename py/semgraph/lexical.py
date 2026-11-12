"""BM25. 낱말 = 영숫자 낱말(소문자) + 한글 두 글자 조각(measure-retrieval lexical-v2 꼴)."""

import math
import re
from collections import Counter

WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]*|\d[\d,\.]*")
HANGUL = re.compile(r"[가-힣]+")


def tokens(text):
    out = [w.lower().replace(",", "") for w in WORD.findall(text)]
    for run in HANGUL.findall(text):
        if len(run) == 1:
            out.append(run)
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


class BM25:
    def __init__(self, docs, k1=1.2, b=0.75):
        self.tf = [Counter(tokens(d)) for d in docs]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / len(self.len)
        df = Counter(w for t in self.tf for w in t)
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query):
        q = Counter(tokens(query))
        out = []
        for tf, ln in zip(self.tf, self.len):
            s = 0.0
            for w in q:
                f = tf.get(w)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
            out.append(s)
        return out
