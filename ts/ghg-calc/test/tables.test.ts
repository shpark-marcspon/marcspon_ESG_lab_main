import { describe, expect, it } from "vitest";
import { validateTables } from "../src/index.js";
import { loadTables, readData } from "./fixtures.js";

const valid = () => structuredClone(loadTables()) as unknown as Record<string, any>;
const problemsOf = (input: unknown) => {
  const result = validateTables(input);
  if (result.ok) throw new Error("검사를 통과하면 안 된다");
  return result.problems;
};

describe("기준정보 표 검사", () => {
  it("저장소의 데이터 파일은 검사를 통과한다", () => {
    expect(validateTables(loadTables()).ok).toBe(true);
    expect(validateTables(readData("examples/factors.synthetic.json")).ok).toBe(true);
  });

  it("출처 없는 수치는 표에 들어올 수 없다", () => {
    const tables = valid();
    delete tables.factors[0].source;
    expect(problemsOf(tables)).toEqual([{ where: "factors[0]", problem: "출처(source)가 없다" }]);
  });

  it("cited 출처는 url 과 확인 일자가 있어야 한다", () => {
    const tables = valid();
    tables.gwpSets[0].source = { kind: "cited", title: "t", url: "", checkedOn: "어제" };
    expect(problemsOf(tables).map((p) => p.problem)).toEqual([
      "cited 출처에 url 이 없다",
      "cited 출처에 확인 일자(checkedOn, YYYY-MM-DD)가 없다",
    ]);
  });

  it("JSON number 로 적힌 수치를 거부한다", () => {
    const tables = valid();
    tables.unitConversions[0].factor = 0.001;
    expect(problemsOf(tables)).toEqual([{ where: "unitConversions[0].factor", problem: "십진 문자열이 아니다: 0.001" }]);
  });

  it("음수 계수를 거부한다", () => {
    const tables = valid();
    tables.gwpSets[0].values.CH4 = "-21";
    expect(problemsOf(tables)).toEqual([{ where: "gwpSets[0].values.CH4", problem: '음수다: "-21"' }]);
  });

  it("배열이 빠진 표, 객체가 아닌 표를 거부한다", () => {
    expect(problemsOf({ factors: [], gwpSets: [] })).toEqual([{ where: "unitConversions", problem: "배열이 없다" }]);
    expect(problemsOf(null)).toEqual([{ where: "$", problem: "객체가 아니다" }]);
    expect(problemsOf([])).toEqual([{ where: "$", problem: "객체가 아니다" }]);
  });

  it("데이터 파일의 수치에는 synthetic 이거나 url·확인 일자가 붙어 있다", () => {
    const tables = loadTables();
    for (const record of [...tables.factors, ...tables.gwpSets]) {
      const synthetic = record.id.startsWith("synthetic");
      expect(record.source.kind, record.id).toBe(synthetic ? "synthetic" : "cited");
    }
    for (const conversion of tables.unitConversions) expect(conversion.source.kind).toBe("definition");
  });
});
