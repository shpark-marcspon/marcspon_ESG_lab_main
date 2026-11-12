import { describe, expect, it } from "vitest";
import { FilePromptStore, defaultPromptRoot, parsePromptId, parseTemplate, renderPrompt } from "../src/index.js";
import { TAGS, harness, promptRoot } from "./helpers.js";

describe("프롬프트 저장소", () => {
  it("버전 고정: 요청한 버전의 본문만 쓴다", async () => {
    const { client, mock, log } = harness();
    mock.enqueueText("a").enqueueText("b");
    const request = { vars: { question: "q" }, route: "mock", tags: TAGS };

    await client.call({ ...request, prompt: "t/plain@1" });
    await client.call({ ...request, prompt: "t/plain@2" });

    expect(mock.calls[0]?.system[0]?.text).toBe("짧게 답한다.");
    expect(mock.calls[1]?.system[0]?.text).toBe("길게 답한다.");
    expect(log.records.map((record) => record.promptVersion)).toEqual([1, 2]);
    expect(log.records[0]?.templateHash).not.toBe(log.records[1]?.templateHash);
  });

  it("버전 없는 id 와 없는 버전은 거부한다 (최신으로 흘러가지 않는다)", () => {
    const store = new FilePromptStore(promptRoot());
    expect(() => parsePromptId("t/plain")).toThrow("버전 필수");
    expect(() => parsePromptId("t/plain@latest")).toThrow("버전 필수");
    expect(() => parsePromptId("t/plain@0")).toThrow("버전 필수");
    expect(() => store.get(parsePromptId("t/plain@3"))).toThrow("프롬프트가 없다");
    expect(parsePromptId("scope3/classify@12")).toEqual({ module: "scope3", name: "classify", version: 12 });
  });

  it("변수 치환: 빠진 변수와 남는 변수는 둘 다 오류다", () => {
    const template = new FilePromptStore(promptRoot()).get(parsePromptId("t/plain@1"));
    expect(renderPrompt(template, { question: "배출량은?" }).user).toBe("배출량은?");
    expect(() => renderPrompt(template, {})).toThrow("변수 question 의 값이 없다");
    expect(() => renderPrompt(template, { question: "q", extra: "x" })).toThrow("템플릿에 없는 변수");
  });

  it("치환된 값 안의 {{...}} 는 다시 치환하지 않는다", () => {
    const template = new FilePromptStore(promptRoot()).get(parsePromptId("t/plain@1"));
    expect(renderPrompt(template, { question: "{{question}}" }).user).toBe("{{question}}");
  });

  it("템플릿 형식 오류를 거부한다", () => {
    const ref = { module: "t", name: "x", version: 1 };
    expect(() => parseTemplate(ref, "=== system ===\n지침\n")).toThrow("user 구획이 없다");
    expect(() => parseTemplate(ref, "본문\n=== user ===\nq\n")).toThrow("앞에 본문이 있다");
    expect(() => parseTemplate(ref, "=== user ===\n\n")).toThrow("구획이 비었다");
    expect(() => parseTemplate(ref, "=== system ===\na\n=== system cache ===\nb\n=== user ===\nq\n")).toThrow("앞에 와야 한다");
    expect(() => parseTemplate(ref, "=== user ===\nq\n=== system ===\na\n")).toThrow("user 구획 앞에");
  });

  it("저장소에 든 예제 프롬프트를 읽는다", () => {
    const store = new FilePromptStore(defaultPromptRoot());
    expect(store.get(parsePromptId("example/hello@1")).system[0]?.cache).toBe(true);
    expect(store.get(parsePromptId("example/hello@2")).user).toContain("{{name}}");
  });
});
