import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const packageDir = fileURLToPath(new URL("..", import.meta.url));
const data = (name: string) => fileURLToPath(new URL(`../../../data/ghg-calc/${name}`, import.meta.url));
const run = (...args: string[]) =>
  spawnSync("pnpm", ["tsx", "src/cli.ts", ...args], { cwd: packageDir, encoding: "utf8" });

describe("CLI 예제", () => {
  it("calc <activity.json> --factors <factors.json> 가 행 결과와 집계를 낸다", () => {
    const result = run("calc", data("examples/activity.synthetic.json"), "--factors", data("examples/factors.synthetic.json"));
    expect(result.status).toBe(0);
    const output = JSON.parse(result.stdout);
    expect(output.gwpSetId).toBe("synthetic-gwp");
    expect(output.rows.map((r: { status: string }) => r.status)).toEqual(["ok", "ok", "ok", "ok", "ok"]);
    // r1 은 검산 사례 H08, r5 는 H11 과 같은 입력이다
    expect(output.rows[0].co2e).toBe("1.8225");
    expect(output.rows[4].co2e).toBe("0.88023725");
    expect(output.aggregation.total.complete).toBe(true);
  }, 30_000);

  it("실패한 행이 있으면 종료 코드 1 이고 실패가 출력에 남는다", () => {
    const result = run("calc", data("examples/activity.synthetic.json"), "--factors", data("examples/factors.synthetic.json"), "--gwp", "no-such-set");
    expect(result.status).toBe(1);
    const output = JSON.parse(result.stdout);
    expect(output.rows[0].failure).toEqual({ kind: "gwp_set_not_found", gwpSetId: "no-such-set" });
    expect(output.aggregation.total).toMatchObject({ co2e: "0", okRowCount: 0, complete: false });
  }, 30_000);

  it("--factors 가 없으면 종료 코드 2", () => {
    const result = run("calc", data("examples/activity.synthetic.json"));
    expect(result.status).toBe(2);
    expect(result.stdout).toBe("");
  }, 30_000);
});
