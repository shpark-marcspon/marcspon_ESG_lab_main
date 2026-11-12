import { readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";
import { dataDir } from "./paths.js";

const taskSchema = z.object({
  chunkSourceTokenBudget: z.number().int().positive(),
  chunkSourceTokenBudgetBasis: z.string().min(1),
  expectedOutputRatio: z.number().positive(),
  expectedOutputRatioBasis: z.string().min(1),
});

const translateSchema = taskSchema.extend({
  /** 표 셀의 번역문 숫자가 원문과 다를 때. `flag` = 의심 표시만 남긴다, `strict` = 조각 실패(옛 규칙). */
  tableNumbers: z.enum(["flag", "strict"]),
  /** 단위만 있는 표 셀. `fixed` = 모델에 보내지 않고 고정 번역 표로 옮긴다, `model` = 모델에 보낸다(옛 동작). */
  unitCells: z.enum(["fixed", "model"]),
  tableCellBasis: z.string().min(1),
});

const settingsSchema = z.object({
  note: z.string(),
  checkedOn: z.string(),
  translate: translateSchema,
  greenwash: taskSchema,
  runner: z.object({ concurrency: z.number().int().positive(), maxAttempts: z.number().int().positive(), basis: z.string() }),
  furnitureMinPageShare: z.number().positive().max(1),
  furnitureMinPages: z.number().int().positive(),
  furnitureBasis: z.string(),
  dryRunRoutes: z.array(z.string()).min(1),
});

export type Settings = z.infer<typeof settingsSchema>;
export type TaskSettings = z.infer<typeof taskSchema>;

export class SettingsError extends Error {
  override name = "SettingsError";
}

export const defaultSettingsPath = join(dataDir, "settings.json");

/** 조각 예산, 동시 실행 수 같은 설계 매개변수는 코드가 아니라 `data/docpipe/settings.json` 에 근거와 함께 있다. */
export function loadSettings(path: string = defaultSettingsPath): Settings {
  const parsed = settingsSchema.safeParse(JSON.parse(readFileSync(path, "utf8")));
  if (!parsed.success) throw new SettingsError(`${path}: ${z.prettifyError(parsed.error)}`);
  return parsed.data;
}
