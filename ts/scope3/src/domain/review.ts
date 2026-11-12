import type { MemoryEntry } from "./memory.js";
import type { FactorPick, SupportedCategory } from "./types.js";

// 검토 항목 모델 (PRD GHG.14~17). 행마다 AI 제안을 사람이 반영·미반영·수정·재추천 요청한 뒤 최종 확정한다.
// AI 원본 제안(`aiProposal`, `proposalHistory`)과 사람의 수정값(`humanEdit`)은 다른 필드에 두고 서로 덮어쓰지 않는다.

export type ReviewState = "ai_proposed" | "accepted" | "rejected" | "edited" | "rerecommend_requested" | "finalized";

/** AI 가 낸 제안 원본. 만든 뒤 바꾸지 않는다. */
export interface AiProposal {
  readonly category: SupportedCategory;
  readonly picks: readonly FactorPick[];
  readonly proposedAt: string;
}

/** 사람이 고친 값. */
export interface HumanEdit {
  readonly category: SupportedCategory;
  readonly factorId: string;
  readonly editedBy: string;
  readonly editedAt: string;
  readonly note: string | null;
}

interface Actor {
  by: string;
  at: string;
}

interface Base {
  readonly rowId: string;
  readonly groupKey: string;
  /** 지금 검토 대상인 AI 제안. */
  readonly aiProposal: AiProposal;
  /** 재추천으로 밀려난 이전 AI 제안들 (오래된 것부터). */
  readonly proposalHistory: readonly AiProposal[];
}

type Final =
  | { readonly kind: "accepted_ai" }
  | { readonly kind: "edited"; readonly humanEdit: HumanEdit }
  /** 미반영으로 확정. 배출량에 넣지 않는다. */
  | { readonly kind: "rejected"; readonly reason: string | null };

interface StateFields {
  ai_proposed: object;
  accepted: { readonly decided: Actor };
  rejected: { readonly decided: Actor; readonly reason: string | null };
  edited: { readonly humanEdit: HumanEdit };
  rerecommend_requested: { readonly requested: Actor; readonly note: string | null };
  finalized: { readonly finalized: Actor; readonly outcome: Final };
}

export type ReviewItem<S extends ReviewState = ReviewState> = { [K in S]: Base & { readonly state: K } & StateFields[K] }[S];

/** 허용되는 전이. 타입(아래 함수들의 인자)과 런타임 검사(`applyAction`)가 이 표 하나를 따른다. */
export const TRANSITIONS = {
  ai_proposed: ["accepted", "rejected", "edited", "rerecommend_requested"],
  accepted: ["rejected", "edited", "rerecommend_requested", "finalized"],
  rejected: ["accepted", "edited", "rerecommend_requested", "finalized"],
  edited: ["accepted", "rejected", "edited", "rerecommend_requested", "finalized"],
  rerecommend_requested: ["ai_proposed"],
  finalized: [],
} as const satisfies Record<ReviewState, readonly ReviewState[]>;

/** 상태 `T` 로 갈 수 있는 출발 상태들. */
type From<T extends ReviewState> = { [S in ReviewState]: T extends (typeof TRANSITIONS)[S][number] ? S : never }[ReviewState];

function base(item: ReviewItem): Base {
  return { rowId: item.rowId, groupKey: item.groupKey, aiProposal: item.aiProposal, proposalHistory: item.proposalHistory };
}

export function propose(rowId: string, groupKey: string, proposal: AiProposal): ReviewItem<"ai_proposed"> {
  return { rowId, groupKey, aiProposal: proposal, proposalHistory: [], state: "ai_proposed" };
}

/** 반영: AI 제안의 대표 결과를 그대로 받는다. */
export function accept(item: ReviewItem<From<"accepted">>, decided: Actor): ReviewItem<"accepted"> {
  return { ...base(item), state: "accepted", decided };
}

/** 미반영. */
export function reject(item: ReviewItem<From<"rejected">>, decided: Actor, reason: string | null = null): ReviewItem<"rejected"> {
  return { ...base(item), state: "rejected", decided, reason };
}

/** 수정: 사람의 값을 따로 적는다. AI 제안은 그대로 남는다. */
export function edit(item: ReviewItem<From<"edited">>, humanEdit: HumanEdit): ReviewItem<"edited"> {
  return { ...base(item), state: "edited", humanEdit };
}

export function requestRerecommend(item: ReviewItem<From<"rerecommend_requested">>, requested: Actor, note: string | null = null): ReviewItem<"rerecommend_requested"> {
  return { ...base(item), state: "rerecommend_requested", requested, note };
}

/** 재추천 결과를 받는다. 이전 제안은 이력으로 옮긴다. */
export function receiveProposal(item: ReviewItem<From<"ai_proposed">>, proposal: AiProposal): ReviewItem<"ai_proposed"> {
  return { rowId: item.rowId, groupKey: item.groupKey, aiProposal: proposal, proposalHistory: [...item.proposalHistory, item.aiProposal], state: "ai_proposed" };
}

/** 최종 확정. 반영·미반영·수정 중 하나를 거친 항목만 확정할 수 있다. */
export function finalize(item: ReviewItem<From<"finalized">>, finalized: Actor): ReviewItem<"finalized"> {
  const outcome: Final =
    item.state === "accepted"
      ? { kind: "accepted_ai" }
      : item.state === "edited"
        ? { kind: "edited", humanEdit: item.humanEdit }
        : { kind: "rejected", reason: item.reason };
  return { ...base(item), state: "finalized", finalized, outcome };
}

export type ReviewAction =
  | { type: "accept"; by: string; at: string }
  | { type: "reject"; by: string; at: string; reason?: string }
  | { type: "edit"; humanEdit: HumanEdit }
  | { type: "request_rerecommend"; by: string; at: string; note?: string }
  | { type: "receive_proposal"; proposal: AiProposal }
  | { type: "finalize"; by: string; at: string };

const TARGET: Record<ReviewAction["type"], ReviewState> = {
  accept: "accepted",
  reject: "rejected",
  edit: "edited",
  request_rerecommend: "rerecommend_requested",
  receive_proposal: "ai_proposed",
  finalize: "finalized",
};

export type ActionResult = { ok: true; item: ReviewItem } | { ok: false; error: string };

/** 타입을 모르는 입력(파일, 화면)에서 온 동작을 적용한다. 허용되지 않는 전이는 예외가 아니라 `ok: false` 다. */
export function applyAction(item: ReviewItem, action: ReviewAction): ActionResult {
  const target = TARGET[action.type];
  if (!(TRANSITIONS[item.state] as readonly ReviewState[]).includes(target)) {
    return { ok: false, error: `${item.state} 에서 ${target} 로 갈 수 없다` };
  }
  // 위에서 전이를 확인했으므로 아래의 단언은 표와 어긋나지 않는다.
  switch (action.type) {
    case "accept":
      return { ok: true, item: accept(item as ReviewItem<From<"accepted">>, { by: action.by, at: action.at }) };
    case "reject":
      return { ok: true, item: reject(item as ReviewItem<From<"rejected">>, { by: action.by, at: action.at }, action.reason ?? null) };
    case "edit":
      return { ok: true, item: edit(item as ReviewItem<From<"edited">>, action.humanEdit) };
    case "request_rerecommend":
      return { ok: true, item: requestRerecommend(item as ReviewItem<From<"rerecommend_requested">>, { by: action.by, at: action.at }, action.note ?? null) };
    case "receive_proposal":
      return { ok: true, item: receiveProposal(item as ReviewItem<From<"ai_proposed">>, action.proposal) };
    case "finalize":
      return { ok: true, item: finalize(item as ReviewItem<From<"finalized">>, { by: action.by, at: action.at }) };
  }
}

/** 확정된 항목이 배출량에 쓰는 매핑. 미반영으로 확정했으면 null. 확정 전 항목은 받지 않는다. */
export function confirmedMapping(item: ReviewItem<"finalized">): { category: SupportedCategory; factorId: string; origin: "ai" | "human" } | null {
  if (item.outcome.kind === "rejected") return null;
  if (item.outcome.kind === "edited") return { category: item.outcome.humanEdit.category, factorId: item.outcome.humanEdit.factorId, origin: "human" };
  const top = item.aiProposal.picks[0];
  return top ? { category: item.aiProposal.category, factorId: top.factorId, origin: "ai" } : null;
}

/** 확정된 항목을 확정 매핑 기억에 넣을 항목으로 바꾼다. 미반영 확정은 기억하지 않는다 (null). */
export function toMemoryEntry(item: ReviewItem<"finalized">, label?: string): MemoryEntry | null {
  const mapping = confirmedMapping(item);
  if (!mapping) return null;
  return { key: item.groupKey, category: mapping.category, factorId: mapping.factorId, confirmedBy: item.finalized.by, confirmedAt: item.finalized.at, label };
}
