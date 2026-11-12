import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { z } from "zod";
import { EmbeddingClient, defaultConfigPaths, loadConfig } from "llm";
import { ReconcileFileError, documentSchema, readJsonFile, resolveInputPath, resolveOutputPath } from "../adapters/files.js";
import { StaleIndexError, inputsSha256, loadUnitIndex, sha256File, type UnitIndex } from "../adapters/unitIndex.js";
import { extractDocument, type DocModel } from "../domain/facts.js";
import { KS, chartUnits, fuseRrf, pageUnits, rankEmbedding, rankLexical, recall, scoreQuery, tableUnits, type Query, type QueryScore, type Ranked, type Unit } from "./embed-nominate.js";
import { MIN_SIGNAL_DIGITS, significantDigits } from "./focus.js";

// 밴드 EB1 (MARC-TICK-45) 의 (5): 같은 문서 안에서 「도표 → 표·쪽」의 지목이 서는가 (호출 0. 밴드 EB1 이 만든 색인을 읽기만 한다).
// 이 파일은 첫 임베딩 호출 뒤에 썼다. 정답은 값으로 센다: 도표의 긴 값(유효 숫자 4자리 이상)과 글자가 같은 값이 같은 문서의 도표 밖 자리에 있으면
// 그 자리의 쪽·표가 옳은 상대다. 값이 같다는 것만 본 것이라 아래쪽 끝이고, 우연히 같은 값도 섞일 수 있다. 순위는 값을 보지 않는다 (가린 글만).

type Out = (line: string) => void;
const USAGE = `사용법 (cd ts && pnpm -F reconcile exec tsx src/compare/cli15.ts same-doc --doc <문서> --side <a|b> --chart-labels <이름=라벨 파일> --index <색인 디렉터리> --out <디렉터리> [--route <임베딩 경로>])
  도표 단위마다 같은 문서의 표·쪽을 줄 세워, 그 도표의 긴 값과 같은 값이 도표 밖에 실린 쪽·표가 몇 등인지 센다 (호출 0).`;
const METHODS = ["lexical", "embedding", "hybrid"] as const;

export async function main(argv: string[], out: Out = console.log): Promise<number> {
  const [command, ...rest] = argv;
  let values;
  try {
    values = parseArgs({ args: rest, options: { doc: { type: "string" }, side: { type: "string" }, "chart-labels": { type: "string" }, index: { type: "string" }, out: { type: "string" }, route: { type: "string" } } }).values;
  } catch (error) {
    out(`${(error as Error).message}\n\n${USAGE}`);
    return 2;
  }
  try {
    if (command !== "same-doc" || !values.doc || !values["chart-labels"] || !values.index || !values.out || (values.side !== "a" && values.side !== "b")) return out(USAGE), 2;
    const side = values.side;
    const path = resolveInputPath(values.doc);
    const parsed = documentSchema.safeParse(readJsonFile(path, "문서 JSON"));
    if (!parsed.success) throw new ReconcileFileError(`${path}: 문서 JSON 형식이 맞지 않는다`);
    const model = parsed.data as DocModel;
    const extraction = extractDocument(model, side, { titles: "own", kinds: true });
    const [name, labelPath] = values["chart-labels"].split("=");
    if (!name || !labelPath) throw new ReconcileFileError("--chart-labels 는 <이름=라벨 파일> 꼴이다");
    const labelFile = z.record(z.enum(["a", "b"]), z.record(z.string(), z.string())).safeParse(readJsonFile(resolveInputPath(labelPath), "도표 라벨"));
    if (!labelFile.success) throw new ReconcileFileError(`${labelPath}: 도표 라벨 형식이 맞지 않는다`);
    const labels = labelFile.data[side] ?? {};
    const charts = chartUnits(extraction, side, labels, name);
    const candidates: Record<string, Unit[]> = { table: tableUnits(extraction, side).filter((unit) => unit.chunks.length > 0), page: pageUnits(model, extraction, side) };

    const config = loadConfig(defaultConfigPaths({ synthetic: true }));
    const embedding = new EmbeddingClient({ config, providers: {}, log: { write: () => undefined } }).model(values.route ?? "light-openai");
    const documentSha256 = sha256File(path);
    const indexDir = resolveOutputPath(values.index);
    const indexes = new Map<string, UnitIndex>();
    for (const [kind, units] of [[`chart-${name}`, charts], ["table", tableUnits(extraction, side)], ["page", candidates.page as Unit[]]] as const) {
      const inputs = units.flatMap((unit) => unit.chunks.map((text, index) => ({ id: `${unit.id}#c${index}`, text })));
      indexes.set(kind, loadUnitIndex(join(indexDir, `${side}.${kind}.index`), { documentSha256, modelKey: embedding.key, dimensions: embedding.dimensions, unitKind: kind, inputsSha256: inputsSha256(inputs) }));
    }
    const vectorsOf = (unit: Unit): Float32Array[] => unit.chunks.map((_, index) => indexes.get(unit.kind)?.vector(`${unit.id}#c${index}`)).filter((vector): vector is Float32Array => vector !== undefined);

    // 정답 (값으로 센다): 도표의 긴 값과 글자가 같은 값이 도표 밖(라벨이 없는 값 후보)에 있는 자리.
    const outside = new Map<string, { page: number; table: string | null; id: string }[]>();
    for (const fact of extraction.facts) {
      if (labels[fact.id] !== undefined || fact.where.page === null) continue;
      if (extraction.kinds && extraction.kinds[fact.id] !== undefined && extraction.kinds[fact.id] !== "value") continue;
      const key = `${fact.value}|${fact.percent}`;
      outside.set(key, [...(outside.get(key) ?? []), { page: fact.where.page, table: fact.where.gridId, id: fact.id }]);
    }
    const factOf = new Map(extraction.facts.map((fact) => [fact.id, fact]));
    const golds = charts.map((chart) => {
      const places = chart.places.map((id) => factOf.get(id)).filter((fact) => fact !== undefined && significantDigits(fact.value) >= MIN_SIGNAL_DIGITS);
      const hits = places.flatMap((fact) => (outside.get(`${fact?.value}|${fact?.percent}`) ?? []).map((hit) => ({ ...hit, from: fact?.id as string })));
      return { chart, longPlaces: places.length, matchedPlaces: new Set(hits.map((hit) => hit.from)).size, hits };
    });

    const lines: string[] = [`## 같은 문서 안: 도표 → 표·쪽 (문서 ${side}, 라벨 ${name}. 정답은 값으로 센 것이라 아래쪽 끝이다)`, "", `도표 단위 ${charts.length}개. 긴 값이 있는 도표 ${golds.filter((gold) => gold.longPlaces > 0).length}개(긴 값 ${golds.reduce((sum, gold) => sum + gold.longPlaces, 0)}자리), 그 가운데 같은 값이 도표 밖에 있는 도표 ${golds.filter((gold) => gold.hits.length > 0).length}개(${golds.reduce((sum, gold) => sum + gold.matchedPlaces, 0)}자리).`, "", "| 후보 단위 | 옳은 상대의 범위 | 방법 | 질의 수 | @1 | @3 | @5 | 순위표에 없음 | 옳은 쪽의 순위 |", "|---|---|---|---:|---:|---:|---:|---:|---|"];
    const detail: Record<string, unknown>[] = [];
    for (const [kind, targets] of Object.entries(candidates)) {
      const byId = new Map(targets.map((unit) => [unit.id, unit]));
      const pageOf = (id: string): number => (byId.get(id) as Unit).page;
      for (const scope of ["다른 쪽만", "같은 쪽 포함"] as const) {
        const scores: Record<string, QueryScore[]> = { lexical: [], embedding: [], hybrid: [] };
        for (const gold of golds) {
          const hits = gold.hits.filter((hit) => scope === "같은 쪽 포함" || hit.page !== gold.chart.page);
          if (hits.length === 0) continue;
          const query: Query = { unit: gold.chart.id, state: "right", rightPages: [...new Set(hits.map((hit) => hit.page))].sort((x, y) => x - y), rightUnits: kind === "table" ? targets.filter((unit) => hits.some((hit) => hit.table !== null && hit.table === unit.table)).map((unit) => unit.id) : [] };
          // 다른 쪽만 볼 때는 도표가 든 쪽의 단위를 후보에서 뺀다 (같은 쪽은 묻는 요청에 이미 실린다).
          const pool = scope === "다른 쪽만" ? targets.filter((unit) => unit.page !== gold.chart.page) : targets;
          const lexical = rankLexical(gold.chart.text, pool);
          const semantic = rankEmbedding(vectorsOf(gold.chart), pool, (id) => vectorsOf(byId.get(id) as Unit));
          if (!Array.isArray(semantic)) throw new StaleIndexError(`색인에 단위 ${semantic.missing} 의 벡터가 없다`);
          const ranked: Record<string, Ranked[]> = { lexical, embedding: semantic, hybrid: fuseRrf([lexical, semantic], pool.map((unit) => unit.id)) };
          for (const method of METHODS) {
            const score = scoreQuery(query, ranked[method] as Ranked[], pageOf);
            (scores[method] as QueryScore[]).push(score);
            detail.push({ candidates: kind, scope, method, chart: gold.chart.id, title: gold.chart.text.split("\n")[0], rightPages: query.rightPages, pageRank: score.pageRank, top: (ranked[method] as Ranked[]).slice(0, 5).map((entry) => `${entry.id} ${entry.score.toFixed(4)}`) });
          }
        }
        for (const method of METHODS) {
          const tally = recall(scores[method] as QueryScore[]);
          lines.push(`| ${kind} | ${scope} | ${method} | ${tally.queries} | ${KS.map((k) => `${tally.atK[k] ?? 0}/${tally.queries}`).join(" | ")} | ${tally.unranked} | ${tally.ranks.join(" ")} |`);
        }
      }
    }
    const outDir = resolveOutputPath(values.out);
    mkdirSync(outDir, { recursive: true });
    const text = `${lines.join("\n")}\n`;
    writeFileSync(join(outDir, "same-doc.md"), text);
    writeFileSync(join(outDir, "same-doc.json"), `${JSON.stringify({ note: "정답은 값이 같은 것으로 센 것이다 (아래쪽 끝, 우연히 같은 값이 섞일 수 있다). 순위는 가린 글만 본다.", gold: golds.map((gold) => ({ chart: gold.chart.id, page: gold.chart.page, longPlaces: gold.longPlaces, matchedPlaces: gold.matchedPlaces, hits: gold.hits })), detail }, null, 1)}\n`);
    out(text);
    return 0;
  } catch (error) {
    if (error instanceof ReconcileFileError || error instanceof StaleIndexError) {
      out(`${error.name}: ${error.message}`);
      return 1;
    }
    throw error;
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      console.error(error);
      process.exit(1);
    },
  );
}
