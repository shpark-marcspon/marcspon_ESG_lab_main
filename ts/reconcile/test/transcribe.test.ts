import { describe, expect, it } from "vitest";
import { compareTranscription, inLabelScope, notationKey, planUnits, transcribeAnswerSchema, transcribeRequest, transcriptionRow, type LabelUnit } from "../src/index.js";
import { TAGS, extract, syntheticDocument } from "./helpers.js";

// 표와 숫자, 모델의 답은 전부 지어낸 것이다 (synthetic).

function unit(): LabelUnit {
  const extraction = extract(
    syntheticDocument("report", [
      {
        id: "p1-t1",
        cells: [
          ["구분", "단위", "2023", "2024"],
          ["직접 온실가스(Scope 1)", "tCO2eq", "1,200", "1,350"],
          ["총 에너지 사용량", "TJ", "45.30", "-47.1"],
          ["재생에너지 비율", "%", "12.5%", "9,876.5"],
        ],
      },
    ]),
    "a",
  );
  const found = planUnits(extraction, extraction.facts.filter((fact) => inLabelScope(fact, { includeSentences: false })), { maxTargetsPerCall: 40, includeSentences: false })[0];
  if (!found) throw new Error("요청 단위가 없다");
  return found;
}

const SOURCE: Record<string, string> = { r1c2: "1,200", r1c3: "1,350", r2c2: "45.30", r2c3: "-47.1", r3c2: "12.5%", r3c3: "9,876.5" };
const answer = (patch: Record<string, string | null> = {}, extra: { target: string; text: string }[] = []) => ({ cells: [...Object.entries({ ...SOURCE, ...patch }).flatMap(([target, text]) => (text === null ? [] : [{ target, text }])), ...extra] });
const verdicts = (patch: Record<string, string | null>, extra: { target: string; text: string }[] = []) => Object.fromEntries(compareTranscription(unit(), answer(patch, extra)).cells.map((cell) => [cell.target, cell.verdict]));

describe("옮겨 쓰기 비교", () => {
  it("글자가 같으면 같음이다. 앞뒤 공백은 보지 않는다", () => {
    const compared = compareTranscription(unit(), answer({ r1c2: " 1,200 " }));
    expect(compared.cells.map((cell) => cell.verdict)).toEqual(["same", "same", "same", "same", "same", "same"]);
    expect(compared.fabricated).toEqual([]);
  });

  it("쉼표·공백만 다르면 「표기만 다름」이고 「다름」으로 세지 않는다", () => {
    const compared = compareTranscription(unit(), answer({ r1c2: "1200", r1c3: "1, 350", r3c3: "9876.5" }));
    expect(compared.cells.filter((cell) => cell.verdict === "notation_only").map((cell) => cell.target)).toEqual(["r1c2", "r1c3", "r3c3"]);
    expect(transcriptionRow("표", compared.cells, 0)).toMatchObject({ cells: 6, same: 3, notationOnly: 3, different: 0, charsChanged: 0 });
    expect(notationKey("１，２００")).toBe("1200");
  });

  it("숫자·소수점·부호·% 가 달라지면 「글자 바뀜」이다: 반올림, 끝의 0, 부호, 자릿수, 기호", () => {
    expect(verdicts({ r1c2: "1,201", r2c2: "45.3", r2c3: "47.1", r3c2: "12.5", r1c3: "13,500" })).toEqual({ r1c2: "chars_changed", r1c3: "chars_changed", r2c2: "chars_changed", r2c3: "chars_changed", r3c2: "chars_changed", r3c3: "same" });
  });

  it("같은 요청의 다른 칸의 글자를 적으면 「자리 바뀜」이다", () => {
    const compared = compareTranscription(unit(), answer({ r1c2: "1,350", r1c3: "1,200" }));
    expect(compared.cells.slice(0, 2)).toMatchObject([{ target: "r1c2", verdict: "position_swapped", swappedWith: "r1c3" }, { target: "r1c3", verdict: "position_swapped", swappedWith: "r1c2" }]);
    expect(transcriptionRow("표", compared.cells, 0)).toMatchObject({ different: 2, positionSwapped: 2, charsChanged: 0 });
  });

  it("빠뜨린 칸, 서로 다른 답이 둘 온 칸, 대상이 아닌 칸을 적은 답을 가른다", () => {
    const compared = compareTranscription(unit(), answer({ r2c2: null }, [{ target: "r1c2", text: "1,299" }, { target: "r1c3", text: "1,350" }, { target: "r9c9", text: "77" }, { target: "r0c2", text: "2023" }]));
    expect(Object.fromEntries(compared.cells.map((cell) => [cell.target, cell.verdict]))).toMatchObject({ r1c2: "duplicate", r1c3: "same", r2c2: "missing" });
    expect(compared.fabricated).toEqual([{ unitId: "p1-t1", target: "r9c9", text: "77", inRequest: false }, { unitId: "p1-t1", target: "r0c2", text: "2023", inRequest: true }]);
    expect(transcriptionRow("표", compared.cells, compared.fabricated.length, 0)).toMatchObject({ cells: 6, same: 4, different: 2, missing: 1, duplicate: 1, fabricated: 2 });
  });

  it("답을 받지 못한 요청의 칸은 전부 빠짐으로 남는다 (분모에서 빠지지 않는다)", () => {
    const compared = compareTranscription(unit(), { cells: [] });
    expect(transcriptionRow("표", compared.cells, 0, compared.cells.length)).toMatchObject({ cells: 6, same: 0, different: 6, missing: 6, inFailedUnits: 6 });
  });

  it("요청은 뜻 붙이기와 같은 표를 싣고, 답의 스키마는 셀 ID 와 글자뿐이다", () => {
    const request = transcribeRequest(unit(), { route: "light-openai", tags: TAGS, modelMaxOutputTokens: 4000 });
    expect(request).toMatchObject({ prompt: "reconcile/transcribe@1", route: "light-openai", schemaName: "reconcile_transcription", maxOutputTokens: (6 * 30 + 100) * 2 });
    expect(request.vars?.unit).toBe(unit().text);
    expect(transcribeAnswerSchema.safeParse({ cells: [{ target: "r1c2", text: "1,200" }] }).success).toBe(true);
    expect(transcribeAnswerSchema.safeParse({ cells: [{ target: "r1c2" }] }).success).toBe(false);
    expect(transcribeRequest(unit(), { route: "mock", tags: TAGS, modelMaxOutputTokens: 300 }).maxOutputTokens).toBe(300);
  });
});
