import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname } from "node:path";
import type { BudgetGuard, CallRequest, LlmResult, Tags } from "llm";
import { z } from "zod";
import type { UnitResultStore } from "../domain/pipeline.js";
import { ReconcileFileError } from "./files.js";

// 이어 하기. 요청 단위의 결과를 JSONL 로 남기고, 다시 돌릴 때 같은 요청의 결과가 있으면 모델을 부르지 않는다.
// 돈을 쓴 호출을 되풀이하지 않기 위한 것이다. 뜻 붙이기와 옮겨 쓰기 시험이 같이 쓴다.

/** 모델이 답한 결과. 이것만 남긴다. 공급사 오류·시간 초과·상한 초과는 측정 결과가 아니라 다시 부를 것이다. */
const FINAL_KINDS = new Set(["success", "schema_mismatch", "truncated", "refusal"]);

export interface StoredUnit<T> {
  unitId: string;
  /** 요청의 지문: 프롬프트 id, 변수, 경로, 출력 상한. 표나 지표 목록이 바뀌면 달라진다. */
  requestKey: string;
  kind: string;
  callId: string;
  costUsd: number;
  result: LlmResult<T>;
}

export function requestKey(request: CallRequest<unknown>): string {
  const body = { prompt: request.prompt, vars: request.vars ?? {}, route: request.route, schemaName: request.schemaName ?? null, maxOutputTokens: request.maxOutputTokens ?? null };
  return createHash("sha256").update(JSON.stringify(body)).digest("hex");
}

const lineSchema = z.object({
  unitId: z.string(),
  requestKey: z.string(),
  kind: z.string(),
  callId: z.string(),
  costUsd: z.number(),
  result: z.object({ kind: z.string(), meta: z.object({ callId: z.string(), totalCostUsd: z.number() }).loose() }).loose(),
});

/**
 * 파일 하나에 이어 쓰는 저장소. `schema` 는 성공 결과의 `data` 를 다시 읽을 때 확인하는 스키마다:
 * 파일에서 읽은 답도 모델의 답과 같은 문을 지나야 한다.
 */
export class JsonlUnitStore<T> implements UnitResultStore<T> {
  private readonly entries = new Map<string, StoredUnit<T>>();

  constructor(
    private readonly path: string,
    schema: z.ZodType<T>,
  ) {
    mkdirSync(dirname(path), { recursive: true });
    if (!existsSync(path)) return;
    readFileSync(path, "utf8")
      .split("\n")
      .filter((line) => line.trim() !== "")
      .forEach((line, index) => {
        let raw: unknown;
        try {
          raw = JSON.parse(line);
        } catch {
          throw new ReconcileFileError(`${path}:${index + 1}: 저장된 결과를 JSON 으로 읽을 수 없다`);
        }
        const parsed = lineSchema.safeParse(raw);
        if (!parsed.success) throw new ReconcileFileError(`${path}:${index + 1}: 저장된 결과의 형식이 맞지 않는다`);
        const entry = parsed.data as unknown as StoredUnit<T>;
        if (entry.result.kind === "success" && !schema.safeParse(entry.result.data).success) throw new ReconcileFileError(`${path}:${index + 1}: 저장된 답이 답의 스키마와 맞지 않는다 (${entry.unitId})`);
        this.entries.set(`${entry.unitId}\n${entry.requestKey}`, entry);
      });
  }

  get size(): number {
    return this.entries.size;
  }

  all(): StoredUnit<T>[] {
    return [...this.entries.values()];
  }

  find(unitId: string, request: CallRequest<unknown>): StoredUnit<T> | undefined {
    return this.entries.get(`${unitId}\n${requestKey(request)}`);
  }

  get(unitId: string, request: CallRequest<T>): LlmResult<T> | undefined {
    return this.find(unitId, request as CallRequest<unknown>)?.result;
  }

  put(unitId: string, request: CallRequest<T>, result: LlmResult<T>): void {
    if (!FINAL_KINDS.has(result.kind)) return;
    const entry: StoredUnit<T> = { unitId, requestKey: requestKey(request as CallRequest<unknown>), kind: result.kind, callId: result.meta.callId, costUsd: result.meta.totalCostUsd, result };
    this.entries.set(`${unitId}\n${entry.requestKey}`, entry);
    appendFileSync(this.path, `${JSON.stringify(entry)}\n`, "utf8");
  }
}

/**
 * 앞선 실행의 호출 기록에서 쓴 비용을 읽어 상한 계산에 채운다. 상한의 누적은 프로세스 메모리에 있어,
 * 이것을 하지 않으면 이어 하는 실행이 상한을 처음부터 다시 센다.
 */
export function seedBudgetFromLog(path: string, budget: BudgetGuard, tags: Tags): { lines: number; spentUsd: number } {
  if (!existsSync(path)) return { lines: 0, spentUsd: 0 };
  let lines = 0;
  let spentUsd = 0;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    if (line.trim() === "") continue;
    let record: { costUsd?: unknown };
    try {
      record = JSON.parse(line) as { costUsd?: unknown };
    } catch {
      throw new ReconcileFileError(`${path}:${lines + 1}: 호출 기록을 JSON 으로 읽을 수 없다. 비용을 모르는 채 이어 하지 않는다`);
    }
    lines += 1;
    if (typeof record.costUsd === "number") {
      budget.record(tags, record.costUsd);
      spentUsd += record.costUsd;
    }
  }
  return { lines, spentUsd };
}
