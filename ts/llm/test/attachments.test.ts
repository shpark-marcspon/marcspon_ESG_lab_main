import { describe, expect, it } from "vitest";
import { OpenAIProvider, type ProviderRequest } from "../src/index.js";
import { TAGS, harness } from "./helpers.js";

// 그림 첨부가 호출 계층을 지나 공급사에 전달되고, 공급사가 보고한 사용량(그림 토큰 포함)이 비용에 세어지는지. 모의 공급사와 요청 조립만 본다(네트워크 없음).

/** 1×1 PNG 의 머리 몇 바이트 (그림의 내용은 보지 않는다). 가상 자료다. */
const PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg==";
const image = { type: "image" as const, base64: PNG_BASE64, mediaType: "image/png" as const };
const base = { prompt: "t/plain@1", vars: { question: "번호마다 답해 줘" }, route: "mock", tags: TAGS };

describe("그림 첨부", () => {
  it("모의 공급사에 그림이 글보다 앞에 전달되고, 보고된 입력 토큰이 비용에 세어진다", async () => {
    const { client, mock, log } = harness({ storeBodies: true });
    // 그림이 든 호출의 가상 사용량: 입력 2,060 (글 60 + 그림 2,000 이라고 치자), 출력 20. 모의 단가(입력 1, 출력 2 USD/MTok)로 2,060 + 40 = 2,100 → 0.0021 USD.
    mock.enqueueText("ok", { inputTokens: 2060, outputTokens: 20, cacheReadTokens: 0, cacheWriteTokens: 0 });
    const result = await client.call({ ...base, attachments: [image] });
    expect(result.kind).toBe("success");
    expect(mock.calls[0]?.user.map((part) => part.type)).toEqual(["image", "text"]);
    expect(mock.calls[0]?.user[0]).toEqual(image);
    expect(result.meta.totalCostUsd).toBe(0.0021);
    expect(log.records[0]?.inputTokens).toBe(2060);
    expect(log.records[0]?.costUsd).toBe(0.0021);
  });

  it("첨부가 없으면 글만 전달된다", async () => {
    const { client, mock } = harness();
    mock.enqueueText("ok");
    await client.call(base);
    expect(mock.calls[0]?.user.map((part) => part.type)).toEqual(["text"]);
  });

  it("dry-run 은 그림의 토큰을 세지 않고, 세지 않은 첨부가 있다고 알린다", () => {
    const { client } = harness();
    const withImage = client.dryRun({ ...base, attachments: [image] });
    const plain = client.dryRun(base);
    expect(withImage.estimate.unestimatedParts).toEqual(["image"]);
    expect(plain.estimate.unestimatedParts).toEqual([]);
    expect(withImage.estimate.inputTokens).toBe(plain.estimate.inputTokens);
  });

  it("OpenAI 요청 본문: 그림은 input_image 의 data URL 로, 글은 그 뒤에 실린다", () => {
    const request: ProviderRequest = { apiModelId: "model-synthetic", system: [], user: [image, { type: "text", text: "질문" }], maxOutputTokens: 100 };
    const body = new OpenAIProvider().buildRequest(request);
    expect(body.input).toEqual([{ role: "user", content: [{ type: "input_image", detail: "auto", image_url: `data:image/png;base64,${PNG_BASE64}` }, { type: "input_text", text: "질문" }] }]);
  });
});
