import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { XMLParser } from "fast-xml-parser";
import { strFromU8, unzipSync } from "fflate";
import { FORMULA_WITHOUT_RESULT, estimateHeader, type Grid, type StoredNumber } from "../domain/facts.js";
import { BUILTIN_FORMATS, isDateFormat, renderNumber, storedToDecimal } from "../domain/numfmt.js";

// 엑셀(xlsx) 읽기. 파일 안의 XML 을 직접 읽어 저장 값의 글자(`<v>`)를 그대로 가져온다.
// 스프레드시트 라이브러리를 쓰지 않는 까닭: 그쪽은 저장 값을 JS number 로 돌려준다.
// 다루는 것: 숫자·글자(공유 문자열, 인라인)·수식의 저장된 결과. 다루지 않는 것: 병합 범위, 숨긴 행·열, 1904 날짜 체계, xls·xlsb.

export class XlsxError extends Error {
  override name = "XlsxError";
}

type Node = Record<string, unknown>;

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  // 숫자처럼 보이는 글자를 number 로 바꾸지 않는다.
  parseTagValue: false,
  parseAttributeValue: false,
  trimValues: false,
  removeNSPrefix: true,
  isArray: (name) => ["row", "c", "si", "r", "xf", "numFmt", "sheet", "Relationship"].includes(name),
});

const list = (value: unknown): Node[] => (Array.isArray(value) ? (value as Node[]) : value === undefined || value === null || value === "" ? [] : [value as Node]);
const child = (node: unknown, name: string): unknown => (node && typeof node === "object" ? (node as Node)[name] : undefined);
const attr = (node: unknown, name: string): string | undefined => {
  const value = child(node, `@_${name}`);
  return typeof value === "string" ? value : undefined;
};

function textOf(node: unknown): string {
  if (typeof node === "string") return node;
  if (node && typeof node === "object") {
    const text = child(node, "#text");
    return typeof text === "string" ? text : "";
  }
  return "";
}

/** `<si>` 또는 `<is>`: 글자 하나(`t`)이거나 서식 토막(`r/t`)의 이음이다. */
function richText(node: unknown): string {
  const direct = child(node, "t");
  if (direct !== undefined) return textOf(direct);
  return list(child(node, "r")).map((run) => textOf(child(run, "t"))).join("");
}

function columnIndex(ref: string): number {
  const letters = /^[A-Z]+/.exec(ref)?.[0] ?? "";
  let index = 0;
  for (const letter of letters) index = index * 26 + (letter.charCodeAt(0) - 64);
  return index - 1;
}

interface SheetCell {
  ref: string;
  row: number;
  col: number;
  text: string;
  numeric: StoredNumber | null;
}

/** xlsx 파일 하나를 격자 묶음으로 읽는다. 시트 안의 빈 행은 표의 경계로 본다. */
export function readXlsx(path: string, docId: string): { fileName: string; grids: Grid[] } {
  let files: Record<string, Uint8Array>;
  try {
    files = unzipSync(new Uint8Array(readFileSync(path)));
  } catch (error) {
    throw new XlsxError(`${path}: xlsx(zip) 로 읽을 수 없다: ${(error as Error).message}`);
  }
  const xml = (name: string): Node | null => (files[name] ? (parser.parse(strFromU8(files[name])) as Node) : null);
  const workbook = xml("xl/workbook.xml");
  const rels = xml("xl/_rels/workbook.xml.rels");
  if (!workbook || !rels) throw new XlsxError(`${path}: 통합 문서 정의(xl/workbook.xml)가 없다`);

  const shared = list(child(child(xml("xl/sharedStrings.xml"), "sst"), "si")).map(richText);
  const styles = child(xml("xl/styles.xml"), "styleSheet");
  const customFormats = new Map(list(child(child(styles, "numFmts"), "numFmt")).map((node) => [Number(attr(node, "numFmtId")), attr(node, "formatCode") ?? "General"]));
  const xfs = list(child(child(styles, "cellXfs"), "xf")).map((node) => Number(attr(node, "numFmtId") ?? "0"));
  const formatOf = (styleIndex: string | undefined): string => {
    const id = xfs[Number(styleIndex ?? "0")] ?? 0;
    const code = customFormats.get(id) ?? BUILTIN_FORMATS[id];
    if (code !== undefined) return code;
    // 내장 번호 27~36, 50~58 은 지역별 날짜 서식이다. 그 밖의 모르는 번호는 지원하지 않는 서식으로 남긴다.
    return (id >= 27 && id <= 36) || (id >= 50 && id <= 58) ? "yyyy-mm-dd" : `?numFmtId=${id}`;
  };
  const targets = new Map(list(child(child(rels, "Relationships"), "Relationship")).map((node) => [attr(node, "Id"), attr(node, "Target")]));

  const grids: Grid[] = [];
  for (const sheet of list(child(child(child(workbook, "workbook"), "sheets"), "sheet"))) {
    const name = attr(sheet, "name") ?? "";
    const target = targets.get(attr(sheet, "id"));
    if (!target) throw new XlsxError(`${path}: 시트 ${name} 의 파일을 찾지 못했다`);
    const sheetPath = target.startsWith("/") ? target.slice(1) : `xl/${target}`;
    const sheetXml = xml(sheetPath);
    if (!sheetXml) throw new XlsxError(`${path}: ${sheetPath} 가 없다`);

    const cells: SheetCell[] = [];
    for (const row of list(child(child(child(sheetXml, "worksheet"), "sheetData"), "row"))) {
      for (const c of list(child(row, "c"))) {
        const ref = attr(c, "r");
        if (!ref) throw new XlsxError(`${path}: ${name} 에 주소 없는 셀이 있다`);
        const type = attr(c, "t") ?? "n";
        const rawValue = child(c, "v");
        const value = rawValue === undefined ? null : textOf(rawValue);
        const formula = child(c, "f") !== undefined;
        const position = { ref, row: Number(/\d+$/.exec(ref)?.[0]) - 1, col: columnIndex(ref) };
        if (type === "s") cells.push({ ...position, text: shared[Number(value)] ?? "", numeric: null });
        else if (type === "inlineStr") cells.push({ ...position, text: richText(child(c, "is")), numeric: null });
        else if (type === "str" || type === "b" || type === "e") cells.push({ ...position, text: value ?? "", numeric: null });
        else if (value === null || value === "") {
          // 값 없는 셀. 수식인데 저장된 결과가 없으면 그 사실을 글자로 남긴다 (값을 지어내지 않는다).
          if (formula) cells.push({ ...position, text: FORMULA_WITHOUT_RESULT, numeric: null });
        } else {
          const stored = storedToDecimal(value);
          if (stored === null) {
            cells.push({ ...position, text: value, numeric: null });
            continue;
          }
          const formatCode = formatOf(attr(c, "s"));
          const date = isDateFormat(formatCode);
          const rendered = date ? { display: null, status: "unsupported_format" as const } : renderNumber(stored, formatCode);
          cells.push({ ...position, text: rendered.display ?? stored, numeric: { storedRaw: value, stored, formatCode, display: rendered.display, displayStatus: rendered.status, isDate: date, formula } });
        }
      }
    }
    grids.push(...sheetGrids(cells.filter((cell) => cell.text !== "" || cell.numeric), name, docId));
  }
  return { fileName: basename(path), grids };
}

function sheetGrids(cells: SheetCell[], sheet: string, docId: string): Grid[] {
  if (cells.length === 0) return [];
  const rows = [...new Set(cells.map((cell) => cell.row))].sort((a, b) => a - b);
  // 빈 행으로 끊긴 덩어리마다 격자 하나.
  const segments: number[][] = [];
  for (const row of rows) {
    const last = segments[segments.length - 1];
    if (last && row === (last[last.length - 1] as number) + 1) last.push(row);
    else segments.push([row]);
  }
  const grids: Grid[] = [];
  let pendingTitles: string[] = [];
  for (const segment of segments) {
    const inside = cells.filter((cell) => segment.includes(cell.row));
    // 글자 셀 하나뿐인 덩어리는 표가 아니라 다음 표의 제목 후보다.
    if (inside.length === 1 && !(inside[0] as SheetCell).numeric) {
      pendingTitles = [(inside[0] as SheetCell).text, ...pendingTitles].slice(0, 3);
      continue;
    }
    const cols = inside.map((cell) => cell.col);
    const minCol = Math.min(...cols);
    const width = Math.max(...cols) - minCol + 1;
    const first = segment[0] as number;
    const text = segment.map(() => Array.from({ length: width }, () => ""));
    const numeric = segment.map(() => Array.from({ length: width }, (): StoredNumber | null => null));
    const refs = segment.map(() => Array.from({ length: width }, () => ""));
    for (const cell of inside) {
      const r = cell.row - first;
      const c = cell.col - minCol;
      (text[r] as string[])[c] = cell.text;
      (numeric[r] as (StoredNumber | null)[])[c] = cell.numeric;
      (refs[r] as string[])[c] = cell.ref;
    }
    const firstRef = (inside.reduce((best, cell) => (cell.row < best.row || (cell.row === best.row && cell.col < best.col) ? cell : best))).ref;
    const flat = inside.map((cell) => cell.text);
    grids.push({
      id: `${sheet}!${firstRef}`,
      docId,
      kind: "sheet",
      page: null,
      sheet,
      cells: text,
      header: estimateHeader(text),
      status: "ok",
      footnoteMarks: [],
      titleCandidates: pendingTitles,
      unitLines: [...new Set([...pendingTitles, ...flat].filter((line) => /단위\s*[:：]/u.test(line) && line.length <= 60))],
      numeric,
      refs,
    });
    pendingTitles = [];
  }
  return grids;
}
