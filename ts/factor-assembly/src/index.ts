export { SOURCE_IDS, assembleCombustionFactor } from "./domain/assemble.js";
export { RULES, resolveTiers } from "./domain/policy.js";
export type { ResolvedTiers } from "./domain/policy.js";
export { resolveSector } from "./domain/sector.js";
export type { ResolvedSector } from "./domain/sector.js";
export { readValueRaw } from "./domain/valueRaw.js";
export { replayHandCases, replaySummary } from "./domain/replay.js";
export { GIR_EDITIONS, TABLE69, assemblePurchasedElectricityFactor, electricityComparison, electricityOptions } from "./domain/electricity.js";
export type {
  ElectricityBoundary,
  ElectricityComparisonRow,
  ElectricityEvidence,
  ElectricityFailure,
  ElectricityGas,
  ElectricityGasForm,
  ElectricityMethodology,
  ElectricityParameterEvidence,
  ElectricityPeriod,
  ElectricityRequest,
  ElectricityResult,
  ElectricitySource,
  GirSelection,
} from "./domain/electricity.js";
export { COVERAGE_MODES, coverageRows, coverageSummary, nationalCalorificFuels, statesFromMapping } from "./domain/coverage.js";
export type { CoverageInputs, CoverageMode, CoverageOutcome, CoverageRow, CoverageSummaryCell } from "./domain/coverage.js";
export type { ExpectedResult, HandCase, ReplayOutcome, ReplayRow, ReplaySpec, ReplayTables } from "./domain/replay.js";
export type * from "./domain/types.js";
export { AssemblyFileError, defaultFactorsPath, defaultMappingPath, factorRecordSchema, loadElectricityRecords, loadGuidelineRecords, loadMapping, loadRecordsBySourcePrefix, mappingSchema } from "./adapters/files.js";
