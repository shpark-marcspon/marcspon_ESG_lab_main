import { z } from "zod";
import { labelText, type Tag } from "./charts.js";

// 그림 라벨 (밴드 W 의 (다)). 번호표로 값 글자를 덮은 쪽 그림을 모델에게 주고, 번호마다 계열·범주·단위·도표 제목을 글로 받는다.
// 값은 받지 않는다: 답 스키마에 숫자 필드가 없고(번호도 글자다), 답의 글에 덮은 숫자의 글자가 있으면 그 답을 버린다.

export const PROMPT_LV = "reconcile/label-chart-tags@1";

export const tagLabelsSchema = z.object({
  tags: z.array(z.object({ tag: z.string(), role: z.enum(["value", "tick", "other"]), title: z.string().nullable(), series: z.string().nullable(), category: z.string().nullable(), unit: z.string().nullable() })),
});
export type TagLabelsAnswer = z.infer<typeof tagLabelsSchema>;

/** 번호표 하나에 대한 받아들인 답. */
export interface TagAnswer {
  role: "value" | "tick" | "other";
  title: string | null;
  series: string | null;
  category: string | null;
  unit: string | null;
}

export interface TagJudgement {
  /** 값 후보 ID → 받아들인 답. 번호 하나를 같이 쓰는 값 후보(자리가 같은 것)는 같은 답을 받는다. */
  answers: Record<string, TagAnswer>;
  rejected: Record<string, number>;
  /** 요청했는데 답이 없거나 버려진 번호. */
  unanswered: number[];
}

const NUMBER = /\d[\d,]*(?:\.\d+)?/g;
const core = (text: string): string[] => [...text.matchAll(NUMBER)].map((match) => match[0].replace(/,/g, "").replace(/\.$/, ""));
const digits = (token: string): number => token.replace(/\D/g, "").length;
const clean = (text: string | null): string | null => (text === null || text.trim() === "" ? null : text.trim());

/** 버리는 기준의 자릿수: 다른 번호표가 덮은 숫자는 숫자 글자가 이 수 이상일 때만 견준다. 한두 자리 숫자는 이름(`Scope 1`, `10년`)과 우연히 겹쳐 새는 것을 가리지 못한다. */
export const LEAK_MIN_DIGITS = 3;

/**
 * 한 쪽의 답을 확인한다. 버리는 것: 요청에 없는 번호(`unknown_tag`), 같은 번호의 둘째 답(`duplicate_tag`),
 * 답의 글에 값의 글자가 든 것(`value_text_in_answer`): 그 번호표가 덮은 숫자와 같은 숫자 덩어리, 또는 그 쪽의 다른 번호표가 덮은 숫자(숫자 글자 셋 이상)와 같은 숫자 덩어리.
 * `rawOf` 는 값 후보 ID → 원문 글자 (원본 문서에서). 여기서 값을 읽어 쓰는 곳은 이 확인뿐이다.
 */
export function judgeTagAnswers(answer: TagLabelsAnswer | null, pageTags: Tag[], rawOf: (id: string) => string): TagJudgement {
  const out: TagJudgement = { answers: {}, rejected: {}, unanswered: [] };
  const bump = (name: string): void => void (out.rejected[name] = (out.rejected[name] ?? 0) + 1);
  const byNumber = new Map(pageTags.map((tag) => [String(tag.n), tag]));
  const covered = new Map(pageTags.map((tag) => [tag.n, new Set((tag.ids ?? [tag.id]).flatMap((id) => core(rawOf(id))))]));
  const longCovered = new Set([...covered.values()].flatMap((set) => [...set]).filter((token) => digits(token) >= LEAK_MIN_DIGITS));
  const done = new Set<number>();
  for (const item of answer?.tags ?? []) {
    const tag = byNumber.get(item.tag.trim());
    if (!tag) {
      bump("unknown_tag");
      continue;
    }
    if (done.has(tag.n)) {
      bump("duplicate_tag");
      continue;
    }
    const own = covered.get(tag.n) as Set<string>;
    const written = [item.title, item.series, item.category, item.unit].flatMap((text) => (text === null ? [] : core(text)));
    if (written.some((token) => own.has(token) || longCovered.has(token))) {
      bump("value_text_in_answer");
      continue;
    }
    done.add(tag.n);
    const accepted: TagAnswer = item.role === "value" ? { role: "value", title: clean(item.title), series: clean(item.series), category: clean(item.category), unit: clean(item.unit) } : { role: item.role, title: null, series: null, category: null, unit: null };
    for (const id of tag.ids ?? [tag.id]) out.answers[id] = accepted;
  }
  out.unanswered = pageTags.filter((tag) => !done.has(tag.n)).map((tag) => tag.n);
  return out;
}

/** 받아들인 답에서 요청 글에 실을 라벨 (값 후보 ID → `도표 제목 · 계열 · 범주`). 값이라고 답했고 범주가 있는 것만. */
export function visionLabels(answers: Record<string, TagAnswer>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, answer] of Object.entries(answers)) {
    if (answer.role !== "value") continue;
    const text = labelText(answer);
    if (text !== null) out[id] = text;
  }
  return out;
}

const squash = (text: string | null): string => (text ?? "").normalize("NFKC").replace(/\s+/g, "");
/** 두 답이 같은가를 가르는 열쇠: 갈래, 계열, 범주 (제목과 단위는 보지 않는다. 표기가 실행마다 달라도 같은 자리를 가리킨다). */
export const answerKey = (answer: TagAnswer | undefined): string => (answer === undefined ? "∅" : answer.role !== "value" ? answer.role : `value|${squash(answer.series)}|${squash(answer.category)}`);

export interface MergedVision {
  answers: Record<string, TagAnswer>;
  /** 값 후보별: 몇 실행이 같은 답(갈래·계열·범주)을 냈는가. */
  agreement: { all: number; majority: number; none: number; ids: number };
}

/**
 * 여러 실행의 그림 라벨을 하나로 합친다 (D4v 에 싣는 것). 값 후보마다, 실행의 과반(셋 중 둘 이상)이 같은 (갈래, 계열, 범주)를 냈을 때만 그 답을 쓴다.
 * 제목과 단위는 그렇게 답한 실행 가운데 가장 앞선 것의 글자를 쓴다. 과반이 없으면 답 없음(라벨을 싣지 않는다). 틀린 라벨은 없는 라벨보다 나쁘다.
 */
export function mergeVision(runs: Record<string, TagAnswer>[]): MergedVision {
  const ids = [...new Set(runs.flatMap((run) => Object.keys(run)))].sort();
  const out: MergedVision = { answers: {}, agreement: { all: 0, majority: 0, none: 0, ids: ids.length } };
  for (const id of ids) {
    const votes = new Map<string, TagAnswer[]>();
    for (const run of runs) {
      const answer = run[id];
      if (answer === undefined) continue;
      votes.set(answerKey(answer), [...(votes.get(answerKey(answer)) ?? []), answer]);
    }
    const best = [...votes.values()].sort((x, y) => y.length - x.length)[0] ?? [];
    if (best.length * 2 > runs.length) {
      out.answers[id] = best[0] as TagAnswer;
      if (best.length === runs.length) out.agreement.all += 1;
      else out.agreement.majority += 1;
    } else out.agreement.none += 1;
  }
  return out;
}

/** PNG 의 가로·세로 (IHDR). 그림 토큰의 어림에만 쓴다. */
export function pngSize(bytes: Uint8Array): { width: number; height: number } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (bytes.length < 24 || view.getUint32(0) !== 0x89504e47) throw new Error("PNG 가 아니다");
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

/** 그림 한 장의 입력 토큰 어림: 32px 조각 수 × 곱수. 곱수는 공급사 문서의 값이거나 연결 확인에서 잰 값이다 (호출하는 쪽이 준다). */
export const imageTokens = (size: { width: number; height: number }, multiplier: number): number => Math.ceil(Math.ceil(size.width / 32) * Math.ceil(size.height / 32) * multiplier);
