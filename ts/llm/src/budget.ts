import { roundUsd } from "./cost.js";
import type { Tags } from "./types.js";

/** 상한 하나. `scope` 에 적은 꼬리표가 모두 맞는 호출의 누적 비용에 적용된다. */
export interface BudgetLimit {
  scope: Partial<Tags>;
  limitUsd: number;
}

export interface BudgetVerdict {
  allowed: boolean;
  /** 거부했을 때 걸린 상한과 그때의 누적 비용. */
  limit?: BudgetLimit;
  spentUsd?: number;
}

function matches(scope: Partial<Tags>, tags: Tags): boolean {
  return (scope.tenant === undefined || scope.tenant === tags.tenant) && (scope.module === undefined || scope.module === tags.module);
}

/** 꼬리표별 누적 비용과 상한. 누적이 상한에 닿았으면 다음 호출을 공급사에 보내기 전에 막는다. */
export class BudgetGuard {
  private readonly entries: { tags: Tags; costUsd: number }[] = [];

  constructor(private readonly limits: BudgetLimit[] = []) {}

  record(tags: Tags, costUsd: number): void {
    this.entries.push({ tags, costUsd });
  }

  spent(scope: Partial<Tags>): number {
    return roundUsd(this.entries.filter((entry) => matches(scope, entry.tags)).reduce((sum, entry) => sum + entry.costUsd, 0));
  }

  check(tags: Tags): BudgetVerdict {
    for (const limit of this.limits) {
      if (!matches(limit.scope, tags)) continue;
      const spentUsd = this.spent(limit.scope);
      if (spentUsd >= limit.limitUsd) return { allowed: false, limit, spentUsd };
    }
    return { allowed: true };
  }
}
