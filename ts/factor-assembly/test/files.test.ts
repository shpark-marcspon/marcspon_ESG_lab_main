import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { AssemblyFileError, defaultFactorsPath, defaultMappingPath, loadGuidelineRecords, loadMapping, readValueRaw } from "../src/index.js";
import { mapping, records } from "./helpers.js";

const here = dirname(fileURLToPath(import.meta.url));
const scratch = () => mkdtempSync(join(tmpdir(), "factor-assembly-"));

describe("value_raw 읽기", () => {
  it("십진 표기는 그대로, 세 자리 쉼표 표기는 쉼표만 걷어 낸다", () => {
    expect(readValueRaw("35.2")).toBe("35.2");
    expect(readValueRaw("43.0")).toBe("43.0");
    expect(readValueRaw("0")).toBe("0");
    expect(readValueRaw("73,200")).toBe("73200");
    expect(readValueRaw("1,234,567.89")).toBe("1234567.89");
  });

  it.each(["", "-", "1,2", "73,20", "7,3200", ",200", "1.2.3", "7e4", " 73200", "+1", "-1", "-1,000", "0.99 ", "약 1", "1,000,00"])("%j 는 null", (raw) => {
    expect(readValueRaw(raw)).toBeNull();
  });

  it("문자열이 아니면 null (number 를 받지 않는다)", () => {
    expect(readValueRaw(73200)).toBeNull();
    expect(readValueRaw(null)).toBeNull();
    expect(readValueRaw(undefined)).toBeNull();
  });
});

describe("파일 어댑터", () => {
  it("계수 표에서 지침 출처의 레코드만 읽는다", () => {
    const bySource = new Map<string, number>();
    for (const r of records) bySource.set(r.source.id, (bySource.get(r.source.id) ?? 0) + 1);
    expect(Object.fromEntries(bySource)).toEqual({ "kr-ets-byl12": 108, "kr-ets-byl11": 55, "kr-ets-byl10": 385, "kr-ets-byl06": 12 });
    expect(records.every((r) => !r.synthetic)).toBe(true);
    expect(records.every((r) => !("value" in r))).toBe(true);
  });

  it("대응표를 읽는다", () => {
    expect(mapping.sectors.map((s) => s.key)).toEqual(["energy_industries", "manufacturing_construction", "commercial_public", "residential_other"]);
    expect(mapping.sector_overlaps.map((o) => o.division)).toEqual(["38", "58", "59"]);
    expect(mapping.tier_minimums.length).toBe(45);
    expect(mapping.fuel_names.filter((e) => e.basis === "assumed").length).toBe(25);
    expect(mapping.national_ef_names?.length).toBe(8);
    expect(mapping.fuel_states.length).toBe(34);
  });

  it("없는 파일, JSON 이 아닌 줄, 형식이 다른 지침 레코드는 예외다 (빈 결과가 아니다)", () => {
    const dir = scratch();
    expect(() => loadGuidelineRecords(join(dir, "none.jsonl"))).toThrow(AssemblyFileError);
    expect(() => loadMapping(join(dir, "none.json"))).toThrow(AssemblyFileError);
    const first = readFileSync(defaultFactorsPath(), "utf8").split("\n").find((line) => line.includes('"kr-ets-byl12"')) as string;
    const write = (name: string, text: string) => {
      writeFileSync(join(dir, name), text);
      return join(dir, name);
    };
    expect(loadGuidelineRecords(write("one.jsonl", `${first}\n`)).length).toBe(1);
    expect(() => loadGuidelineRecords(write("bad.jsonl", `${first}\n{not json\n`))).toThrow(/2행이 JSON 이 아니다/);
    const broken = JSON.parse(first) as Record<string, unknown>;
    delete broken.value_raw;
    expect(() => loadGuidelineRecords(write("field.jsonl", `${JSON.stringify(broken)}\n`))).toThrow(/1행: value_raw/);
    expect(() => loadGuidelineRecords(write("other.jsonl", `${JSON.stringify({ source: { id: "synthetic-other" } })}\n`))).toThrow(/한 건도 없다/);
    const noSectors = { ...(JSON.parse(readFileSync(defaultMappingPath(), "utf8")) as Record<string, unknown>) };
    delete noSectors.sectors;
    expect(() => loadMapping(write("map.json", JSON.stringify(noSectors)))).toThrow(/sectors/);
  });

  it("조립 함수(domain)는 파일·네트워크·시계를 쓰지 않는다", () => {
    const dir = join(here, "..", "src", "domain");
    for (const file of readdirSync(dir)) {
      const text = readFileSync(join(dir, file), "utf8");
      expect(text, file).not.toMatch(/from "node:|readFileSync|fetch\(|Date\.now|new Date|Math\.random|process\./);
      expect(text, file).not.toMatch(/parseFloat|Number\(|\.value\b(?!\.)(?=\s*[*+/-])/);
    }
  });
});

describe("CLI", () => {
  const cli = join(here, "..", "src", "cli.ts");
  const run = (args: string[]) => spawnSync("pnpm", ["tsx", cli, ...args], { cwd: join(here, ".."), encoding: "utf8" });

  it("verify-handcalc 는 전부 일치하면 0 으로 끝난다", () => {
    const out = execFileSync("pnpm", ["tsx", cli, "verify-handcalc"], { cwd: join(here, ".."), encoding: "utf8" });
    expect(out).toContain("사례 10건: 일치 10, 어긋남·실패 0\n");
    expect(out).not.toContain("돌리지 않음");
    expect(out).toContain("결과 2.5596384 = 기대 2.5596384");
  });

  it("assemble 은 조립 조건 하나로 계수와 근거를 찍고, 실패하면 1 로 끝난다", () => {
    const dir = scratch();
    writeFileSync(join(dir, "ok.json"), JSON.stringify({ fuel: "경유", combustionType: "stationary", industry: { ksicCode: "20" }, tiers: { kind: "group_minimum", group: "A" } }));
    const ok = run(["assemble", join(dir, "ok.json")]);
    expect(ok.status).toBe(0);
    expect((JSON.parse(ok.stdout) as { value: { factor: { oxidationFactor: string } } }).value.factor.oxidationFactor).toBe("1.0");
    writeFileSync(join(dir, "missing.json"), JSON.stringify({ fuel: "경유", combustionType: "stationary", industry: { ksicCode: "20" } }));
    const failed = run(["assemble", join(dir, "missing.json")]);
    expect(failed.status).toBe(1);
    expect(JSON.parse(failed.stdout)).toMatchObject({ ok: false, failure: { kind: "missing_input", field: "tiers" } });
  });

  it("compare-electricity 는 선택지 21개를 계산하고, 사용량·단위·GWP 가 빠지면 2 로 끝난다", () => {
    const out = run(["compare-electricity", "--amount", "1234.567", "--unit", "MWh", "--gwp", "kr-ets-decree-annex2", "--json"]);
    expect(out.status).toBe(0);
    const parsed = JSON.parse(out.stdout) as { rows: Array<{ co2e: string | null }> };
    expect(parsed.rows.length).toBe(21);
    expect(parsed.rows[0]?.co2e).toBe("567.1731662102");
    expect(run(["compare-electricity", "--amount", "1234.567", "--unit", "MWh"]).status).toBe(2);
  });

  it("assemble-electricity 는 출처가 빠지면 1 로 끝나고 무엇이 빠졌는지 찍는다", () => {
    const dir = scratch();
    writeFileSync(join(dir, "e.json"), JSON.stringify({ energyType: "electricity", gasForm: "per_gas" }));
    const failed = run(["assemble-electricity", join(dir, "e.json")]);
    expect(failed.status).toBe(1);
    expect(JSON.parse(failed.stdout)).toMatchObject({ ok: false, failure: { kind: "missing_input", field: "source" } });
  });

  it("coverage --json 은 잘리지 않은 표를 낸다 (174줄, 파이프로 받아도 끝까지)", () => {
    const out = run(["coverage", "--json"]);
    expect(out.status).toBe(0);
    const parsed = JSON.parse(out.stdout) as { rows: unknown[]; summary: unknown[] };
    expect(parsed.rows.length).toBe(29 * 3 * 2);
    expect(parsed.summary.length).toBe(6);
  });

  it("없는 계수 표나 틀린 사용법은 2 로 끝난다", () => {
    expect(run(["verify-handcalc", "--factors", join(scratch(), "none.jsonl")]).status).toBe(2);
    expect(run(["nope"]).status).toBe(2);
  });
});
