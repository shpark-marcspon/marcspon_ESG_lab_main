// 조합기 CLI. 파일을 읽는 것은 여기와 adapters/files.ts 뿐이고, 결과는 표준 출력으로만 낸다 (파일을 쓰지 않는다).
//   pnpm tsx src/cli.ts verify-handcalc [--factors <jsonl>] [--mapping <json>] [--json]
//       엔진의 검산 사례를 계수 표에서 조립한 계수로 다시 계산해 기대값과 문자열까지 견준다.
//   pnpm tsx src/cli.ts assemble <조립 조건 json> [--factors <jsonl>] [--mapping <json>]
//       조립 조건 하나로 계수와 근거를 찍는다.
//   pnpm tsx src/cli.ts coverage [--factors <jsonl>] [--mapping <json>] [--states-from <대응표 json>] [--json]
//       별표 12 발열량 표의 연료마다 조합 셋 × 상태 지정 여부로 조립·계산해 덮인 범위를 센다 (실패는 종류별로).
//       「상태를 준 경우」의 상태는 --states-from 의 대응표(없으면 --mapping 의 대응표)에 실린 상태다.
//   pnpm tsx src/cli.ts assemble-electricity <조립 조건 json> [--factors <jsonl>] [--mapping <json>]
//       구매 전력 조립 조건 하나로 계수와 근거를 찍는다.
//   pnpm tsx src/cli.ts compare-electricity --amount <사용량> --unit <MWh|kWh> --gwp <GWP 세트 id> [--json]
//       같은 전기 사용량을 표-69 와 GIR 공표 값(판·기간·발전단/소비단·산정기준·기체별/CO2e)마다 계산한 비교 표. 기대값이 아니다.
// 종료 코드: 0 전부 통과(또는 조립 성공) / 1 어긋남·실패 있음 / 2 입력이나 사용법 오류

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { validateTables } from "ghg-calc";
import { AssemblyFileError, loadElectricityRecords, loadGuidelineRecords, loadMapping } from "./adapters/files.js";
import { assembleCombustionFactor } from "./domain/assemble.js";
import { assemblePurchasedElectricityFactor, electricityComparison, type ElectricityRequest } from "./domain/electricity.js";
import { COVERAGE_MODES, coverageRows, coverageSummary, statesFromMapping } from "./domain/coverage.js";
import { replayHandCases, replaySummary, type HandCase, type ReplaySpec, type ReplayTables } from "./domain/replay.js";
import type { AssemblyMapping, AssemblyRequest, FactorRecord } from "./domain/types.js";

const here = dirname(fileURLToPath(import.meta.url));
const ghgData = join(here, "..", "..", "..", "data", "ghg-calc");

function usage(message: string): never {
  process.stderr.write(
    `${message}\n사용법: tsx src/cli.ts verify-handcalc [--factors <jsonl>] [--mapping <json>] [--json]\n` +
      "        tsx src/cli.ts assemble <조립 조건 json> [--factors <jsonl>] [--mapping <json>]\n" +
      "        tsx src/cli.ts coverage [--factors <jsonl>] [--mapping <json>] [--states-from <json>] [--json]\n" +
      "        tsx src/cli.ts assemble-electricity <조립 조건 json> [--factors <jsonl>] [--mapping <json>]\n" +
      "        tsx src/cli.ts compare-electricity --amount <사용량> --unit <단위> --gwp <GWP 세트 id> [--json]\n",
  );
  process.exit(2);
}

function readJson(path: string): unknown {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    usage(`읽을 수 없다: ${path} (${error instanceof Error ? error.message : String(error)})`);
  }
}

const [command, ...rest] = process.argv.slice(2);
const positional: string[] = [];
let factorsPath: string | undefined;
let mappingPath: string | undefined;
let statesFromPath: string | undefined;
let amount: string | undefined;
let amountUnit: string | undefined;
let gwpSetId: string | undefined;
let asJson = false;
for (let i = 0; i < rest.length; i += 1) {
  const arg = rest[i] as string;
  if (arg === "--json") asJson = true;
  else if (["--factors", "--mapping", "--states-from", "--amount", "--unit", "--gwp"].includes(arg)) {
    const value = rest[i + 1];
    if (value === undefined) usage(`값이 없는 옵션: ${arg}`);
    if (arg === "--factors") factorsPath = value;
    else if (arg === "--mapping") mappingPath = value;
    else if (arg === "--states-from") statesFromPath = value;
    else if (arg === "--amount") amount = value;
    else if (arg === "--unit") amountUnit = value;
    else gwpSetId = value;
    i += 1;
  } else if (arg.startsWith("--")) usage(`알 수 없는 옵션: ${arg}`);
  else positional.push(arg);
}

// 아래 두 명령은 출력이 클 수 있다. process.exit 은 파이프로 나가는 표준 출력을 다 비우기 전에 끝낼 수 있어
// 종료 코드만 정하고 자연히 끝낸다.

function printCoverage(records: FactorRecord[], mapping: AssemblyMapping, tables: ReplayTables): void {
  const states = statesFromMapping(statesFromPath === undefined ? mapping : loadMapping(statesFromPath));
  const inputs = { industry: { sector: "manufacturing_construction" }, powerGeneration: false, givenStates: states, gwpSetId: "kr-ets-decree-annex2", ...tables };
  const rows = coverageRows(records, mapping, inputs);
  const summary = coverageSummary(rows);
  process.exitCode = 0;
  if (asJson) {
    const statesFrom = statesFromPath ?? mappingPath ?? "(기본 대응표)";
    const echo = { industry: inputs.industry, powerGeneration: inputs.powerGeneration, gwpSetId: inputs.gwpSetId, statesFrom };
    process.stdout.write(`${JSON.stringify({ inputs: echo, summary, rows }, null, 2)}\n`);
    return;
  }
  const fuels = new Set(rows.map((row) => row.fuel));
  const label = (mode: string) => COVERAGE_MODES.find((m) => m.mode === mode)?.label ?? mode;
  const counts = (record: Record<string, number>) =>
    Object.entries(record)
      .map(([k, v]) => `${k} ${v}`)
      .join(", ") || "-";
  const out: string[] = [
    `대응표 ${mappingPath ?? "(기본)"}, 상태를 준 경우의 상태 ${statesFromPath ?? "(같은 대응표)"}`,
    `분모: 별표 12 순발열량 표의 연료 ${fuels.size}개 (전기 포함). 업종 제조업·건설업, 발전 여부 false, 사용량은 그 연료의 별표 12 단위로 1000`,
    "",
    "| 조합 | 상태 | 조립 성공 | 조립 실패 (종류별) | 계산 성공 | 계산 실패 (종류별) |",
    "|---|---|---|---|---|---|",
  ];
  for (const cell of summary) {
    out.push(
      `| ${label(cell.mode)} | ${cell.stateGiven ? "줌" : "안 줌"} | ${cell.assembled}/${cell.fuels} | ${counts(cell.assemblyFailures)} | ${cell.calculated}/${cell.assembled} | ${counts(cell.calculationFailures)} |`,
    );
  }
  out.push("", `| 연료 | ${COVERAGE_MODES.flatMap((m) => [`${m.label} (상태 안 줌)`, `${m.label} (상태 줌)`]).join(" | ")} |`);
  out.push(`|---|${COVERAGE_MODES.flatMap(() => ["---", "---"]).join("|")}|`);
  for (const fuel of fuels) {
    const cells = rows
      .filter((row) => row.fuel === fuel)
      .map((row) => {
        if (!row.assembly.ok) return `${row.assembly.kind}${row.assembly.where === null ? "" : ` (${row.assembly.where})`}`;
        const assumed = row.assembly.assumptions > 0 ? `(추정 ${row.assembly.assumptions})` : "";
        const calc = row.calculation === null || row.calculation.ok ? "계산" : `계산 실패 ${row.calculation.kind}`;
        return `조립${assumed}·${calc}`;
      });
    out.push(`| ${fuel} | ${cells.join(" | ")} |`);
  }
  process.stdout.write(`${out.join("\n")}\n`);
}

function verifyHandcalc(records: FactorRecord[], mapping: AssemblyMapping, tables: ReplayTables): void {
  const cases = (readJson(join(ghgData, "handcalc-cases.json")) as { cases: HandCase[] }).cases;
  const spec = readJson(join(ghgData, "handcalc-assembly.json")) as ReplaySpec;
  const rows = replayHandCases(cases, spec, records, mapping, tables);
  const summary = replaySummary(rows);
  process.exitCode = summary.failed === 0 && summary.matched > 0 ? 0 : 1;
  if (asJson) {
    process.stdout.write(`${JSON.stringify({ summary, rows }, null, 2)}\n`);
    return;
  }
  process.stdout.write(`계수 표 ${factorsPath ?? "(기본 경로)"} 의 지침 레코드 ${records.length}건, 대응표 ${mapping.guideline.version}\n`);
  for (const row of rows) {
    const o = row.outcome;
    const tail =
      o.status === "match"
        ? `결과 ${String(row.actualCo2e)} = 기대 ${row.expectedCo2e}  ← ${o.assembledFactorId}${o.assumptions.length > 0 ? `  [추정 대응: ${o.assumptions.join("; ")}]` : ""}`
        : o.status === "not_replayed"
          ? `돌리지 않음: ${o.reason}`
          : JSON.stringify(o);
    process.stdout.write(`${o.status === "match" ? "일치  " : o.status === "not_replayed" ? "제외  " : "어긋남"} ${row.caseId} ${row.title}\n       ${tail}\n`);
  }
  const skipped = summary.notReplayed > 0 ? `, 돌리지 않음 ${summary.notReplayed}` : "";
  process.stdout.write(`사례 ${summary.total}건: 일치 ${summary.matched}, 어긋남·실패 ${summary.failed}${skipped}\n`);
}

function compareElectricity(records: FactorRecord[], mapping: AssemblyMapping, tables: ReplayTables): void {
  if (amount === undefined || amountUnit === undefined || gwpSetId === undefined) usage("compare-electricity 는 --amount, --unit, --gwp 가 다 필요하다 (기본값 없음)");
  const rows = electricityComparison(amount, amountUnit, gwpSetId, records, mapping, tables);
  process.exitCode = rows.every((row) => row.co2e !== null) ? 0 : 1;
  if (asJson) {
    process.stdout.write(`${JSON.stringify({ amount, unit: amountUnit, gwpSetId, rows }, null, 2)}\n`);
    return;
  }
  const describe = (r: ElectricityRequest) =>
    r.gir === undefined
      ? "지침 별표 6 표-69 ('14~'16 평균)"
      : `GIR ${r.gir.edition} 공표, ${r.gir.period.kind === "single_year" ? `${r.gir.period.year}년` : r.gir.period.kind === "three_year_average" ? `${r.gir.period.from}~${r.gir.period.to} 평균` : "기간 표시 없음"}, ${r.gir.boundary === "generation" ? "발전단" : "소비단"}, ${r.gir.methodology === "ipcc_2006" ? "’06 IPCC" : "’96 IPCC(기존 기준)"}`;
  const out = [
    `사용량 ${amount} ${amountUnit}, GWP ${gwpSetId} (기체별 계수에만 곱한다. CO2e 계수에는 공표문이 적은 GWP 가 이미 들어 있다). 기대값이 아니라 비교다 (전력 계수를 무엇으로 할지는 MARC-ISSUE-20 15·17번 답 대기).`,
    "",
    "| 선택지 | 기체 | 계수 (원문 표기) | 레코드 id | 적용된 GWP | 결과 tCO2eq |",
    "|---|---|---|---|---|---|",
  ];
  for (const row of rows) {
    out.push(
      `| ${describe(row.request)} | ${row.request.gasForm === "co2e" ? "CO2e" : "기체별"} | ${row.factorValues.join("; ") || "-"} | ${row.recordIds.join(", ") || "-"} | ${row.co2eGwp ?? gwpSetId} | ${row.co2e ?? `실패 ${JSON.stringify(row.failure)}`} |`,
    );
  }
  process.stdout.write(`${out.join("\n")}\n`);
}

try {
  const electricityCommand = command === "assemble-electricity" || command === "compare-electricity";
  const records = electricityCommand ? loadElectricityRecords(factorsPath) : loadGuidelineRecords(factorsPath);
  const mapping = loadMapping(mappingPath);

  if (command === "assemble-electricity") {
    const requestPath = positional[0];
    if (requestPath === undefined) usage("조립 조건 파일이 필요하다");
    const result = assemblePurchasedElectricityFactor(readJson(requestPath) as ElectricityRequest, records, mapping);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exit(result.ok ? 0 : 1);
  }

  if (command === "assemble") {
    const requestPath = positional[0];
    if (requestPath === undefined) usage("조립 조건 파일이 필요하다");
    const result = assembleCombustionFactor(readJson(requestPath) as AssemblyRequest, records, mapping);
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    process.exit(result.ok ? 0 : 1);
  }

  if (command !== "verify-handcalc" && command !== "coverage" && command !== "compare-electricity") {
    usage("verify-handcalc, assemble, coverage, assemble-electricity, compare-electricity 중 하나가 필요하다");
  }

  const unitConversions = (readJson(join(ghgData, "unit-conversions.json")) as { unitConversions: unknown[] }).unitConversions;
  const gwpSets = (readJson(join(ghgData, "gwp-sets.json")) as { gwpSets: unknown[] }).gwpSets;
  const tables = validateTables({ unitConversions, gwpSets, factors: [] });
  if (!tables.ok) usage(`환산표·GWP 표에 문제가 있다: ${JSON.stringify(tables.problems)}`);

  if (command === "coverage") printCoverage(records, mapping, tables.tables);
  else if (command === "compare-electricity") compareElectricity(records, mapping, tables.tables);
  else verifyHandcalc(records, mapping, tables.tables);
} catch (error) {
  if (error instanceof AssemblyFileError) {
    process.stderr.write(`실패: ${error.message}\n`);
    process.exit(2);
  }
  throw error;
}
