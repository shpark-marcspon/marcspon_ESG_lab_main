import type { FactBucket } from "../domain/facts.js";
import { VALUE_KINDS, isNonValue, type ValueKind } from "../domain/kinds.js";
import { TallyError } from "./hybrid.js";
import type { KindGoldItem } from "./kind-exam.js";

// 밴드 V (MARC-TICK-25): 종류 규칙의 채점과, 저장된 답을 종류로 다시 가르는 셈. 모델을 부르지 않는다. 수는 전부 분모와 같이 낸다.

export interface KindCell {
  /** 정답이 그 종류인 자리 수. */
  gold: number;
  /** 규칙이 그 종류로 낸 자리 수. */
  predicted: number;
  /** 둘이 같은 자리 수 (정밀도 = hit/predicted, 재현율 = hit/gold). */
  hit: number;
}

export interface KindWrong {
  doc: "a" | "b";
  id: string;
  bucket: FactBucket;
  raw: string;
  gold: ValueKind;
  ambiguousWith: ValueKind | null;
  predicted: ValueKind;
}

export interface KindScore {
  total: number;
  /** 규칙의 종류가 정답과 같은 자리. */
  correct: number;
  /** 정답이 애매하다고 단 자리에서는 다른 쪽 종류도 맞다고 볼 때. */
  correctLenient: number;
  ambiguous: number;
  /** 갈래 → 종류 → 수. 갈래 `all` 은 전체. */
  byBucket: Record<string, Record<ValueKind, KindCell>>;
  /** 「값이 아님」(value 가 아닌 종류)으로 가르는 둘 갈래의 성적. 갈래 → 수. */
  nonValue: Record<string, KindCell & { wronglyNonValue: number; wronglyNonValueAmbiguous: number }>;
  wrong: KindWrong[];
}

const emptyCells = (): Record<ValueKind, KindCell> => Object.fromEntries(VALUE_KINDS.map((kind) => [kind, { gold: 0, predicted: 0, hit: 0 }])) as Record<ValueKind, KindCell>;

/** 종류 규칙을 정답과 견준다. 정답에 있는데 규칙의 결과가 없는 자리는 예외다 (대상이 문서와 어긋났다는 뜻이다). */
export function scoreKinds(gold: KindGoldItem[], predictedOf: (doc: "a" | "b", id: string) => ValueKind | undefined): KindScore {
  const out: KindScore = { total: gold.length, correct: 0, correctLenient: 0, ambiguous: 0, byBucket: { all: emptyCells() }, nonValue: { all: { gold: 0, predicted: 0, hit: 0, wronglyNonValue: 0, wronglyNonValueAmbiguous: 0 } }, wrong: [] };
  for (const item of gold) {
    const predicted = predictedOf(item.doc, item.id);
    if (predicted === undefined) throw new TallyError(`${item.doc}:${item.id}: 규칙의 결과에 없는 자리다`);
    out.byBucket[item.bucket] ??= emptyCells();
    out.nonValue[item.bucket] ??= { gold: 0, predicted: 0, hit: 0, wronglyNonValue: 0, wronglyNonValueAmbiguous: 0 };
    if (item.ambiguousWith !== null) out.ambiguous += 1;
    for (const bucket of ["all", item.bucket]) {
      const cells = out.byBucket[bucket] as Record<ValueKind, KindCell>;
      cells[item.kind].gold += 1;
      cells[predicted].predicted += 1;
      if (predicted === item.kind) cells[item.kind].hit += 1;
      const binary = out.nonValue[bucket] as KindScore["nonValue"][string];
      if (isNonValue(item.kind)) binary.gold += 1;
      if (isNonValue(predicted)) binary.predicted += 1;
      if (isNonValue(item.kind) && isNonValue(predicted)) binary.hit += 1;
      if (!isNonValue(item.kind) && isNonValue(predicted)) {
        binary.wronglyNonValue += 1;
        if (item.ambiguousWith !== null) binary.wronglyNonValueAmbiguous += 1;
      }
    }
    if (predicted === item.kind) out.correct += 1;
    else out.wrong.push({ doc: item.doc, id: item.id, bucket: item.bucket, raw: item.raw, gold: item.kind, ambiguousWith: item.ambiguousWith, predicted });
    if (predicted === item.kind || predicted === item.ambiguousWith) out.correctLenient += 1;
  }
  return out;
}

export interface Reclass {
  total: number;
  /** 두 자리 다 값. */
  bothValue: number;
  /** 한 자리만 「값이 아님」. */
  oneNonValue: number;
  /** 두 자리 다 「값이 아님」. */
  bothNonValue: number;
  /** 「값이 아님」이 낀 것 (= oneNonValue + bothNonValue): 걸러지는 것. */
  filtered: number;
}

/**
 * 짝(또는 묶음 안의 두 자리)을 종류로 다시 가른다. **한 자리라도 「값이 아님」이면 그 짝은 걸러진다**(검증할 값끼리의 짝이 아니다).
 * 한 자리만 「값이 아님」인 것은 따로 센다: 종류 규칙이 한쪽을 틀렸을 수 있는 자리다. 합이 대상 수와 다르면 예외다.
 */
export function reclassify(pairs: { x: ValueKind; y: ValueKind }[]): Reclass {
  const out: Reclass = { total: pairs.length, bothValue: 0, oneNonValue: 0, bothNonValue: 0, filtered: 0 };
  for (const pair of pairs) {
    const non = [pair.x, pair.y].filter(isNonValue).length;
    if (non === 0) out.bothValue += 1;
    else if (non === 1) out.oneNonValue += 1;
    else out.bothNonValue += 1;
  }
  out.filtered = out.oneNonValue + out.bothNonValue;
  if (out.bothValue + out.filtered !== out.total) throw new TallyError(`짝 ${out.total}개 중 갈래가 붙은 것이 ${out.bothValue + out.filtered}개다. 합이 맞지 않아 수를 내지 않는다`);
  return out;
}

/** 자리의 목록 가운데 규칙이 「값이 아님」으로 가른 것. 정답 쌍·묶음의 자리에 쓰면 「잘못 걸러지는 것」이다. */
export function nonValuePlaces<T extends { doc: "a" | "b"; id: string }>(places: T[], kindOf: (doc: "a" | "b", id: string) => ValueKind | undefined): (T & { kind: ValueKind })[] {
  const out: (T & { kind: ValueKind })[] = [];
  for (const place of places) {
    const kind = kindOf(place.doc, place.id);
    if (kind === undefined) throw new TallyError(`${place.doc}:${place.id}: 규칙의 결과에 없는 자리다`);
    if (isNonValue(kind)) out.push({ ...place, kind });
  }
  return out;
}

const pct = (hit: number, of: number): string => (of === 0 ? `${hit}/0` : `${hit}/${of} (${((hit / of) * 100).toFixed(1)}%)`);
const row = (cells: string[]): string => `| ${cells.join(" | ")} |`;
export const KIND_KO: Record<ValueKind, string> = { value: "값", identifier: "식별자", duration: "기간", count: "개수", year_label: "연도 표시", scale: "눈금" };
const BUCKET_KO: Record<string, string> = { all: "전체", sentence: "문장 속", number_only_block: "숫자뿐인 블록", table_text_cell: "표의 글 칸 속 (표본)", table_cell: "표의 숫자 칸 (표본)" };

export function renderKindScore(score: KindScore): string {
  const lines: string[] = ["## 종류 규칙의 성적 (정답 대비)", ""];
  lines.push(`정답 ${score.total}자리 중 규칙이 같은 종류를 낸 것 ${pct(score.correct, score.total)}. 정답이 애매하다고 단 ${score.ambiguous}자리에서 다른 쪽 종류도 맞다고 보면 ${pct(score.correctLenient, score.total)}.`, "");
  for (const bucket of ["all", "sentence", "number_only_block", "table_text_cell", "table_cell"]) {
    const cells = score.byBucket[bucket];
    if (!cells) continue;
    lines.push(`### ${BUCKET_KO[bucket]}`, "", row(["종류", "정답 수", "규칙이 낸 수", "정밀도 (맞은 것 / 규칙이 낸 것)", "재현율 (맞은 것 / 정답)"]), "|---|---:|---:|---:|---:|");
    for (const kind of VALUE_KINDS) lines.push(row([KIND_KO[kind], `${cells[kind].gold}`, `${cells[kind].predicted}`, pct(cells[kind].hit, cells[kind].predicted), pct(cells[kind].hit, cells[kind].gold)]));
    const binary = score.nonValue[bucket] as KindScore["nonValue"][string];
    lines.push(row(["「값이 아님」 (값 밖의 종류 전부)", `${binary.gold}`, `${binary.predicted}`, pct(binary.hit, binary.predicted), pct(binary.hit, binary.gold)]), "");
    lines.push(`정답이 「값」인데 규칙이 「값이 아님」으로 가른 것: ${binary.wronglyNonValue}자리 (그중 정답이 애매하다고 단 것 ${binary.wronglyNonValueAmbiguous}).`, "");
  }
  lines.push("### 틀린 것의 목록", "", row(["문서", "자리", "갈래", "원문 글자", "정답", "애매(다른 쪽)", "규칙"]), "|---|---|---|---|---|---|---|");
  for (const item of score.wrong) lines.push(row([item.doc, `\`${item.id}\``, BUCKET_KO[item.bucket] ?? item.bucket, item.raw, KIND_KO[item.gold], item.ambiguousWith ? KIND_KO[item.ambiguousWith] : "-", KIND_KO[item.predicted]]));
  return `${lines.join("\n")}\n`;
}
