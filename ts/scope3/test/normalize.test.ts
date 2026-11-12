import { describe, expect, it } from "vitest";
import { groupKey, groupRows, normalizeItemName, unitDim } from "../src/index.js";
import { row } from "./helpers.js";

describe("품목 정규화", () => {
  it("공백, 대소문자, 전각, 괄호 표기 차이를 지운다", () => {
    const variants = ["A4 용지 80g", "a4용지 80G", "A4용지(80g)", "Ａ４ 용지  80ｇ", " A4-용지_80g "];
    expect(new Set(variants.map(normalizeItemName))).toEqual(new Set(["a4용지80g"]));
  });

  it("규격 숫자가 다르면 다른 키다", () => {
    expect(normalizeItemName("A4 용지 80g")).not.toBe(normalizeItemName("A4 용지 70g"));
    expect(normalizeItemName("볼트 M8x1.25")).toBe("볼트m8x1.25");
    expect(normalizeItemName("볼트 M8×1.25")).toBe(normalizeItemName("볼트 m8*1.25"));
    expect(normalizeItemName("볼트 M8x1.25")).not.toBe(normalizeItemName("볼트 M8x125"));
  });

  it("문장 부호로 쓰인 점은 지우고 소수점은 남긴다", () => {
    expect(normalizeItemName("황산 98%.")).toBe("황산98");
    expect(normalizeItemName("필름 0.5T")).toBe("필름0.5t");
  });

  it("단위 표기를 차원으로 바꾼다. 모르는 단위는 unknown 이다", () => {
    expect(unitDim("KG")).toBe("kg");
    expect(unitDim("톤")).toBe("tonne");
    expect(unitDim("tonnes")).toBe("tonne");
    expect(unitDim("인km")).toBe("passenger_km");
    expect(unitDim("Room per night")).toBe("room_night");
    expect(unitDim("대")).toBe("unknown");
    expect(unitDim(null)).toBe("unknown");
  });
});

describe("묶음", () => {
  it("같은 품목·같은 단위 차원의 행을 한 묶음으로 묶고 입력 순서를 지킨다", () => {
    const rows = [
      row({ rowId: "a", itemName: "A4 용지 80g" }),
      row({ rowId: "b", itemName: "폴리프로필렌 수지" }),
      row({ rowId: "c", itemName: "a4용지 80G" }),
      row({ rowId: "d", itemName: "A4용지(80g)", unit: "KG" }),
    ];
    const groups = groupRows(rows);
    expect(groups.map((group) => group.rows.map((entry) => entry.rowId))).toEqual([["a", "c", "d"], ["b"]]);
  });

  it("단위 차원이 다르면 같은 품목이라도 따로 묶는다", () => {
    expect(groupKey(row({ itemName: "A4 용지", unit: "kg" }))).not.toBe(groupKey(row({ itemName: "A4 용지", unit: "box" })));
    expect(groupKey(row({ itemName: "A4 용지", unit: "t" }))).toBe(groupKey(row({ itemName: "A4 용지", unit: "톤" })));
    expect(groupKey(row({ itemName: "A4 용지", unit: "box" }))).not.toBe(groupKey(row({ itemName: "A4 용지", unit: "대" })));
  });

  it("운송 행은 수단이 다르면 따로 묶는다", () => {
    const transport = { origin: "부산", destination: "평택", weightKg: "1000", distanceKm: "300" };
    const truck = row({ itemName: "원자재 운송", unit: null, transport: { ...transport, mode: "트럭" } });
    const rail = row({ itemName: "원자재 운송", unit: null, transport: { ...transport, mode: "철도" } });
    expect(groupKey(truck)).not.toBe(groupKey(rail));
    expect(groupKey(truck)).toBe(groupKey({ ...truck, transport: { ...transport, mode: " 트럭 ", distanceKm: null } }));
  });

  it("글자가 남지 않는 품목명은 서로 묶지 않는다", () => {
    const groups = groupRows([row({ rowId: "a", itemName: "---" }), row({ rowId: "b", itemName: "***" })]);
    expect(groups).toHaveLength(2);
  });
});
