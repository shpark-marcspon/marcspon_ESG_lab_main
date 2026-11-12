"""노트북에서 옮긴 상수의 지문을 낸다(test_pins.py 의 값을 처음 만들 때와 노트북이 새 판으로 바뀌었을 때만 쓴다)."""
import hashlib
import json

from greenwash import checker, heuristics, wordcount
from greenwash.prompts import PROMPT_VERSION, default_prompts


def sha(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprints():
    pack = default_prompts(PROMPT_VERSION)
    return {
        "detect_system_prompt": sha(pack.detect_system_prompt),
        "eval_system_prompt": sha(pack.eval_system_prompt),
        "repair_fix_prompt": sha(pack.repair_fix_prompt),
        "low_only_prompt": sha(pack.low_only_prompt),
        "GREEN_TERMS": sha(wordcount.GREEN_TERMS),
        "DEFAULT_EXCLUDE_PATTERNS": sha(wordcount.DEFAULT_EXCLUDE_PATTERNS),
        "DEFAULT_ENV_KEYWORDS": sha(heuristics.DEFAULT_ENV_KEYWORDS),
        "_LABEL_PATTERNS": sha(heuristics._LABEL_PATTERNS),
        "_FACT_TENSE_RE": sha(heuristics._FACT_TENSE_RE.pattern),
        "_PROMO_PHRASES_RE": sha(heuristics._PROMO_PHRASES_RE.pattern),
        "_HIGH_RISK_KEYWORDS_RE": sha(heuristics._HIGH_RISK_KEYWORDS_RE.pattern),
        "SAFE_LIMITS_INT": sha({k: list(v) for k, v in checker.SAFE_LIMITS_INT.items()}),
    }


if __name__ == "__main__":
    print(json.dumps(fingerprints(), ensure_ascii=False, indent=4))
