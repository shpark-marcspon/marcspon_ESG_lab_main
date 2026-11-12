/**
 * 공급사 오류 문구에 키(일부가 가려진 꼴 포함)가 섞여 와도 기록과 출력에 남지 않게 지운다.
 * `sk-` 로 시작하는 꼴(OpenAI, Anthropic)과 `AIza` 로 시작하는 꼴(Google)을 가린다.
 */
export function redactKeys(text: string): string {
  return text.replace(/(?<![A-Za-z0-9])sk-[A-Za-z0-9_*.-]{4,}/g, "sk-[가림]").replace(/(?<![A-Za-z0-9])AIza[A-Za-z0-9_*-]{8,}/g, "AIza[가림]");
}
