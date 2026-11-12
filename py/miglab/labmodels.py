"""랩 models 파일: esg-app 의 core/llm/data/models.json 을 실행 때 읽어 판마다 OUT 에 쓴다(저장소에 사본 0).

판 셋(발주서 §1 G0-3, §6 ①, §7-2):
  old       그대로(옛 경로 → gpt-4.1·gpt-4.1-mini·gpt-4o)
  luna-raw  **routes 만** 바꿈 — 옛 경로 이름 셋을 gpt-6-luna 하나로(= 길 (ㄱ) 재지정만). 모델 항목은 손대지 않는다
  luna      luna-raw + luna 항목에 web_search 건당 요금(hostedToolPricing) 한 칸(감독 판단 ①·11-03 답). 그 밖의 칸
            (여유분·단가·structuredOutput)은 그대로
"""

import json
from pathlib import Path
from typing import Any, Final

from miglab import env

LUNA: Final = "gpt-6-luna"
OLD_ROUTES: Final = ("light-openai-41", "standard-openai-41", "standard-openai-4o")
VARIANTS: Final = ("old", "luna-raw", "luna")

WEB_SEARCH_PER_CALL: Final = 0.01
WEB_SEARCH_SOURCE: Final = (
    "2026-11-03 확인(miglab, 감독 marcspon-21 판단 ①). https://developers.openai.com/api/docs/pricing Tools 표 "
    "'| Web search | Web search (all models) | $10.00 / 1k calls + Search content tokens billed at model rates. |' 의 "
    "$10.00 / 1k calls. luna 이름을 적은 줄은 없고 「(all models)」 줄이다 — gpt-4.1·gpt-4o 항목과 같은 근거(같은 날 "
    "같은 줄로 대조: 두 항목의 0.01 도 이 줄과 같다). preview 줄(reasoning models gpt-5·o-series / non-reasoning)은 "
    "요청 type 'web_search' 가 아니라 해당 없음(추정 — 도구 이름으로 가름)."
)


def lab_models(variant: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"판은 {VARIANTS} 가운데 하나: {variant}")
    data = json.loads(env.ESGAPP_MODELS.read_text(encoding="utf-8")) if base is None else json.loads(json.dumps(base))
    if variant == "old":
        return data
    for route in OLD_ROUTES:
        if route not in data["routes"]:
            raise SystemExit(f"esg-app models.json 에 경로 {route} 가 없다 — 하네스를 고칠 자리")
        data["routes"][route] = [LUNA]
    if variant == "luna":
        luna = data["models"][LUNA]
        if "hostedToolPricing" in luna:
            raise SystemExit("esg-app 의 luna 항목에 이미 hostedToolPricing 이 있다 — 판 luna 를 다시 볼 자리")
        luna["hostedToolPricing"] = {"webSearchPerCall": WEB_SEARCH_PER_CALL}
        luna["notes"] = [*luna.get("notes", []), WEB_SEARCH_SOURCE]
    return data


def write_lab_models(variant: str, out: Path | None = None) -> Path:
    folder = out or env.OUT
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"models-{variant}.json"
    path.write_text(json.dumps(lab_models(variant), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def changed_routes(variant: str) -> dict[str, list[str]]:
    """판이 바꾼 경로(보고용)."""
    old = lab_models("old")["routes"]
    new = lab_models(variant)["routes"]
    return {route: new[route] for route in new if new[route] != old.get(route)}
