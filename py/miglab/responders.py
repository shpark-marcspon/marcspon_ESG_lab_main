"""모의 판(--mock·plan)의 공급사 답. 실호출 0 — 하네스가 끝까지 도는지와 호출 수를 보는 용도다(답의 뜻은 없다)."""

from miglab import harness


def for_module(module: str) -> harness.Responder:
    return harness.fallback_responder
