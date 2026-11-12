import type { InvokeOptions, Provider, ProviderRequest, ProviderResponse, Usage } from "../types.js";

export type MockReply = ProviderResponse | ((request: ProviderRequest) => ProviderResponse);

interface Scripted {
  reply: MockReply;
  delayMs: number;
}

const SYNTHETIC_USAGE: Usage = { inputTokens: 100, outputTokens: 20, cacheReadTokens: 0, cacheWriteTokens: 0 };

/** 키 없이 쓰는 모의 공급사. 심어 둔 응답을 순서대로 돌려주고 받은 요청을 기록한다. */
export class MockProvider implements Provider {
  readonly calls: ProviderRequest[] = [];
  private readonly queue: Scripted[] = [];

  constructor(readonly name = "mock") {}

  /** 다음 호출이 받을 응답을 심는다. `delayMs` 를 주면 그만큼 늦게 답한다(시간 초과 시험용). */
  enqueue(reply: MockReply, options: { delayMs?: number } = {}): this {
    this.queue.push({ reply, delayMs: options.delayMs ?? 0 });
    return this;
  }

  /** 글자만 주면 완료 응답으로 심는다. 사용량은 가상 값이다. */
  enqueueText(text: string, usage: Usage = SYNTHETIC_USAGE): this {
    return this.enqueue({ kind: "completed", text, usage });
  }

  get pending(): number {
    return this.queue.length;
  }

  buildRequest(request: ProviderRequest): unknown {
    return { mock: true, ...request };
  }

  async invoke(request: ProviderRequest, options: InvokeOptions): Promise<ProviderResponse> {
    this.calls.push(request);
    const scripted = this.queue.shift();
    // 심어 둔 응답이 없으면 성공을 지어내지 않고 오류로 돌린다.
    if (!scripted) return { kind: "error", status: null, retryable: false, message: `모의 공급사 ${this.name} 에 심어 둔 응답이 없다` };
    if (scripted.delayMs > 0) {
      const aborted = await sleep(scripted.delayMs, options.signal);
      if (aborted) return { kind: "timeout", message: "모의 공급사: 호출이 중단됐다" };
    }
    return typeof scripted.reply === "function" ? scripted.reply(request) : scripted.reply;
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve(true);
      },
      { once: true },
    );
  });
}
