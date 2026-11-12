"""시험용 지은 읽기판(synthetic) — 받은 코랩의 셀 번호와 꼴(셀 7 이 import 때 키를 묻고 파일을 다시 읽음, 셀 10·14·18 이
Responses 자리로 보냄, 셀 14 가 셀 10 의 답을 literal_eval, 셀 7 의 고리가 function_call_output 을 보냄)만 흉내 낸다. 몸은 코랩 코드가 아니라 지은 것이다(이름만 같다)."""

FAKE_MD = '''\
# 가상 노트북 — 읽기판 꼴(synthetic)

## 1. 셀 전문

### 셀 2 · code

```python
data = "지은 자리 — 돌리지 않는다"
```

### 셀 3 · code

```python
data_split = {k: v for k, v in data.items() if k == "환경"}
print(len(data_split))
```

### 셀 4 · code

```python
DATA_PATH = fname + ".json"
with open(DATA_PATH, "w", encoding="utf-8") as fh:
    json.dump(data_split, fh)
```

### 셀 7 · code

```python
import requests as rq
from google.colab import userdata as ud
KEY = ud.get("가상키")
with open(DATA_PATH, encoding="utf-8") as fh:
    data = json.load(fh)
_value_cache = dict()

def tool_sum_check(*args):
    return {"kind": "가상합", "a": args[0]}

def tool_sum_n_check(*args):
    return {"kind": "가상합n"}

def tool_ratio_check(*args):
    return {"kind": "가상비"}

def tool_value_of_by_name(d, 대, 중, 소, 단, y):
    k = (대, 중, 소, 단, str(y))
    if k not in _value_cache:
        hit = [r for r in d.get("환경", []) if (r["대분류"], r["중분류"], r["소분류"], r["단위"]) == k[:4]]
        raw = hit[0]["values"].get(str(y)) if hit else None
        _value_cache[k] = {"value": float(raw) if isinstance(raw, (int, float)) else "N/A"}
    return _value_cache.get(k)

def tool_sum_check_by_name(d, *args):
    return {"results": list(args[3])}

def tool_sum_n_check_by_names(d, *args):
    return {"results": []}

def tool_ratio_check_by_name(d, *args):
    return {"results": []}

def tool_search_items(d, **kw):
    return {"total": 0, "limit": kw.get("limit", 50)}

def _run_local_tool(name, args, **kw):
    return {"name": name, "nan": float("nan")}

TOOLS_RESPONSES = [{"type": "function", "name": "가상도구"}]
TOOLS_RESPONSES_websearch = [{"type": "web_search"}]
URL = "https://api.openai.com" + "/v1/responses"

def run_responses_loop(system, user, *, api_key, model, tools, max_steps, data, tolerance):
    body = {"model": model, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    out = rq.post(URL, json=body).json()
    for _ in range(max(1, max_steps) - 1):
        found = [o for o in out["output"] if o["type"] == "function_call"]
        if not found:
            break
        sent = [{"type": "function_call_output", "call_id": o["call_id"],
                 "output": json.dumps(_run_local_tool(o["name"], o["arguments"]))} for o in found]
        out = rq.post(URL, json={"input": sent}).json()
    texts = [c["text"] for o in out["output"] if o["type"] == "message" for c in o["content"]]
    return "\\n".join(texts).strip(), out["id"]

def ask(system, user, tools):
    try:
        r = rq.post(URL, json={"model": MODEL_RESP, "tools": tools,
            "input": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    except Exception as err:
        return "삼킴"
    return r.json()["output"][0]["content"][0]["text"]
```

### 셀 10 · code

```python
MODEL_RESP = "가상-모델"
TOLERANCE = 1.1
SET_MAX_STEPS = 12
SYSTEM = "가상 시스템\\n둘째 줄"
USER = "    [기업명]: 자리표시\\n" + json.dumps(data_split, ensure_ascii=False)
plan = ask(SYSTEM, USER, TOOLS_RESPONSES)
```

### 셀 14 · code

```python
import ast
HINTS = ast.literal_eval(plan)
SET_MAX_STEPS = len(HINTS) * 3
report = ask("가상 검토", "- " + "\\n- ".join(HINTS), TOOLS_RESPONSES)
```

### 셀 18 · code

```python
MODEL_RESP = "가상-모델"
TOLERANCE = 1.1
SET_MAX_STEPS = 12
insight = ask("가상 인사이트", "가상", TOOLS_RESPONSES_websearch)
```
'''
