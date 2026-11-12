export { AdjustmentError, calculateRow, validateAdjustments, type CalcResult, type CurrencyAdjustment } from "./domain/calc.js";
export {
  DEFAULT_SECTOR,
  analyzeCoverage,
  evaluate,
  planDryRun,
  predictedCategory,
  sectorKey,
  type CoverageReport,
  type DryRunPlan,
  type EvalReport,
  type MatchCriterion,
  type SectorOptions,
} from "./eval/evaluate.js";
export { ExampleSetError, loadAdjustments, loadExampleSet, type ExampleRow, type ExampleSet, type Gold } from "./eval/examples.js";
export { FactorStore, FactorStoreError, defaultFactorsPath, factorRecordSchema, loadFactorStore } from "./adapters/jsonlFactorCatalog.js";
export type { CandidateQuery, Factor, LicenseStatus } from "./domain/factor.js";
export {
  CLASSIFY_MAX_OUTPUT_TOKENS,
  CLASSIFY_PROMPT,
  MAX_PICKS,
  RECOMMEND_MAX_OUTPUT_TOKENS,
  RECOMMEND_PROMPT,
  candidateForPrompt,
  classificationSchema,
  itemForPrompt,
  recommendationSchema,
  type Classification,
  type LlmFailure,
  type LlmUsage,
} from "./domain/llmSteps.js";
export { FileMappingMemory, MemoryError } from "./adapters/fileMappingMemory.js";
export { InMemoryMappingMemory, type MappingMemory, type MemoryEntry } from "./domain/memory.js";
export { HeuristicMockProvider, heuristicReply } from "./eval/mockHeuristic.js";
export { groupKey, groupRows, normalizeItemName, type RowGroup } from "./domain/normalize.js";
export {
  DEFAULT_SHORTLIST_SIZE,
  calculateFor,
  decideGroup,
  runPipeline,
  type BasisTrace,
  type CandidateStats,
  type GroupDecision,
  type PipelineOptions,
  type PipelineResult,
  type RowResult,
  type ShortlistTrace,
} from "./domain/pipeline.js";
export {
  TRANSITIONS,
  accept,
  applyAction,
  confirmedMapping,
  edit,
  finalize,
  propose,
  receiveProposal,
  reject,
  requestRerecommend,
  toMemoryEntry,
  type AiProposal,
  type HumanEdit,
  type ReviewAction,
  type ReviewItem,
  type ReviewState,
} from "./domain/review.js";
export {
  EmbeddingIndex,
  EmbeddingIndexError,
  INDEX_TEXT_VERSION,
  StaleIndexError,
  assertFresh,
  buildEmbeddingIndex,
  loadEmbeddingIndex,
  sha256File,
  type EmbeddingIndexHeader,
  type IndexExpectation,
} from "./adapters/embeddingIndex.js";
export { measureRecall, recallTargets, type GoldRank, type MethodRecall, type PriorRow, type RecallOutcome, type RecallRow, type RecallTarget } from "./eval/recall.js";
export { EmbeddingShortlister, LlmQueryEmbedder, type EmbeddingShortlisterOptions } from "./adapters/embeddingShortlister.js";
export {
  DEFAULT_SHORTLIST_METHOD,
  SHORTLIST_METHODS,
  type FactorCatalog,
  type FactorVectorIndex,
  type LexicalMethod,
  type LlmGateway,
  type QueryEmbedder,
  type QueryEmbedding,
  type ShortlistFailure,
  type ShortlistMethod,
  type ShortlistOutcome,
  type Shortlister,
} from "./domain/ports.js";
export {
  BM25_B,
  BM25_K1,
  EMBEDDING_QUERY_MODES,
  RRF_K,
  cosine,
  embeddingQueryText,
  factorText,
  fuseRrf,
  rankByEmbedding,
  rankLexicalV2,
  singular,
  tokenize,
  type CandidateEvidence,
  type EmbeddingQueryMode,
  type LexicalV2Options,
  type RankedCandidate,
} from "./domain/ranking.js";
export { LexicalV1Shortlister, LexicalV2Shortlister, VectorShortlister, defaultShortlister, lexicalShortlister, type VectorShortlisterOptions } from "./domain/shortlister.js";
export { rankLexicalV1, shortlist, type Shortlist, type ShortlistQuery } from "./domain/lexicalV1.js";
export { SUPPORTED_CATEGORIES, type Basis, type Confidence, type FactorPick, type FallbackReason, type InputRow, type SupportedCategory, type TransportInfo } from "./domain/types.js";
export { activityDim, conversionFactor, unitDim, type UnitDim } from "./domain/units.js";
