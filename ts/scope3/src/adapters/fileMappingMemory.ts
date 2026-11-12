import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { z } from "zod";
import { InMemoryMappingMemory, type MemoryEntry } from "../domain/memory.js";
import { SUPPORTED_CATEGORIES } from "../domain/types.js";

/** 기억 파일을 읽지 못했거나 형식이 다르다. 「기억에 없다」와 구분하려고 예외로 둔다. */
export class MemoryError extends Error {
  override name = "MemoryError";
}

const categorySchema = z.union(SUPPORTED_CATEGORIES.map((category) => z.literal(category)));

const entrySchema = z.strictObject({
  key: z.string().min(1),
  category: categorySchema,
  factorId: z.string().min(1),
  confirmedBy: z.string().min(1),
  confirmedAt: z.iso.datetime({ offset: true }),
  label: z.string().optional(),
});

const fileSchema = z.strictObject({
  version: z.literal(1),
  /** 테스트·예제용 가상 기억이면 true. */
  synthetic: z.boolean(),
  note: z.string().optional(),
  entries: z.array(entrySchema),
});

/** JSON 파일 하나에 두는 기억. 확정할 때마다 임시 파일에 쓰고 이름을 바꿔 통째로 교체한다. */
export class FileMappingMemory extends InMemoryMappingMemory {
  private constructor(
    readonly path: string,
    readonly synthetic: boolean,
    entries: readonly MemoryEntry[],
  ) {
    super(entries);
  }

  /** 파일이 없으면 예외다. 새로 시작하려면 `createIfMissing` 을 켠다. */
  static open(path: string, options: { createIfMissing?: boolean; synthetic?: boolean } = {}): FileMappingMemory {
    if (!existsSync(path)) {
      if (!options.createIfMissing) throw new MemoryError(`기억 파일이 없다: ${path}`);
      const created = new FileMappingMemory(path, options.synthetic ?? false, []);
      created.flush();
      return created;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(readFileSync(path, "utf8"));
    } catch {
      throw new MemoryError(`기억 파일이 JSON 이 아니다: ${path}`);
    }
    const result = fileSchema.safeParse(parsed);
    if (!result.success) {
      throw new MemoryError(`기억 파일 형식이 다르다: ${path}: ${result.error.issues.map((issue) => `${issue.path.join(".")} ${issue.message}`).join("; ")}`);
    }
    const keys = new Set<string>();
    for (const entry of result.data.entries) {
      if (keys.has(entry.key)) throw new MemoryError(`기억 파일에 같은 키가 두 번 있다: ${entry.key}`);
      keys.add(entry.key);
    }
    return new FileMappingMemory(path, result.data.synthetic, result.data.entries as MemoryEntry[]);
  }

  override confirm(entry: MemoryEntry): void {
    super.confirm(entry);
    this.flush();
  }

  private flush(): void {
    const body = { version: 1, synthetic: this.synthetic, entries: [...this.entries.values()] };
    const temp = `${this.path}.tmp-${process.pid}`;
    writeFileSync(temp, `${JSON.stringify(body, null, 2)}\n`);
    renameSync(temp, this.path);
  }
}
