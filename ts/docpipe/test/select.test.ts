import { describe, expect, it } from "vitest";
import { findFurniture, planUnits, translatableCells } from "../src/index.js";
import { fixtureDocument, settings } from "./helpers.js";

describe("번역 대상 고르기", () => {
  const document = fixtureDocument();
  const plan = planUnits(document, settings);

  it("모든 블록이 처리 방식을 정확히 하나 받는다", () => {
    expect(plan.dispositions.size).toBe(document.blocks.length);
    for (const block of document.blocks) expect(plan.dispositions.has(block.id)).toBe(true);
  });

  it("쪽마다 되풀이되는 머리말은 보내지 않고 excluded 로 남긴다", () => {
    expect([...findFurniture(document, settings)]).toEqual(["가상 주식회사 지속가능경영보고서 (synthetic)"]);
    const excluded = document.blocks.filter((block) => plan.dispositions.get(block.id)?.kind === "excluded");
    expect(excluded).toHaveLength(6);
    expect(plan.units.some((unit) => unit.items.some((item) => item.text.includes("synthetic")))).toBe(false);
  });

  it("되풀이되는 소제목은 쪽의 절반에 못 미치면 장식으로 보지 않는다", () => {
    // '활동' 은 12쪽 중 4쪽에 나온다. 세 쪽 이상이지만 절반에 못 미친다.
    const longer = fixtureDocument({ pages: 12 });
    expect(longer.blocks.filter((block) => block.text === "활동")).toHaveLength(4);
    expect(findFurniture(longer, settings).has("활동")).toBe(false);
    expect(findFurniture(longer, settings).size).toBe(1);
  });

  it("쪽 번호와 로마자만 있는 블록은 보내지 않고 copy 로 남긴다", () => {
    expect(plan.dispositions.get("p1-b8")).toEqual({ kind: "copy", reason: "numeric" });
    expect(plan.dispositions.get("p1-b6")).toEqual({ kind: "copy", reason: "passthrough" });
  });

  it("표에서는 한글이 든 셀만 보내고 숫자 셀은 보내지 않는다", () => {
    const table = document.tables.find((candidate) => candidate.id === "p1-t1");
    expect(translatableCells(table!).map((item) => item.id)).toEqual(["p1-t1:r0c0", "p1-t1:r0c2", "p1-t1:r0c3", "p1-t1:r1c0", "p1-t1:r2c0"]);
    const sent = plan.units.flatMap((unit) => unit.items.map((item) => item.text));
    expect(sent).not.toContain("1,234");
    expect(sent).not.toContain("31.0");
  });

  it("low_confidence 표는 글자 셀을 보내고, failed 표는 빈 표로 접지 않고 source_failed 로 남긴다", () => {
    expect(plan.dispositions.get("p1-b90")).toEqual({ kind: "send" });
    expect(plan.dispositions.get("p1-b91")).toEqual({ kind: "source_failed", reason: "table_extraction_failed" });
  });

  it("단위는 읽는 순서(쪽, 쪽 안 순서)대로 나온다", () => {
    const pages = plan.units.map((unit) => unit.page);
    expect(pages).toEqual([...pages].sort((a, b) => a - b));
  });
});
