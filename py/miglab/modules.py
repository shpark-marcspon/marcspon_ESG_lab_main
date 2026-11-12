"""모듈 일곱의 실행기: esg-app 의 흐름을 run_flow 로 그대로 부른다(실행기가 부르는 입구와 같은 길). 바꾸는 것은
클라이언트(판의 models 파일)와, 판 luna 의 온도 칸 None(감독 판단 ②) 뿐이다. 프롬프트·추론 강도는 코드 그대로.

모듈 키: gw(그린워싱 e0) · tr(영문번역 colab_158) · dv(데이터 검증 colab_156) · mt1(중대성평가 1부 colab_155) ·
mt2(2부 part2_155 — 1부 결과를 씨앗으로) · mt(1부 → 2부) · s3(Scope 3 colab_157) · dr(원고작성 v2).
mt2 는 혼자 돌지 않는다: mt 가 1부 결과로 2부를 잇는다(mt2 의 줄은 calls 의 promptId 로 가른다).
"""

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Final

from miglab import env, fixtures

env.setup_paths()

from esgapi.core.flows.run import run_flow  # noqa: E402
from esgapi.core.llm.types import Tags  # noqa: E402

MODULES: Final = ("gw", "tr", "dv", "mt1", "mt", "s3", "dr")
TOOL_MODULES: Final = frozenset({"dv", "mt1", "mt"})
MT1_PROMPTS: Final = frozenset({"materiality/regulation@1", "materiality/keywords@1", "materiality/controversy@1"})
MT2_PROMPTS: Final = frozenset({"materiality/impact@1", "materiality/financial@1", "materiality/pathway@1"})
TENANT: Final = "mig1-synthetic"


@dataclass
class Options:
    variant: str
    size: str = "full"  # full | probe
    pdf: Path | None = None
    pages: str | None = None


@dataclass
class Ran:
    result: Any = None
    error: str | None = None
    notes: list[str] = field(default_factory=list[str])
    input_key: Mapping[str, Any] = field(default_factory=dict[str, Any])
    source: str | None = None
    """원문(지은 글 — 번역의 숫자·태그 보존 셈용). 요약에 싣지 않는다."""


def luna(options: Options) -> bool:
    return options.variant == "luna"


def _temperature(options: Options, code: float | None) -> float | None:
    """판 luna 는 온도를 싣지 않는다(감독 판단 ② — 모델 + 온도 빠짐을 한 변수로). old·luna-raw 는 코드 값 그대로."""
    return None if luna(options) else code


def _run(flow: Any, inputs: Any, adapters: Any) -> Ran:
    try:
        done = run_flow(flow, inputs, adapters)
    except Exception as error:  # noqa: BLE001 — 깨지는 자리를 적는 것이 목적이다
        cause = error.__cause__ or error.__context__
        detail = f"{type(error).__name__}: {error}"
        if cause is not None:
            detail += f" ← {type(cause).__name__}: {cause}"
        return Ran(error=detail[:2000])
    return Ran(result=list(done.output.results))


# ── gw ──


def run_gw(client: Any, options: Options) -> Ran:
    from esgapi.greenwash.engines.e0.ports import MockEmbedding
    from esgapi.greenwash.flows import colab_e0

    if options.pdf is None or options.pages is None:
        raise SystemExit("gw 는 --pdf <공개 PDF> --pages <쪽, 예 12~16> 이 필요하다")
    data = options.pdf.read_bytes()
    pages = options.pages
    if options.size == "probe":
        first = pages.replace("-", "~").split("~")[0].split(",")[0]
        pages = first
    inputs = colab_e0.GreenwashInputs(
        document=data, media_type="application/pdf", file_name="input.pdf", ceo="", company="", climate=pages
    )
    bridge = colab_e0.LlmChatBridge(
        client,
        tags=Tags(tenant=TENANT, module="greenwash"),
        temperature=_temperature(options, colab_e0.TEMPERATURE),
    )
    adapters = colab_e0.GreenwashAdapters(pdf_text=colab_e0.PDF_TEXT(), llm=bridge, embedding=MockEmbedding())
    ran = _run(colab_e0.FLOW, inputs, adapters)
    ran.input_key = {"pdf": hashlib.sha256(data).hexdigest()[:12], "climatePages": pages}
    ran.notes.append("임베딩은 모의(MockEmbedding — 두 판 같음, 실호출 0). 대화만 판을 따른다")
    return ran


# ── tr ──


def run_tr(client: Any, options: Options, sink_usd: Callable[[], float]) -> Ran:
    from esgapi.translation.engines.colab_158.texts import DEFAULT_TEXTS as TR_TEXTS
    from esgapi.translation.flows import colab_158
    from esgapi.translation.tools.llm_bridge import DEFAULT_MODELS

    text = fixtures.TRANSLATION_TEXT
    if options.size == "probe":
        text = text.split("[PAGE 2]")[0]
    inputs = colab_158.TranslationRunInputs(
        document=text.encode("utf-8"),
        company_name=fixtures.COMPANY,
        preferred_subject=fixtures.PREFERRED_SUBJECT,
        industry_standards=fixtures.INDUSTRY_STANDARDS,
        glossary_base=fixtures.TRANSLATION_GLOSSARY,
        glossary_final=(),
        part="general",
        compare_ab=False,
        single_style="B",
        peer_group_style_prompt=None,
        page_start=None,
        page_end=None,
        ceo_page_limit=6,
        autofix=True,
        retry=None,
        max_usd=2.0,
        texts=TR_TEXTS,
        models=DEFAULT_MODELS,
    )
    adapters = colab_158.TranslationAdapters(llm=client, tenant=TENANT, mock=False, spent_usd=sink_usd)
    ran = _run(colab_158.FLOW, inputs, adapters)
    ran.input_key = {"text": hashlib.sha256(text.encode()).hexdigest()[:12], "pages": text.count("[PAGE ")}
    ran.source = text
    ran.notes.append("번역은 온도를 싣지 않는다(llm_bridge — 두 판 같음)")
    return ran


# ── dv ──


def run_dv(client: Any, options: Options) -> Ran:
    from esgapi.dataverify.engines.colab_156 import prompts
    from esgapi.dataverify.flows import colab_156
    from esgapi.dataverify.tests.flows import support

    received = support.received()
    if options.size == "probe":
        theme = next(iter(received["data"]))
        received = {**received, "data": {theme: received["data"][theme][:2]}}
    inputs = colab_156.DataverifyInputs(
        received=received,
        tags=Tags(tenant=TENANT, module="dataverify"),
        max_cost_usd=colab_156.StageCosts(plan=1.0, review=1.0, insight=1.0),
        texts=prompts.DEFAULT_TEXTS,
        temperature=_temperature(options, colab_156.TEMPERATURE),  # type: ignore[arg-type]
    )
    ran = _run(colab_156.FLOW, inputs, colab_156.DataverifyAdapters(llm=client))
    ran.input_key = {"received": "esg-app dataverify tests/flows/support.received()", "items": _dv_items(received)}
    return ran


def _dv_items(received: Mapping[str, Any]) -> int:
    return sum(len(v) for v in received["data"].values())


# ── mt ──


def _mt_received(options: Options) -> dict[str, Any]:
    """토픽 둘(§7-3, IR1b 꼴 — esg-app 시험 support.received2: 서브토픽 셋, valueChain 있음). 탐침은 토픽 하나."""
    from esgapi.materiality.tests.flows import support

    body = support.received2()
    if options.size == "probe":
        first = body["topics"][0]["topic"]
        body["topics"] = body["topics"][:1]
        body["subtopicsByTopic"] = {first: body["subtopicsByTopic"][first][:1]}
    return body


def run_mt(client: Any, options: Options, *, part2: bool) -> Ran:
    from esgapi.materiality.engines.colab_155 import constants
    from esgapi.materiality.flows import colab_155, part2_155
    from esgapi.materiality.tests.flows import support

    received = _mt_received(options)
    tags = Tags(tenant=TENANT, module="materiality")
    inputs1 = colab_155.MaterialityInputs(
        received=received,
        tags=tags,
        max_cost_usd=2.0,
        route=colab_155.ROUTE,
        texts=colab_155.DEFAULT_TEXTS,
        temperature=_temperature(options, constants.TEMPERATURE),  # type: ignore[arg-type]
        max_output_tokens=colab_155.MAX_OUTPUT_TOKENS,
    )
    first = _run(colab_155.FLOW, inputs1, colab_155.MaterialityAdapters(llm=client))
    first.input_key = {"received": "esg-app materiality tests/flows/support.received2()", "topics": len(received["topics"])}
    first.notes.append(
        "1부는 colab_155.FLOW 를 바로 — 실행의 흐름 iro_155 는 1부 온도 None 을 FlowInputError 로 거절한다(dry-run 표)"
    )
    if not part2 or first.result is None:
        return first
    (part1_result,) = first.result
    inputs2 = support.SeededInputs(
        received=received,
        part1=part1_result,
        tags=tags,
        max_cost_usd=2.0,
        route=part2_155.ROUTE_PART2,
        texts=colab_155.DEFAULT_TEXTS,
        max_output_tokens=part2_155.MAX_OUTPUT_TOKENS_PART2,
    )
    second = _run(support.PART2_FLOW, inputs2, part2_155.Part2Adapters(llm=client))
    second.input_key = first.input_key
    second.notes = [*first.notes, "2부는 esg-app 시험 support.PART2_FLOW(씨앗 = 이 실행의 1부 결과)"]
    second.result = {"part1": first.result, "part2": second.result} if second.result is not None else None
    if second.error:
        second.result = {"part1": first.result, "part2": None}
    return second


def iro_check(client: Any, options: Options) -> Ran:
    """실행의 흐름 iro_155(1부 → 2부)를 판의 온도로 부른다 — dry-run 에서 「어디서 깨지나」만 본다."""
    from esgapi.materiality.engines.colab_155 import constants
    from esgapi.materiality.flows import colab_155, iro_155, part2_155

    research = colab_155.StageModel(
        route=colab_155.ROUTE,
        temperature=_temperature(options, constants.TEMPERATURE),
        max_output_tokens=colab_155.MAX_OUTPUT_TOKENS,
    )
    inputs = iro_155.IroInputs(
        received=_mt_received(options),
        tags=Tags(tenant=TENANT, module="materiality"),
        max_cost_usd=2.0,
        today=date(2026, 11, 3),
        texts=colab_155.DEFAULT_TEXTS,
        research=research,
        analysis=part2_155.CODE_ANALYSIS,
    )
    return _run(iro_155.FLOW, inputs, iro_155.IroAdapters(llm=client))


# ── s3 ──


def scope3_template(options: Options) -> bytes:
    from esgapi.inventory.tests.engines.scope3.colab_157.support import sheet, workbook, xlsx_bytes

    c1, c2 = fixtures.scope3_rows(3 if options.size == "probe" else None)
    return xlsx_bytes(workbook(sheet("C1_구매품서비스", c1), sheet("C2_자본재", c2), sheet("표지", {1: ["가상"]})))


def run_s3(client: Any, options: Options) -> Ran:
    from esgapi.inventory.engines.scope3.colab_157.ports import NoGeocoder
    from esgapi.inventory.flows import binding_157
    from esgapi.inventory.flows import scope3_colab_157 as colab
    from esgapi.inventory.service.utils import scope3_files
    from esgapi.inventory.tools.xlsx_reader import ReaderProblem, read_workbook

    content = scope3_files.FACTOR_BOOK.read()
    if not isinstance(content, bytes):
        return Ran(error=f"계수 책 없음: {content}")
    book = read_workbook(content)
    if isinstance(book, ReaderProblem):
        return Ran(error=f"계수 책을 못 읽음: {book.kind}")
    template = scope3_template(options)
    inputs = colab.ColabInputs(
        template=template,
        report_year=2025,
        route=binding_157.ROUTE,
        tags=Tags(tenant=TENANT, module="inventory.scope3"),
    )
    ran = _run(colab.FLOW, inputs, colab.ColabAdapters(llm=client, factor_book=book, geocoder=NoGeocoder()))
    c1, c2 = fixtures.scope3_rows(3 if options.size == "probe" else None)
    ran.input_key = {"template": hashlib.sha256(template).hexdigest()[:12], "c1Rows": len(c1) - 2, "c2Rows": len(c2) - 2}
    ran.notes.append("계수 책은 esg-app 이 싣는 책(scope3_files.FACTOR_BOOK, 읽기만). 지오코더 NoGeocoder(실행과 같음)")
    return ran


# ── dr ──


def run_dr(client: Any, options: Options, sink_usd: Callable[[], float]) -> Ran:
    from esgapi.drafting.contracts.wire import accept_outline, outline_sha256
    from esgapi.drafting.flows import draft_v2
    from esgapi.drafting.tests.flows.support import Artifacts

    outline = accept_outline(fixtures.DRAFT_OUTLINE)
    pages = list(fixtures.DRAFT_PAGES[:3] if options.size == "probe" else fixtures.DRAFT_PAGES)
    model = replace(draft_v2.CODE_MODEL, temperature=_temperature(options, draft_v2.TEMPERATURE))
    inputs = draft_v2.DraftInputs(
        project_id=1,
        outline_id=1,
        outline_sha256=outline_sha256(outline),
        outline=outline,
        document_id=1,
        document_etag="e1",
        texts=draft_v2.DEFAULT_TEXTS,
        models=draft_v2.StageModels(map=model, draft=model),
        tags=Tags(tenant=TENANT, module="drafting"),
        max_usd=1.0,
    )
    adapters = draft_v2.DraftAdapters(llm=client, mock=False, spent_usd=sink_usd, artifacts=Artifacts(pages))
    ran = _run(draft_v2.FLOW, inputs, adapters)
    ran.input_key = {"pages": len(pages), "leaves": 3}
    ran.notes.append("산출물 뷰는 esg-app 시험 support.Artifacts(쪽 글 → 문단 블록)")
    return ran


def run_module(module: str, client: Any, options: Options, sink_usd: Callable[[], float]) -> Ran:
    match module:
        case "gw":
            return run_gw(client, options)
        case "tr":
            return run_tr(client, options, sink_usd)
        case "dv":
            return run_dv(client, options)
        case "mt1":
            return run_mt(client, options, part2=False)
        case "mt":
            return run_mt(client, options, part2=True)
        case "s3":
            return run_s3(client, options)
        case "dr":
            return run_dr(client, options, sink_usd)
    raise SystemExit(f"모듈은 {MODULES}: {module}")
