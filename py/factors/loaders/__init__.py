"""출처별 로더. LOADERS 의 순서가 factors.jsonl 의 순서다."""

from . import defra, epa, keiti, kr_ets, kr_ets_byl06, kr_gir

LOADERS = {
    keiti.SOURCE_ID: keiti.load,
    defra.SOURCE_ID: defra.load,
    epa.SOURCE_ID: epa.load,
    kr_gir.SOURCE_2025: kr_gir.load_2025,
    kr_gir.SOURCE_2024: kr_gir.load_2024,
    kr_ets.BYL12: kr_ets.load_byl12,
    kr_ets.BYL11: kr_ets.load_byl11,
    kr_ets.BYL10: kr_ets.load_byl10,
    # 새 출처는 끝에 붙인다. 앞선 줄의 순서와 내용이 바뀌면 안 된다.
    kr_ets_byl06.BYL06: kr_ets_byl06.load_byl06,
}
