"""OpenAI 부르기 — 임베딩과 응답. 키는 환경 변수 OPENAI_API_KEY 에서만 읽고 찍지 않는다.
호출마다 <out>/calls.jsonl 한 줄(모델·토큰·USD·초). dry_run 이면 보내지 않고 토큰을 어림해 같은 줄을 남긴다.
"""

import json
import time
from pathlib import Path

# USD / MTok — esg-app core/llm/data/models.json (checkedOn 2026-10-01, https://developers.openai.com/api/docs/pricing)
PRICES = {  # (입력, 캐시 입력, 출력)
    "gpt-6-luna": (0.1, 0.01, 0.5),
    "gpt-6.1-sol": (2.0, 0.1, 10.0),
    "gpt-4.1-mini": (0.4, 0.1, 1.6),  # esg-app core/llm/data/models.json:235-257 (checkedOn 2026-10-16) — TRX7
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}
NO_REASONING = {"gpt-4.1-mini"}  # 비추론 모델 — respond 가 reasoning 을 싣지 않는다(TRX7). 다른 모델은 그대로


def _log(out, row):
    with open(Path(out) / "calls.jsonl", "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def cost(model, inp, cached, outp):
    pi, pc, po = PRICES[model]
    return ((inp - cached) * pi + cached * pc + outp * po) / 1e6


def embed(texts, out, tag, dry_run=False, batch=128):
    model = "text-embedding-3-small"
    if dry_run:
        est = sum(len(t) for t in texts)  # 한글 1자 ≈ 1토큰 미만 — 위쪽 어림
        _log(out, {"tag": tag, "model": model, "dry_run": True, "inputTokens": est, "costUsd": cost(model, est, 0, 0)})
        return None
    from openai import OpenAI
    client = OpenAI()
    vecs = []
    for i in range(0, len(texts), batch):
        t0 = time.time()
        r = client.embeddings.create(model=model, input=[t[:6000] or " " for t in texts[i:i + batch]])
        vecs.extend(d.embedding for d in r.data)
        n = r.usage.prompt_tokens
        _log(out, {"tag": tag, "model": model, "key": "marcspon", "inputTokens": n, "costUsd": cost(model, n, 0, 0),
                   "seconds": round(time.time() - t0, 2)})
    return vecs


def respond(model, instructions, text, schema, out, tag, effort="low", max_output=16000, timeout=300.0):
    from openai import OpenAI
    client = OpenAI(timeout=timeout, max_retries=1)  # 한 호출이 4.8시간 걸린 적이 있다(SG1c canon) — 상한 + 한 번 다시
    if model in NO_REASONING: client.responses.create = (lambda f: lambda reasoning=None, **kw: f(**kw))(client.responses.create)  # noqa: E701 — 더한 줄 3 안(TRX7 §6 1)
    t0 = time.time()
    r = client.responses.create(
        model=model, instructions=instructions, input=text, max_output_tokens=max_output,
        reasoning={"effort": effort},
        text={"format": {"type": "json_schema", "name": "edges", "schema": schema, "strict": True}},
    )
    u = r.usage
    cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    row = {"tag": tag, "model": model, "key": "marcspon", "inputTokens": u.input_tokens, "cachedTokens": cached,
           "outputTokens": u.output_tokens, "costUsd": cost(model, u.input_tokens, cached, u.output_tokens),
           "seconds": round(time.time() - t0, 2), "status": r.status}
    _log(out, row)
    return r.output_text, row
