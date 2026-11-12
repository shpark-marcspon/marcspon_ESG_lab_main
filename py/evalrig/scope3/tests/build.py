"""시험 고정물 짓기 — synthetic, 출처 없음. 글은 `가상 …`, 수는 첫 유효 숫자 9(scope3colab README 「지어낸 입력」 규칙).

`section4-EVC.sh` 도 이 함수로 xlsx 를 지어 CLI 를 돌린다(저장소에 .xlsx 0).
"""

import copy
from pathlib import Path
from typing import Any

from openpyxl import Workbook

ROWS1 = "scope3colab.rows/1"

# 받은 결과 파일 꼴의 지은 판: 시트 셋(C3·C5·C6) — 열 이름은 scope3colab rows.py 칸 표의 「코랩 결과 열」
RESULT_SHEETS: dict[str, tuple[list[str], list[list[Any]]]] = {
    "C3_연료에너지": (
        ["에너지원", "사용량", "배출계수(kgCO2eq/단위)", "EF단위", "EF매핑명", "단위환산비고", "배출량(tCO2e)"],
        [["가상 연료A", 9, 0.9, "kgCO2eq/L", "가상 연료A 계수", None, 0.009],
         ["가상 연료B", 90, 0.99, "kgCO2eq/kWh", "가상 연료B 계수", None, 0.099]],
    ),
    "C5_사업장폐기물": (
        ["폐기물 종류", "처리량", "배출계수(kgCO2eq/kg)", "처리방법_정규화", "화석기원 CO2e(tCO2e)", "생물기원 CO2e(tCO2e)",
         "바이오매스 구분", "배출량(tCO2e)", "단위환산비고"],
        [["가상 폐기물", 900, 0.09, "가상 소각", 0.09, 0, "가상 화석", 0.09, None]],
    ),
    "C6_출장": (
        ["이동수단", "계산방식", "출장비용", "EF_DB", "NAICS", "배출계수", "배출계수단위", "배출량(tCO2e)", "계산식", "정규화이동수단",
         "이동거리(km)", "출장인원", "이동횟수", "거리왕복계수"],
        [["가상 항공권", "Spend", 9000, "가상 항공 지출", "999999", 0.0009, "kgCO2e/KRW", 0.009, "가상 식", None, None, None, None, None],
         ["가상 버스", "Distance", None, None, None, 0.09, None, 0.0099, "가상 식", "가상 버스", 90, 1, 1, 2]],
    ),
    "기업정보": (["가상"], [["가상"]]),
}


def write_result_xlsx(path: Path, sheets: dict[str, tuple[list[str], list[list[Any]]]] | None = None) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for title, (header, rows) in (sheets or RESULT_SHEETS).items():
        ws = wb.create_sheet(title)
        ws.append(header)
        for r in rows:
            ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def rows1(rows: list[dict[str, Any]], side: str = "engine") -> dict[str, Any]:
    return {"schema": ROWS1, "side": side, "rows": rows, "not_compared": []}


def purchase_row(row: int, rep: str | None, rep_value: str | None, recs: list[str | None], co2e: str | None = "0.9",
                 sheet: str = "C1") -> dict[str, Any]:  # fmt: skip
    fields: dict[str, Any] = {"구매금액(KRW)": "9000", "대표EF": rep, "대표EF_배출계수": rep_value, "배출량(tCO2e)": co2e,
                              "산정출처": "가상 출처"}  # fmt: skip
    for i in range(1, 5):
        fields[f"추천EF{i}"] = recs[i - 1] if i - 1 < len(recs) else None
        fields[f"추천EF{i}_배출계수"] = "0.9" if i - 1 < len(recs) and recs[i - 1] else None
    return {"sheet": sheet, "row": row, "item": f"가상 품목 {row}", "fields": fields}


def energy_row(row: int, name: str, unit: str, value: str, co2e: str | None) -> dict[str, Any]:
    return {"sheet": "C3", "row": row, "item": f"가상 연료 {row}", "fields": {
        "사용량": "9", "배출계수(kgCO2eq/단위)": value, "EF단위": unit, "EF매핑명": name, "단위환산비고": None, "배출량(tCO2e)": co2e}}  # fmt: skip


# ── result@1(지은 것 — esg-app contracts/shapes.py Scope3ColabDone 꼴의 칸만) ───────────────────────────


def purchase1(row_id: str, title: str | None, value: str | None, recs: list[str], group: str = "가상 묶음",
              co2e: str | None = "0.9") -> dict[str, Any]:  # fmt: skip
    return {
        "rowId": row_id, "kind": "purchase", "month": None, "amount": "9000", "spendKrw": "9000", "spendNote": "",
        "factor": {"title": title, "value": value, "unit": "kgCO2e/KRW", "column": "가상 열", "source": "rule"},
        "co2e": co2e, "unit": "tCO2eq", "formula": "",
        "recommendations": [{"title": t, "value": f"0.9{i}", "expectedCo2e": f"0.9{i}"} for i, t in enumerate(recs, start=1)],
        "group": {"key": "가상", "name": group, "reason": "", "family": "", "category": ""},
        "origin": "rule", "originSteps": [], "missing": [],
    }  # fmt: skip


def activity1(row_id: str, kind: str, *, title: str | None = None, code: str | None = None, value: str | None = "0.9",
              unit: str = "가상 단위", treatment: str | None = None, biogenic_note: str | None = None,
              mode: str | None = None, co2e: str | None = "0.09") -> dict[str, Any]:  # fmt: skip
    return {
        "rowId": row_id, "kind": kind, "month": None,
        "activity": {"value": "9", "unit": "가상", "baseValue": "9", "baseUnit": "가상", "note": None},
        "factor": {"sheet": "가상", "title": title, "value": value, "unit": unit, "column": None, "code": code},
        "co2e": co2e, "unit": "tCO2eq", "formula": "", "treatment": treatment,
        "biogenic": None if biogenic_note is None else {"fossilCo2e": co2e, "biogenicCo2e": "0", "fcf": None, "note": biogenic_note},
        "mode": None if mode is None else {"value": mode, "origin": "rule"},
        "distance": None, "origin": "rule", "originSteps": [], "missing": [],
    }  # fmt: skip


def energy1(row_id: str, kind: str, *, title: str | None = "가상 계수", unit: str = "kgCO2eq/가상", value: str | None = "0.9",
            co2e: str | None = "0.09", factor: bool = True) -> dict[str, Any]:  # fmt: skip
    return {
        "rowId": row_id, "kind": kind, "month": None,
        "activity": {"value": "9", "unit": "가상", "baseValue": "9", "baseUnit": "가상", "note": None},
        "factor": {"sheet": "가상", "title": title, "value": value, "unit": unit, "column": None, "code": None} if factor else None,
        "co2e": co2e, "unit": "tCO2eq", "formula": "", "match": None, "allocation": None, "biogenic": None, "product": None,
        "investment": None, "origin": "rule", "originSteps": [], "missing": [],
    }  # fmt: skip


def result1(sheets: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": "scope3.colab.result@1", "status": "done", "reportYear": 9999,
        "factorBook": {"sheet": "가상", "column": None, "year": 9999, "reload": "reloaded", "reloadProblem": None},
        "factorSheets": [], "sheets": sheets, "llmCalls": {},
    }  # fmt: skip


def processed(sheet: str, category: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"sheet": sheet, "category": category, "status": "processed", "rows": rows}


def base_result1() -> dict[str, Any]:
    return result1([
        processed("C1_가상", "Category1", [
            purchase1("C1_가상!4", "가상 계수 A", "0.9", ["가상 계수 B", "가상 계수 C", "가상 계수 D", "가상 계수 E"]),
            purchase1("C1_가상!5", "가상 계수 B", "0.99", ["가상 계수 A"]),
        ]),
        processed("C6-2_가상", "Category6", [activity1("C6-2_가상!4", "travel_spend", title="가상 항공 지출", code="999999")]),
        {"sheet": "C5_가상", "category": "Category5", "status": "failed", "error": "가상 실패"},
        processed("C15_가상", "Category15", [energy1("C15_가상!4", "investment", factor=False)]),
    ])  # fmt: skip


def edit(row_id: str, category: str, *, excluded: bool = False, choice: str | None = None, rank: int | None = None,
         title: str | None = None, value: str | None = None, co2e: str | None = None) -> dict[str, Any]:  # fmt: skip
    factor = None
    if choice is not None:
        factor = {"choice": choice, "rank": rank, "title": title, "value": value, "source": "가상 출처", "unit": "kgCO2e/KRW",
                  "co2e": co2e, "biogenicCo2e": None,
                  "ai": {"title": "가상 계수 A", "value": "0.9", "source": "가상", "co2e": "0.9"}}  # fmt: skip
    return {"rowId": row_id, "category": category, "excluded": excluded, "factor": factor,
            "editedAt": "2099-09-09T09:09:09+09:00", "editedBy": {"id": "가상사람id", "name": "가상사람이름"},
            "reason": "가상 사유 글"}  # fmt: skip


def edits(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"runId": "9999", "rows": copy.deepcopy(rows)}


def write_w5_files(tmp_path: Path) -> tuple[dict[str, Any], Path, Path]:
    """W5 지은 템플릿·계수 책·짝 파일(드롭다운 소제목·「선택안함」·같은 이름 두 줄 포함)."""
    t = Workbook()
    ws = t.active
    ws.title = "배출계수_드롭다운"
    ws.append(["DD_ENERGY", "DD_TRANSPORT_SPEND"])
    for row in (["가상 에너지명 소제목", "가상 운송 소제목"], ["가상 전기", "가상 트럭"], ["가상 가스", "가상 배"], ["선택안함", None]):
        ws.append(row)
    tp = tmp_path / "t.xlsx"
    t.save(tp)
    b = Workbook()
    e = b.active
    e.title = "에너지"
    e.append(["가상 머리"])
    e.append(["구분", "활동유형\n(에너지원)", "EF"])
    e.append(["가상", "가상 전기", 0.9])
    s = b.create_sheet("운송_Spend")
    s.append(["운송 구분", "한국어 품목명", "NAICS Title (US EPA)"])
    s.append(["가상", "가상 트럭", "Synthetic Truck"])
    s.append(["가상", "가상 트럭", "Synthetic Truck 2"])
    bp = tmp_path / "b.xlsx"
    b.save(bp)
    spec = {"schema": "evalrig.scope3.regress-w5/1", "skip": ["선택안함"], "skipFirst": True, "pairs": [
        {"id": "energy", "dropdown": "DD_ENERGY", "bookSheet": "에너지", "nameColumn": "활동유형\n(에너지원)", "answerColumn": "활동유형\n(에너지원)", "baseline": {"same": 1, "of": 2}},
        {"id": "transport-spend", "dropdown": "DD_TRANSPORT_SPEND", "bookSheet": "운송_Spend", "nameColumn": "한국어 품목명", "answerColumn": "NAICS Title (US EPA)", "baseline": {"same": 9, "of": 9}},
    ]}  # fmt: skip
    return spec, tp, bp
