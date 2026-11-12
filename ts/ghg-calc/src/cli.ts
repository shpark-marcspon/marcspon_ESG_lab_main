// 예제 CLI. 파일을 읽고 쓰는 것은 여기뿐이다 (엔진은 값만 받는다).
//   pnpm tsx src/cli.ts calc <activity.json> --factors <factors.json> [--factors <more.json>] [--gwp <id>] [--group-by orgUnit,period,scope,sourceType]
// 형식이 틀린 행(알 수 없는 sourceType 등)은 엔진이 invalid_activity 로 돌려준다.
// 종료 코드: 0 전부 성공 / 1 실패한 행 있음 / 2 입력이나 사용법 오류

import { readFileSync } from "node:fs";
import { aggregate, type GroupKey } from "./aggregate.js";
import { calculate } from "./calc.js";
import { mergeTables, validateTables } from "./tables.js";
import type { ActivityRow, ReferenceTables } from "./types.js";

const GROUP_KEYS: readonly GroupKey[] = ["orgUnit", "period", "scope", "sourceType"];

function usage(message: string): never {
  process.stderr.write(
    `${message}\n사용법: tsx src/cli.ts calc <activity.json> --factors <factors.json> [--factors <more.json>] [--gwp <id>] [--group-by ${GROUP_KEYS.join(",")}]\n`,
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

const [command, activityPath, ...rest] = process.argv.slice(2);
if (command !== "calc" || activityPath === undefined) usage("calc 명령과 활동 데이터 파일이 필요하다");

const factorPaths: string[] = [];
let gwpFlag: string | undefined;
let groupBy: GroupKey[] = ["scope", "sourceType"];
for (let i = 0; i < rest.length; i += 2) {
  const flag = rest[i];
  const value = rest[i + 1];
  if (value === undefined) usage(`값이 없는 옵션: ${flag}`);
  if (flag === "--factors") factorPaths.push(value);
  else if (flag === "--gwp") gwpFlag = value;
  else if (flag === "--group-by") {
    const keys = value.split(",");
    const unknown = keys.filter((k) => !GROUP_KEYS.includes(k as GroupKey));
    if (unknown.length > 0) usage(`알 수 없는 묶음 기준: ${unknown.join(", ")}`);
    groupBy = keys as GroupKey[];
  } else usage(`알 수 없는 옵션: ${flag}`);
}
if (factorPaths.length === 0) usage("--factors 가 필요하다");

const merged = mergeTables(...factorPaths.map((p) => readJson(p) as Partial<ReferenceTables>));
const validated = validateTables(merged);
if (!validated.ok) {
  process.stderr.write(`기준정보 표에 문제가 있다:\n${validated.problems.map((p) => `  ${p.where}: ${p.problem}`).join("\n")}\n`);
  process.exit(2);
}

const activity = readJson(activityPath) as { gwpSetId?: unknown; activities?: unknown };
if (!Array.isArray(activity.activities)) usage("활동 데이터 파일에 activities 배열이 없다");
const gwpSetId = gwpFlag ?? activity.gwpSetId;
if (typeof gwpSetId !== "string") usage("GWP 세트를 정하지 않았다. --gwp 또는 활동 데이터 파일의 gwpSetId 가 필요하다 (기본값 없음)");

const rows = calculate(activity.activities as ActivityRow[], validated.tables, { gwpSetId });
process.stdout.write(`${JSON.stringify({ gwpSetId, rows, aggregation: aggregate(rows, groupBy) }, null, 2)}\n`);
process.exit(rows.some((r) => r.status === "failed") ? 1 : 0);
