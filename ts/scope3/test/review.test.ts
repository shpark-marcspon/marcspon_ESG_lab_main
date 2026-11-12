import { describe, expect, it } from "vitest";
import {
  TRANSITIONS,
  accept,
  applyAction,
  confirmedMapping,
  edit,
  finalize,
  propose,
  receiveProposal,
  reject,
  requestRerecommend,
  toMemoryEntry,
  type AiProposal,
  type HumanEdit,
  type ReviewAction,
  type ReviewState,
} from "../src/index.js";

const PROPOSAL: AiProposal = {
  category: 1,
  picks: [
    { factorId: "syn:steel-kg", reason: "테스트 (synthetic)", confidence: "high" },
    { factorId: "syn:steel-t", reason: "테스트 (synthetic)", confidence: "low" },
  ],
  proposedAt: "2026-10-01T10:00:00+09:00",
};
const WHO = { by: "reviewer-synthetic", at: "2026-10-01T11:00:00+09:00" };
const EDIT: HumanEdit = { category: 2, factorId: "syn:spend-computer", editedBy: "reviewer-synthetic", editedAt: "2026-10-01T11:30:00+09:00", note: "자본재로 본다" };
const start = () => propose("r1", "합성강판|kg|", PROPOSAL);

describe("검토 항목 상태 전이", () => {
  it("AI 제안 → 반영 → 최종 확정", () => {
    const done = finalize(accept(start(), WHO), WHO);
    expect(done.state).toBe("finalized");
    expect(done.outcome).toEqual({ kind: "accepted_ai" });
    expect(confirmedMapping(done)).toEqual({ category: 1, factorId: "syn:steel-kg", origin: "ai" });
  });

  it("수정하면 사람의 값과 AI 원본 제안이 둘 다 남는다", () => {
    const done = finalize(edit(start(), EDIT), WHO);
    expect(done.aiProposal).toEqual(PROPOSAL);
    expect(done.outcome).toEqual({ kind: "edited", humanEdit: EDIT });
    expect(confirmedMapping(done)).toEqual({ category: 2, factorId: "syn:spend-computer", origin: "human" });
    expect(toMemoryEntry(done, "합성 강판")).toEqual({ key: "합성강판|kg|", category: 2, factorId: "syn:spend-computer", confirmedBy: WHO.by, confirmedAt: WHO.at, label: "합성 강판" });
  });

  it("미반영으로 확정하면 배출량에 쓰는 매핑이 없고 기억하지 않는다", () => {
    const done = finalize(reject(start(), WHO, "중복 행"), WHO);
    expect(done.outcome).toEqual({ kind: "rejected", reason: "중복 행" });
    expect(confirmedMapping(done)).toBeNull();
    expect(toMemoryEntry(done)).toBeNull();
  });

  it("재추천을 받으면 이전 AI 제안은 이력으로 간다", () => {
    const second: AiProposal = { ...PROPOSAL, picks: [{ factorId: "syn:pp-kg", reason: "테스트 (synthetic)", confidence: "medium" }], proposedAt: "2026-10-01T12:00:00+09:00" };
    const again = receiveProposal(requestRerecommend(start(), WHO, "다른 재질"), second);
    expect(again.state).toBe("ai_proposed");
    expect(again.aiProposal).toEqual(second);
    expect(again.proposalHistory).toEqual([PROPOSAL]);
  });

  it("허용되지 않는 전이는 타입에서 막힌다", () => {
    const proposed = start();
    const done = finalize(accept(proposed, WHO), WHO);
    const waiting = requestRerecommend(proposed, WHO);
    // 아래 호출은 컴파일되지 않아야 한다. `pnpm typecheck` 가 @ts-expect-error 로 이를 지킨다.
    // @ts-expect-error AI 제안 상태는 반영·미반영·수정을 거치지 않고 확정할 수 없다
    expect(() => finalize(proposed, WHO)).not.toThrow();
    // @ts-expect-error 확정된 항목은 다시 반영할 수 없다
    expect(() => accept(done, WHO)).not.toThrow();
    // @ts-expect-error 확정된 항목은 수정할 수 없다
    expect(() => edit(done, EDIT)).not.toThrow();
    // @ts-expect-error 재추천을 기다리는 항목은 반영할 수 없다
    expect(() => accept(waiting, WHO)).not.toThrow();
    // @ts-expect-error AI 제안 상태의 항목에 재추천 결과를 덮어쓸 수 없다
    expect(() => receiveProposal(proposed, PROPOSAL)).not.toThrow();
    // @ts-expect-error 확정 전 항목에서는 확정 매핑을 꺼낼 수 없다
    expect(() => confirmedMapping(proposed)).toThrow();
  });

  it("런타임 동작도 같은 표를 따른다: 허용된 전이만 통과한다", () => {
    const actions: Record<ReviewState, ReviewAction> = {
      accepted: { type: "accept", ...WHO },
      rejected: { type: "reject", ...WHO },
      edited: { type: "edit", humanEdit: EDIT },
      rerecommend_requested: { type: "request_rerecommend", ...WHO },
      ai_proposed: { type: "receive_proposal", proposal: PROPOSAL },
      finalized: { type: "finalize", ...WHO },
    };
    const samples = {
      ai_proposed: start(),
      accepted: accept(start(), WHO),
      rejected: reject(start(), WHO),
      edited: edit(start(), EDIT),
      rerecommend_requested: requestRerecommend(start(), WHO),
      finalized: finalize(accept(start(), WHO), WHO),
    };
    for (const from of Object.keys(samples) as ReviewState[]) {
      for (const to of Object.keys(actions) as ReviewState[]) {
        const result = applyAction(samples[from], actions[to]);
        const allowed = (TRANSITIONS[from] as readonly ReviewState[]).includes(to);
        expect(result.ok, `${from} → ${to}`).toBe(allowed);
        if (result.ok) expect(result.item.state).toBe(to);
      }
    }
    expect(TRANSITIONS.finalized).toEqual([]);
    expect(applyAction(samples.finalized, actions.edited)).toEqual({ ok: false, error: "finalized 에서 edited 로 갈 수 없다" });
  });
});
