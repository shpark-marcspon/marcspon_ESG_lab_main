import type { Extraction, Fact, FactWhere } from "./facts.js";

// 자리 → 쪽과 상자. 문서 분해가 곁에 낸 글자 자리(`char-places.json`, `schemas/char-places.schema.json`)를 읽는다.
// 여기서 하는 일은 「그릇의 글에서 몇 번째 글자부터 몇 번째 글자까지」를 상자로 바꾸는 것뿐이다.
// 숫자가 어디인지는 숫자 뽑기(`facts.ts`)가 이미 정했다 (`where.span`). 이 파일에는 숫자를 알아보는 규칙이 없다.
// PDF 를 열지 않는다. 좌표는 PDF pt, 왼쪽 위 원점이다.

/** [x0, y0, x1, y1] pt. */
export type Box = [number, number, number, number];

/** 줄 토막: 그릇의 글에서 `start` 자리부터 이어지는 글자들의 가로 범위와, 토막의 세로 범위. 자리의 단위는 UTF-16 (문자열의 자리 그대로). */
export interface PlaceRun {
  start: number;
  y0: number;
  y1: number;
  x0: number[];
  x1: number[];
}

/** 그릇 하나: 글 블록(`p3-b2`) 또는 표의 칸(`p3-t1:r2c4`). */
export interface PlaceContainer {
  id: string;
  kind: "block" | "cell";
  page: number;
  /** 그릇의 글자 전체를 감싼 상자. */
  bbox: number[];
  runs: PlaceRun[];
}

/** 글자 자리 파일에서 이 패키지가 읽는 부분. */
export interface CharPlaces {
  source: { file_name: string; sha256: string; page_count: number };
  unit: "utf16";
  containers: PlaceContainer[];
  unlinked: { id: string; page: number; reason: string }[];
}

export type PlaceMissing =
  | "unknown_id" // 그런 ID 의 값 후보가 없다
  | "no_places" // 글자 자리 없이 분해한 문서다 (옵션을 켜지 않았다)
  | "not_in_document" // 엑셀처럼 PDF 문서 모델에서 나오지 않은 자리다
  | "container_unlinked" // 분해가 그 그릇의 글자 자리를 잇지 못했다
  | "container_missing" // 글자 자리에 그 그릇이 없다
  | "no_glyphs_in_span"; // 그 글자 범위에 좌표 있는 글자가 없다

export type Place =
  | {
      found: true;
      page: number;
      /** 줄마다 하나. 숫자가 두 줄에 걸치면 둘이다. */
      boxes: Box[];
      /** 그릇(칸·블록)의 글자 전체를 감싼 상자. */
      containerBox: Box;
      /** 범위의 글자 가운데 좌표가 있는 글자의 수 / 범위의 글자 수. 같지 않으면 분해가 끼운 빈칸이 범위에 들어 있다. */
      covered: number;
      length: number;
    }
  | { found: false; reason: PlaceMissing };

/** 두 세로 범위가 한 줄인가: 겹친 높이가 낮은 쪽 높이의 절반을 넘는다. (`py/docparse/charplaces.py` 의 `same_line` 과 같다) */
function sameLine(a: Box, b: Box): boolean {
  const overlap = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return overlap > 0.5 * Math.min(a[3] - a[1], b[3] - b[1]);
}

/** 그릇의 글자 범위 [start, end) 의 상자 (줄마다 하나)와 좌표가 있던 글자의 수. 범위를 안 주면 글 전체. */
export function spanBoxes(container: PlaceContainer, start?: number, end?: number): { boxes: Box[]; covered: number } {
  const boxes: Box[] = [];
  let covered = 0;
  for (const run of container.runs) {
    const lo = Math.max(start ?? run.start, run.start);
    const hi = Math.min(end ?? run.start + run.x0.length, run.start + run.x0.length);
    if (lo >= hi) continue;
    covered += hi - lo;
    let x0 = Infinity;
    let x1 = -Infinity;
    for (let k = lo - run.start; k < hi - run.start; k++) {
      x0 = Math.min(x0, run.x0[k] as number);
      x1 = Math.max(x1, run.x1[k] as number);
    }
    const box: Box = [x0, run.y0, x1, run.y1];
    const last = boxes[boxes.length - 1];
    if (last !== undefined && sameLine(last, box)) boxes[boxes.length - 1] = [Math.min(last[0], box[0]), Math.min(last[1], box[1]), Math.max(last[2], box[2]), Math.max(last[3], box[3])];
    else boxes.push(box);
  }
  return { boxes, covered };
}

/** 자리가 가리키는 그릇의 ID: 표의 칸이면 `표ID:r행c열`, 글 블록이면 블록 ID. 문서 모델의 자리가 아니면 null. */
export function containerIdOf(where: FactWhere): string | null {
  if (where.sheet !== null) return null;
  if (where.gridId !== null && where.row !== null && where.col !== null) return `${where.gridId}:r${where.row}c${where.col}`;
  return where.blockId;
}

export interface PlaceIndex {
  containers: Map<string, PlaceContainer>;
  unlinked: Set<string>;
}

export function indexPlaces(places: CharPlaces): PlaceIndex {
  return { containers: new Map(places.containers.map((container) => [container.id, container])), unlinked: new Set(places.unlinked.map((item) => item.id)) };
}

/** 자리(`where`) 하나의 쪽과 상자. 글 속 숫자면 그 글자 범위, 표의 숫자 칸이면 칸의 글 전체다. */
export function placeOfWhere(where: FactWhere, index: PlaceIndex | null | undefined): Place {
  if (index === null || index === undefined) return { found: false, reason: "no_places" };
  const id = containerIdOf(where);
  if (id === null) return { found: false, reason: "not_in_document" };
  const container = index.containers.get(id);
  if (container === undefined) return { found: false, reason: index.unlinked.has(id) ? "container_unlinked" : "container_missing" };
  const { boxes, covered } = spanBoxes(container, where.span?.start, where.span?.end);
  if (boxes.length === 0) return { found: false, reason: "no_glyphs_in_span" };
  const last = container.runs[container.runs.length - 1] as PlaceRun;
  const length = where.span ? where.span.end - where.span.start : last.start + last.x0.length;
  return { found: true, page: container.page, boxes, containerBox: container.bbox as Box, covered, length };
}

/**
 * 값 후보의 ID(`p3-t1:r2c4`, `p3-t1:r2c4#n1`, `p3-b2#n3`) → 쪽과 상자.
 * `extraction` 은 같은 문서 모델에서 뽑은 것이어야 한다. `places` 가 없으면(좌표 없이 분해한 문서) `no_places` 다.
 */
export function locatePlace(id: string, extraction: Extraction, places: CharPlaces | PlaceIndex | null | undefined): Place {
  const fact: Fact | undefined = extraction.facts.find((item) => item.id === id);
  if (fact === undefined) return { found: false, reason: "unknown_id" };
  if (places === null || places === undefined) return { found: false, reason: "no_places" };
  return placeOfWhere(fact.where, "source" in places ? indexPlaces(places) : places);
}

/** 문서 모델과 글자 자리가 같은 PDF 의 것인가. 다르면 자리가 엉뚱한 곳을 가리킨다. */
export function sameSource(document: { source: { sha256: string } }, places: CharPlaces): boolean {
  return document.source.sha256 === places.source.sha256;
}
