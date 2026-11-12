import type { SupportedCategory } from "./types.js";

/** 사람이 확정한 매핑 한 건: 묶음 키 → 카테고리와 계수 id. */
export interface MemoryEntry {
  key: string;
  category: SupportedCategory;
  factorId: string;
  confirmedBy: string;
  /** ISO 8601 일시. */
  confirmedAt: string;
  /** 사람이 알아볼 원래 품목명. 조회에는 쓰지 않는다. */
  label?: string | undefined;
}

/** 확정 매핑 기억. 저장 방식(파일, DB)은 이 인터페이스 뒤에 둔다. */
export interface MappingMemory {
  /** 없으면 undefined. 저장소를 읽지 못한 것은 예외다. */
  lookup(key: string): MemoryEntry | undefined;
  /** 같은 키가 있으면 덮어쓴다. */
  confirm(entry: MemoryEntry): void;
  readonly size: number;
}

export class InMemoryMappingMemory implements MappingMemory {
  protected readonly entries = new Map<string, MemoryEntry>();

  constructor(entries: readonly MemoryEntry[] = []) {
    for (const entry of entries) this.entries.set(entry.key, entry);
  }

  lookup(key: string): MemoryEntry | undefined {
    return this.entries.get(key);
  }

  confirm(entry: MemoryEntry): void {
    this.entries.set(entry.key, entry);
  }

  get size(): number {
    return this.entries.size;
  }
}
