"""원본 대조와 ★ 의 일회용 견줌. pytest 가 모으지 않는다(게이트 밖).

쓰는 법(py/ 에서):
  uv run --frozen --group greenwash python greenwash/tests/notebook_probe.py compare <노트북.ipynb> <PDF>… [--leak-file <보고 파일>…]
  uv run --frozen --group greenwash --with langchain-community==0.4.2 python greenwash/tests/notebook_probe.py star1 <PDF>…
  uv run --frozen --group greenwash --with faiss-cpu==1.15.1 python greenwash/tests/notebook_probe.py star3

compare 는 노트북의 셀 11·14·16·19·22·24 의 소스(`source` 만. `outputs` 는 「새지 않음」 줄에서 세기만 한다)를
하위 프로세스의 메모리에서 실행하고 같은 입력으로 우리 코드와 견준다. 지킴 장치:
- 하위 프로세스에서 돌리고 stdout 을 가둔다. 파일을 쓰지 않는다.
- exec 전에 `socket.socket` 을 예외를 던지는 것으로 바꾸고 환경에서 키·추적 변수를 지운다.
- `langchain_openai`·`langchain_community`·`faiss` 는 가짜 모듈로 끼운다. 끝에 `openai` 가 import 되지 않았음을 단언한다.
노트북의 출력 본문은 어디에도 찍지 않는다. 줄마다 「다른 것 / 견준 수」만 낸다.
"""
import ast
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

PY_ROOT = Path(__file__).resolve().parents[2]
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

CELLS = {"chunk": 11, "judge": 14, "run": 16, "print": 19, "terms": 22, "count": 24}

# 글자 그대로 옮긴 정의(우리 모듈 → 노트북의 셀과 이름). AST 가 같아야 한다.
VERBATIM = {
    "heuristics.py": (14, [
        "cap_text", "cap_evidence_context", "normalize_text_for_llm", "get_page_text", "_FACT_TENSE_RE", "_LABEL_PATTERNS",
        "_PROMO_PHRASES_RE", "_HIGH_RISK_KEYWORDS_RE", "DEFAULT_ENV_KEYWORDS", "build_env_keywords_re", "_ENV_KEYWORDS_RE",
        "set_env_keywords", "has_env_signal", "is_hard_label", "is_promo_like", "is_table_like", "sentence_quality_score_kor",
        "is_low_declarative", "split_sentences_kor", "collect_rule_based_declaratives", "_extract_json_substring",
        "_json_loads_safe", "_as_str_list", "_ensure_polite_ending", "_fallback_fix_paragraph"]),
    "prompts.py": (14, ["PromptPack", "GreenwashingChecker._default_prompts>_NotebookPrompts._default_prompts"]),
    "checker.py": (14, [
        "SAFE_LIMITS_INT", "GWConfig", "GreenwashingChecker.filter_and_rank", "GreenwashingChecker.build_evidence",
        "GreenwashingChecker._is_generic_fix"]),
    "wordcount.py": (24, [
        "normalize_text", "normalize_term", "build_flexible_pattern", "build_patterns_from_terms", "DEFAULT_EXCLUDE_PATTERNS",
        "EXCLUDE_REGEX", "mask_excluded_phrases", "count_green_terms_in_text"]),
    "wordcount.py#terms": (22, ["GREEN_TERMS"]),
    "chunking.py": (11, ["_normalize_for_retrieval", "_ensure_page_metadata"]),
    "parts.py": (16, ["parse_pages", "_clip_pages_to_doc_range"]),
    "parts.py#print": (19, ["build_results_by_part"]),
}

# 지은 답(synthetic). 실제 모델의 답이 아니다.
_FIX_LONG = (
    "이 문장은 감축 목표의 기준연도와 목표연도를 2020년과 2030년처럼 연도로 밝혀야 합니다. 적용 범위를 Scope 1과 Scope 2로 나누어 적어야 합니다. "
    "감축량은 tCO2eq 단위의 수치와 산정 방법을 같이 제시해야 합니다. 제3자 검증 여부를 밝히는 것이 바람직합니다"
)
_FIX_GENERIC_LONG = "용어와 대상과 기간에 대한 설명을 문장 안에 명확히 추가 하는 것이 좋겠다는 의견이며 " * 4


def _ev(decision, **kw):
    d = {"decision": decision, "risk_tags": ["가", "나", "다", "라"], "confirmed_in_evidence": ["사실 하나", "사실 둘"],
         "missing_fields": [], "reason_bullets": ["까닭 하나", "까닭 둘"], "fix_paragraph": _FIX_LONG, "evidence_internal": " 근거 한 줄(p.1) "}
    d.update(kw)
    return d


SCRIPTS = {
    "A-등급과 다시 묻기": {
        "detect": [{"json": []}, {"pick_env": 3}, {"pick_env": 5}, {"pick_env": 1}],
        "eval": [
            {"json": _ev("High")},
            {"json": _ev("Mid", fix_paragraph="범위를 명확히 제시해야 합니다.", reason_bullets=[])},
            {"json": _ev("NonRisk")},
            {"json": _ev("NonRisk", missing_fields=["범위/경계"])},
            {"json": _ev("NonRisk", confirmed_in_evidence=["사실 하나"])},
            {"json": _ev("Mid", fix_paragraph=_FIX_GENERIC_LONG, missing_fields=["정의/기준", "산정 방법"])},
            {"json": _ev("High", fix_paragraph="", missing_fields=[])},
            {"json": _ev("Low")},
        ],
        "repair": [{"json": {"fix_paragraph": _FIX_LONG}}, {"json": {"fix_paragraph": ""}}, "고쳐 쓸 수 없음", {"json": {"fix_paragraph": "짧은 답"}}],
        "low_only": [
            {"json": {"low_reason_bullets": ["하나", "둘", "셋"], "fix_paragraph": "의지를 밝힌 글만으로는 읽는 사람이 실제 성과를 가늠하기 어렵습니다. 수치와 기한이 있는 목표를 글의 앞에 두는 편이 좋겠습니다."}},
            {"json": {"low_reason_bullets": [], "fix_paragraph": "짧은 수정 방향"}},
            "JSON 이 아닌 답",
        ],
    },
    "B-깨진 답과 울타리": {
        "detect": [
            {"pick_env": 2},
            "```json\n[]\n```",
            {"json": {"sentence": "배열이 아닌 답입니다. 온실가스 배출을 줄이겠습니다."}},
            "여기 결과입니다: [{\"sentence\": \"온실가스 배출량을 100% 줄였다고 밝힌 문장입니다.\"}, {\"sentence\": \"   \"}, \"글자열\"] 이상입니다.",
            "[{\"sentence\": ",
            {"pick_env": 3},
        ],
        "eval": [
            {"fenced": _ev("High")},
            "설명을 붙입니다. " + json.dumps(_ev("NonRisk"), ensure_ascii=False) + " 끝.",
            "{\"decision\": \"High\", ",
            {"json": _ev("높음")},
            {"json": {"decision": "High", "risk_tags": "글자열", "confirmed_in_evidence": None, "missing_fields": [1, " ", "둘"], "reason_bullets": [], "fix_paragraph": _FIX_LONG + ".", "evidence_internal": None}},
            {"json": {"decision": "NonRisk", "risk_tags": [], "confirmed_in_evidence": ["하나", "둘"], "missing_fields": [], "reason_bullets": [], "fix_paragraph": ""}},
        ],
        "repair": [{"fenced": {"fix_paragraph": _FIX_LONG}}, {"json": {"fix_paragraph": _FIX_LONG + "."}}],
        "low_only": [{"fenced": {"low_reason_bullets": ["하나"], "fix_paragraph": _FIX_LONG}}, {"json": {"low_reason_bullets": ["하나"], "fix_paragraph": _FIX_LONG + "."}}],
    },
    "C-글자열이 아닌 값": {
        "detect": [{"pick_env": 2}, {"json": [{"sentence": 5}]}, {"pick_env": 2}, {"pick_env": 2}, {"pick_env": 2}],
        "eval": [{"json": _ev("High")}, {"json": _ev(3)}, {"json": _ev("Mid", fix_paragraph=7)}, {"json": _ev("Mid")}, {"json": _ev(["High"])}],
        "repair": [{"json": {"fix_paragraph": 9}}],
        "low_only": [{"json": {"low_reason_bullets": ["하나"], "fix_paragraph": 11}}, {"json": {"low_reason_bullets": ["하나"], "fix_paragraph": _FIX_LONG}}],
    },
}

# 직접 부르는 스캔(셀 16 을 거치지 않고 설정을 바꿔서). (대본 이름, 설정, 근거 허용 쪽을 고르는 법)
DIRECT = [
    ("A-등급과 다시 묻기", {"evidence_k": 5, "max_candidates_per_page": 5, "max_chars_page_text": 800, "report_levels": "High, Mid"}, "even"),
    ("A-등급과 다시 묻기", {"evidence_k": 9, "max_chars_per_chunk": 1200, "max_chars_evidence_total": 800, "report_levels": ["Low"], "debug_pages": "x"}, "none"),
    ("B-깨진 답과 울타리", {"max_candidates_per_page": 0, "use_evidence_failopen": 1, "prompt_version": " v-test "}, "first"),
    ("A-등급과 다시 묻기", {}, "first"),
    ("C-글자열이 아닌 값", {"max_candidates_per_page": 2}, "first"),
    ("A-등급과 다시 묻기", {"env_keywords_extra": "가상상사, 사업장", "include_general_env": True}, "all"),
]

PAGE_INPUTS = [None, "", "  ", "5", "6~14", "25-37,55~74", "9~3", "3,3,4", "a~b", "x", "1~", "~4", "0,1,99999", "-3", "3 ~ 5 , 7",
               "1,,2", "２", [3, "4", 5, "x", None], [], "10-12-14", "3.5", " 7 "]


# ----------------------------------------------------------------------------
# 노트북 읽기
# ----------------------------------------------------------------------------
def cell_sources(notebook_path):
    nb = json.loads(Path(notebook_path).read_text(encoding="utf-8"))
    out = {}
    for i, c in enumerate(nb["cells"]):
        src = c["source"]
        out[i] = "".join(src) if isinstance(src, list) else src
    return out


def cell_output_text(notebook_path):
    nb = json.loads(Path(notebook_path).read_text(encoding="utf-8"))
    parts = []
    for c in nb["cells"]:
        for o in c.get("outputs", []) or []:
            for key in ("text",):
                v = o.get(key)
                if v:
                    parts.append("".join(v) if isinstance(v, list) else v)
            for v in (o.get("data") or {}).values():
                if isinstance(v, (list, str)):
                    parts.append("".join(v) if isinstance(v, list) else v)
    return "\n".join(parts)


def _node(tree_body, dotted):
    name, _, rest = dotted.partition(".")
    for n in tree_body:
        nm = getattr(n, "name", None)
        if nm is None and isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            nm = n.targets[0].id
        if nm == name:
            return _node(n.body, rest) if rest else n
    raise KeyError(dotted)


def _dump(node):
    if isinstance(node, ast.FunctionDef):
        node = ast.FunctionDef(name="_", args=node.args, body=node.body, decorator_list=node.decorator_list, returns=node.returns,
                               type_comment=None, type_params=[])
    return ast.dump(node, include_attributes=False)


# ----------------------------------------------------------------------------
# 하위 프로세스: 노트북의 코드를 메모리에서 돌리고 견준다
# ----------------------------------------------------------------------------
class Row:
    def __init__(self, name):
        self.name = name
        self.compared = 0
        self.diff = 0
        self.notes = []

    def eq(self, a, b, note=None):
        self.compared += 1
        same = a == b and (type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float))))
        if not same:
            self.diff += 1
            if note and len(self.notes) < 8:
                self.notes.append(note)
        return same

    def to_json(self):
        return {"name": self.name, "compared": self.compared, "diff": self.diff, "notes": self.notes}


def _guard():
    import socket

    class _BlockedSocket(socket.socket):  # 함수로 바꾸면 ssl 이 이것을 물려받다 깨진다
        def __init__(self, *a, **k):
            raise RuntimeError("probe: 네트워크는 막혀 있다")

    def _blocked(*a, **k):
        raise RuntimeError("probe: 네트워크는 막혀 있다")

    socket.socket = _BlockedSocket
    socket.create_connection = _blocked
    for k in list(os.environ):
        if k == "OPENAI_API_KEY" or k.startswith("LANGSMITH_") or k.startswith("LANGCHAIN_") or k.startswith("OPENAI_"):
            del os.environ[k]


class _Fakes:
    """가짜 모듈 셋. 노트북 쪽의 대화·검색을 우리 대본·우리 검색으로 잇는다."""

    def __init__(self):
        from greenwash.ports import MockEmbedding
        from greenwash.retrieval import VectorIndex

        fakes = self
        self.script_chat = None       # 지금 시나리오의 ScriptedChat(노트북 쪽 전용)
        self.sent = []                # 노트북이 보낸 메시지 [(role, content), …] 의 목록
        self.chat_kwargs = []
        self.embedding_kwargs = []

        class ChatOpenAI:
            def __init__(self, **kwargs):
                fakes.chat_kwargs.append(dict(kwargs))

            def invoke(self, messages):
                msgs = [({"system": "system", "human": "user"}.get(m.type, m.type), m.content) for m in messages]
                fakes.sent.append(msgs)
                return types.SimpleNamespace(content=fakes.script_chat.respond(msgs[0][1], msgs[1][1]))

        class OpenAIEmbeddings:
            def __init__(self, **kwargs):
                fakes.embedding_kwargs.append(dict(kwargs))

        class FAISS:
            @classmethod
            def from_documents(cls, documents, embedding):
                return VectorIndex.from_documents(documents, MockEmbedding())

        m_openai = types.ModuleType("langchain_openai")
        m_openai.ChatOpenAI = ChatOpenAI
        m_openai.OpenAIEmbeddings = OpenAIEmbeddings
        m_comm = types.ModuleType("langchain_community")
        m_comm.__path__ = []  # 꾸러미처럼 보이게(하위 모듈 import 가 ModuleNotFoundError 로 떨어지게)
        m_vs = types.ModuleType("langchain_community.vectorstores")
        m_vs.FAISS = FAISS
        m_comm.vectorstores = m_vs
        m_faiss = types.ModuleType("faiss")
        sys.modules.update({"langchain_openai": m_openai, "langchain_community": m_comm,
                            "langchain_community.vectorstores": m_vs, "faiss": m_faiss})


def _exec(src, ns):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(src, "<notebook-cell>", "exec"), ns)  # noqa: S102 — 사용자가 허락한 원본 대조(티켓 노트)
    return buf.getvalue()


def _call(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def _safe(fn, *a):
    try:
        return ("ok", fn(*a))
    except Exception as e:
        return ("raise", type(e).__name__, str(e))


_AGG_RE = re.compile(r"후보 집계\(raw=(\d+), hard_drop=(\d+), non_env_drop=(\d+), promo_drop=(\d+), table_drop=(\d+), kept=(\d+)\)")
_DONE_RE = re.compile(r"- 처리 페이지: (\d+) \(실패: (\d+)\)")
_FAIL_RE = re.compile(r"^\[경고\] p\.(\d+) 처리 실패: (.*)$", re.M)


def _parse_stdout(text):
    m = _AGG_RE.search(text)
    d = _DONE_RE.search(text)
    agg = dict(zip(["raw", "hard_dropped", "non_env_dropped", "promo_dropped", "table_dropped", "kept"], map(int, m.groups()))) if m else None
    return {
        "agg": agg,
        "pages": int(d.group(1)) if d else None,
        "failed": int(d.group(2)) if d else None,
        "failed_pages": [{"page": int(p), "error": e} for p, e in _FAIL_RE.findall(text)],
    }


def child(notebook_path, pdf_paths):
    _guard()
    import inspect
    from dataclasses import asdict

    from langchain_core.documents import Document

    from greenwash import checker, chunking, cli, heuristics, parts, prompts, result, wordcount
    from greenwash.pdftext import read_page_texts
    from greenwash.ports import MockEmbedding, ScriptedChat, prompt_kind
    from greenwash.retrieval import VectorIndex

    fakes = _Fakes()
    cells = cell_sources(notebook_path)
    rows = {k: Row(k) for k in ["상수", "옮긴 정의의 AST", "순수 함수", "조각내기", "쪽 입력", "스캔", "낱말 세기"]}
    info = {"pdfs": [], "scenarios": 0, "covered": {"results": 0, "failed_pages": 0, "messages": {}, "levels": {}, "no_evidence": 0}}

    def cover(outcome, chat):
        c = info["covered"]
        c["results"] += len(outcome.results)
        c["failed_pages"] += outcome.failed
        for res in outcome.results:
            c["levels"][res["level"]] = c["levels"].get(res["level"], 0) + 1
        for msgs in chat.sent:
            kind = prompt_kind(msgs[0].content, chat.pack)
            c["messages"][kind] = c["messages"].get(kind, 0) + 1
            if "[근거 없음]" in msgs[1].content:
                c["no_evidence"] += 1

    # ---- 옮긴 정의의 AST ----
    r = rows["옮긴 정의의 AST"]
    for key, (cell, names) in VERBATIM.items():
        ours = ast.parse((PY_ROOT / "greenwash" / key.split("#")[0]).read_text(encoding="utf-8")).body
        theirs = ast.parse(cells[cell]).body
        for nm in names:
            theirs_name, _, ours_name = nm.partition(">")
            r.eq(_dump(_node(theirs, theirs_name)), _dump(_node(ours, ours_name or theirs_name)), f"{key}:{nm}")

    for pdf_i, pdf in enumerate(pdf_paths):
        texts = read_page_texts(pdf)
        docs = [Document(page_content=t, metadata={"page": i}) for i, t in enumerate(texts)]
        n = len(docs)
        ns = {"__name__": "__notebook__", "docs": docs, "openai_api_key": "fake-key-not-real"}

        # ---- 셀 11: 조각내기 ----
        _exec(cells[CELLS["chunk"]], ns)
        r = rows["조각내기"]
        mine = chunking.build_chunks_from_docs(docs)
        theirs = ns["chunks"]
        r.eq(len(mine["chunks"]), len(theirs), f"{Path(pdf).name}: 조각 수")
        for a, b in zip(mine["chunks"], theirs):
            r.eq(a.page_content, b.page_content)
            for key in ("page", "start_index", "source_page", "chunk_index", "chunk_id"):
                r.eq(a.metadata.get(key), b.metadata.get(key), f"메타 {key}")
        r.eq([d.page_content for d in docs], texts, "원래 docs 가 바뀌지 않음")

        # ---- 셀 14: 정의 ----
        _exec(cells[CELLS["judge"]], ns)

        if pdf_i == 0:
            r = rows["상수"]
            for nm in ("_FACT_TENSE_RE", "_PROMO_PHRASES_RE", "_HIGH_RISK_KEYWORDS_RE", "_ENV_KEYWORDS_RE"):
                r.eq(getattr(heuristics, nm).pattern, ns[nm].pattern, nm)
                r.eq(getattr(heuristics, nm).flags, ns[nm].flags, nm + ".flags")
            for nm in ("_LABEL_PATTERNS", "DEFAULT_ENV_KEYWORDS"):
                r.eq(getattr(heuristics, nm), ns[nm], nm)
            r.eq(checker.SAFE_LIMITS_INT, ns["SAFE_LIMITS_INT"], "SAFE_LIMITS_INT")
            r.eq(asdict(checker.GWConfig()), asdict(ns["GWConfig"]()), "GWConfig 기본값")
            for ver in ("2026-01-15-v1", "다른-판"):
                a, b = prompts.default_prompts(ver), ns["GreenwashingChecker"]._default_prompts(ver)
                for f in ("detect_system_prompt", "eval_system_prompt", "repair_fix_prompt", "low_only_prompt"):
                    r.eq(getattr(a, f), getattr(b, f), f"프롬프트 {f} ({ver})")
            r.eq(prompts.PROMPT_VERSION, ns["GWConfig"]().prompt_version, "PROMPT_VERSION")
            sig = inspect.signature(ns["GreenwashingChecker"].__init__).parameters
            r.eq(checker.MODEL_NAME, sig["model_name"].default, "model_name")
            r.eq(checker.TEMPERATURE, sig["temperature"].default, "temperature")
            r.eq(checker.MAX_TOKENS, sig["max_tokens"].default, "max_tokens")
            sig = inspect.signature(ns["build_vectorstore_from_docs"]).parameters
            cd = result.chunk_defaults()
            for k in ("chunk_size", "chunk_overlap", "embedding_model", "embedding_batch_size", "normalize"):
                r.eq(cd[k], sig[k].default, f"조각내기 기본값 {k}")
            r.eq({k: cd[k] for k in ns["result4"]["config"]}, ns["result4"]["config"], "셀 11 의 실행 설정")
            r.eq(fakes.embedding_kwargs[-1], {"model": cd["embedding_model"], "api_key": "fake-key-not-real", "chunk_size": cd["embedding_batch_size"]}, "임베딩 인자")

        # ---- 순수 함수 ----
        r = rows["순수 함수"]
        fns = ["is_hard_label", "has_env_signal", "is_promo_like", "is_table_like", "is_low_declarative", "sentence_quality_score_kor"]
        n_sent = 0
        for d in docs:
            norm_a, norm_b = heuristics.normalize_text_for_llm(d.page_content), ns["normalize_text_for_llm"](d.page_content)
            r.eq(norm_a, norm_b, "normalize_text_for_llm")
            sents = ns["split_sentences_kor"](norm_b)
            r.eq(heuristics.split_sentences_kor(norm_a), sents, "split_sentences_kor")
            for s in sents:
                n_sent += 1
                for f in fns:
                    r.eq(getattr(heuristics, f)(s), ns[f](s), f)
            for cap in (2500, 800):
                pa, pb = heuristics.get_page_text(d, cap), ns["get_page_text"](d, cap)
                r.eq(pa, pb, f"get_page_text({cap})")
                for mx in (3, 5):
                    r.eq(heuristics.collect_rule_based_declaratives(pa, max_n=mx), ns["collect_rule_based_declaratives"](pb, max_n=mx),
                         f"collect_rule_based_declaratives(max_n={mx})")
        if pdf_i == 0:
            junk = ["", "   ", "[]", "{}", "[1, 2]", "앞 [1] 가운데 {\"a\": 1} 뒤", "```json\n{\"a\": [1]}\n```", "{\"a\": 1} 꼬리", "] [", "} {",
                    "{\"a\": {\"b\": 2}}", "null", "\"글\"", "3", "[{\"sentence\": \"가\"}", "말 {\"decision\": \"High\"} 말"]
            for j in junk:
                r.eq(_safe(heuristics._json_loads_safe, j), _safe(ns["_json_loads_safe"], j), "_json_loads_safe")
                r.eq(_safe(heuristics._extract_json_substring, j), _safe(ns["_extract_json_substring"], j), "_extract_json_substring")
            for t in ["", " ", "끝.", "했습니다.", "합니다", "입니다.", "요", "필요함", "좋다.", "습니다. "]:
                r.eq(heuristics._ensure_polite_ending(t), ns["_ensure_polite_ending"](t), "_ensure_polite_ending")
            for mf in [[], None, ["정의/기준"], ["범위"], ["Scope 3"], ["산정 방법"], ["수치"], ["정의", "경계", "방법론", "기타"]]:
                r.eq(heuristics._fallback_fix_paragraph(mf), ns["_fallback_fix_paragraph"](mf), "_fallback_fix_paragraph")
            for v in [None, "가", [], ["가", " ", 3, None, "나", "다", "라"], {"a": 1}]:
                for mx in (0, 2, 12):
                    r.eq(heuristics._as_str_list(v, mx), ns["_as_str_list"](v, mx), "_as_str_list")
            for cfg in [None, {}, {"max_candidates_per_page": "9", "evidence_k": 0, "max_chars_page_text": "x", "debug": 0, "report_levels": [], "prompt_version": "  ",
                               "env_keywords_extra": 3, "include_general_env": "y"}, {"report_levels": 5, "env_keywords_extra": ["a", " "], "batch_eval": 1},
                        {"report_levels": " High ,", "max_chars_per_chunk": 99999, "debug_pages": -1, "unknown": 1}]:
                r.eq(checker.sanitize_config_dict(cfg), ns["sanitize_config_dict"](cfg), "sanitize_config_dict")
            for t in ["", "가" * 10, "가" * 1601, "  " + "나" * 2000 + "  "]:
                r.eq(heuristics.cap_evidence_context(t), ns["cap_evidence_context"](t), "cap_evidence_context")
                r.eq(heuristics.cap_text(t, 5), ns["cap_text"](t, 5), "cap_text")

        # ---- 셀 16·19: 쪽 입력과 스캔 ----
        r_in, r_scan = rows["쪽 입력"], rows["스캔"]
        third = max(1, n // 3)
        triples = [
            ("A-등급과 다시 묻기", ("1~%d" % min(n, 12), "%d-%d" % (min(n, 8), min(n, 20)), "1~%d" % n)),
            ("B-깨진 답과 울타리", ("", "%d~1" % third, "%d ~ %d, %d, 99999" % (third, 2 * third, n))),
            ("C-글자열이 아닌 값", ("3,3,x", "", "%d~%d" % (2 * third, n))),
            ("A-등급과 다시 묻기", ("", " ", "0, 99999")),
        ]
        for script_name, (ceo, company, climate) in triples:
            info["scenarios"] += 1
            answers = iter([ceo, company, climate])
            ns["input"] = lambda prompt="": next(answers)
            fakes.script_chat, fakes.sent = ScriptedChat(SCRIPTS[script_name]), []
            ns.pop("manual_results", None)
            out_text = _exec(cells[CELLS["run"]], ns)
            _exec(cells[CELLS["print"]], ns)

            section_pages, manual_pages = parts.build_section_pages(ceo, company, climate, n)
            r_in.eq(section_pages, ns["section_pages"], "section_pages")
            r_in.eq(manual_pages, ns["manual_pages"], "manual_pages")
            chat = ScriptedChat(SCRIPTS[script_name])
            outcome = cli.run_scan(docs, manual_pages, llm=chat, embeddings=MockEmbedding())
            cover(outcome, chat)
            where = f"{Path(pdf).name} / {script_name}"
            r_scan.eq(len(outcome.results), len(ns["manual_results"]), where + ": 결과 수")
            r_scan.eq(outcome.results, ns["manual_results"], where + ": 결과 목록")
            r_scan.eq([[(m.role, m.content) for m in msgs] for msgs in chat.sent], fakes.sent, where + ": 보낸 메시지")
            r_in.eq(parts.build_results_by_part(outcome.results, section_pages), ns["results_by_part"], "results_by_part")
            if manual_pages:
                p = _parse_stdout(out_text)
                r_scan.eq(outcome.agg, p["agg"], where + ": 후보 집계")
                r_scan.eq(outcome.failed, p["failed"], where + ": 실패 수")
                r_scan.eq(len(outcome.pages_norm), p["pages"], where + ": 처리 쪽 수")
                r_scan.eq(outcome.failed_pages, p["failed_pages"], where + ": 실패 쪽과 까닭")
            else:
                r_scan.eq("[경고] 점검할 페이지가 없습니다." in out_text, True, where + ": 빈 입력")

        for x in PAGE_INPUTS:
            r_in.eq(_safe(parts.parse_pages, x), _safe(ns["parse_pages"], x), f"parse_pages({x!r})")
        r_in.eq(_safe(parts.parse_pages, 3), _safe(ns["parse_pages"], 3), "parse_pages(3)")
        for pages in ([], [0, 1, n, n + 1, 5, 5], ["2", "x", None, 3.7], [1]):
            for total in (n, 0, -1):
                r_in.eq(parts._clip_pages_to_doc_range(pages, total), ns["_clip_pages_to_doc_range"](pages, total), "_clip_pages_to_doc_range")

        # ---- 직접 부르는 스캔(설정을 바꿔서) ----
        their_index = ns["vectorstore"]
        my_index = VectorIndex.from_documents(mine["chunks"], MockEmbedding())
        pages = list(range(1, n + 1, max(1, n // 40))) + [0, n + 5, "x"]
        for script_name, config, allow in DIRECT:
            info["scenarios"] += 1
            allowed = {"even": [p for p in range(1, n + 1) if p % 2 == 0], "none": None, "first": [1, "y", 0], "all": list(range(1, n + 1))}[allow]
            pack = prompts.default_prompts(str(config.get("prompt_version", prompts.PROMPT_VERSION)).strip())
            fakes.script_chat, fakes.sent = ScriptedChat(SCRIPTS[script_name], pack), []
            (their_results, out_text) = _call(ns["scan_report_greenwashing_pages"], docs, pages, evidence_allowed_pages=allowed, config=dict(config),
                                              vectorstore=their_index, openai_api_key="fake-key-not-real", enable_cache=False)
            chat = ScriptedChat(SCRIPTS[script_name], pack)
            outcome = checker.scan_report_greenwashing_pages(docs, pages, evidence_allowed_pages=allowed, config=dict(config), vectorstore=my_index, llm=chat)
            cover(outcome, chat)
            where = f"{Path(pdf).name} / 직접 {script_name} {sorted(config)}"
            p = _parse_stdout(out_text)
            r_scan.eq(outcome.results, their_results, where + ": 결과 목록")
            r_scan.eq([[(m.role, m.content) for m in msgs] for msgs in chat.sent], fakes.sent, where + ": 보낸 메시지")
            r_scan.eq(outcome.agg, p["agg"], where + ": 후보 집계")
            r_scan.eq(outcome.failed, p["failed"], where + ": 실패 수")
            r_scan.eq(outcome.failed_pages, p["failed_pages"], where + ": 실패 쪽과 까닭")
            r_scan.eq(heuristics._ENV_KEYWORDS_RE.pattern, ns["_ENV_KEYWORDS_RE"].pattern, where + ": 환경 낱말 정규식(설정 뒤)")
        # 환경 낱말을 넓힌 설정이 다음 문서로 새지 않게 둘 다 되돌린다.
        heuristics.set_env_keywords()
        ns["set_env_keywords"]()
        for bad in ({"vectorstore": None, "llm": object()}, {"vectorstore": my_index, "llm": None}):
            their_kw = {"vectorstore": bad["vectorstore"], "openai_api_key": "k" if bad["llm"] else None, "enable_cache": False}
            a = _safe(lambda: checker.scan_report_greenwashing_pages(docs, [1], **bad))
            b = _safe(lambda: _call(ns["scan_report_greenwashing_pages"], docs, [1], **their_kw))
            r_scan.eq(a[:2], b[:2], "빠진 인자의 예외 종류")
        for empty_pages in ([], [0, n + 1]):
            a = checker.scan_report_greenwashing_pages(docs, empty_pages, vectorstore=my_index, llm=ScriptedChat(SCRIPTS["A-등급과 다시 묻기"]))
            b, _ = _call(ns["scan_report_greenwashing_pages"], docs, empty_pages, vectorstore=their_index, openai_api_key="k", enable_cache=False)
            r_scan.eq(a.results, b, "점검 쪽이 없을 때")

        # ---- 셀 22·24: 낱말 세기 ----
        r = rows["낱말 세기"]
        _exec(cells[CELLS["terms"]], ns)
        _exec(cells[CELLS["count"]], ns)
        if pdf_i == 0:
            rc = rows["상수"]
            rc.eq(wordcount.GREEN_TERMS, ns["GREEN_TERMS"], "GREEN_TERMS")
            rc.eq(wordcount.DEFAULT_EXCLUDE_PATTERNS, ns["DEFAULT_EXCLUDE_PATTERNS"], "DEFAULT_EXCLUDE_PATTERNS")
            rc.eq([(x.pattern, x.flags) for x in wordcount.EXCLUDE_REGEX], [(x.pattern, x.flags) for x in ns["EXCLUDE_REGEX"]], "EXCLUDE_REGEX")
            for term in ns["GREEN_TERMS"] + ["", " ", "탄소 중립", "net-zero", "AB"]:
                rc.eq(wordcount.build_flexible_pattern(term).pattern, ns["build_flexible_pattern"](term).pattern, f"낱말 패턴 {term!r}")
        total, per_page = wordcount.count_green_terms_in_docs(docs, wordcount.GREEN_TERMS)
        r.eq(total, ns["total_counts"], f"{Path(pdf).name}: 전체 빈도")
        r.eq(list(total.most_common()), list(ns["total_counts"].most_common()), "전체 빈도의 차례")
        r.eq(len(per_page), len(ns["per_page_counts"]), "빈도가 있는 쪽 수")
        for a, b in zip(per_page, ns["per_page_counts"]):
            r.eq(a, b, "쪽별 빈도")
            r.eq(list(a[1].most_common()), list(b[1].most_common()), "쪽별 빈도의 차례")

        info["pdfs"].append({"file": Path(pdf).name, "pages": n, "chunks": len(theirs), "sentences": n_sent})

    assert "openai" not in sys.modules, "openai 가 import 됐다"
    info["openai_imported"] = "openai" in sys.modules
    info["chat_ctor_kwargs"] = sorted({json.dumps({k: v for k, v in kw.items()}, sort_keys=True, ensure_ascii=False) for kw in fakes.chat_kwargs})
    return {"rows": [r.to_json() for r in rows.values()], "info": info}


# ----------------------------------------------------------------------------
# 새지 않음: 노트북에 저장된 출력의 글이 이 밴드의 파일에 있는가 (세기만 한다)
# ----------------------------------------------------------------------------
_HANGUL = re.compile(r"[가-힣]")
WINDOW = 20


def leak_check(notebook_path, files):
    cells = cell_sources(notebook_path)
    source_all = "\n".join(cells.values())
    out_text = cell_output_text(notebook_path)
    lines = {ln.strip() for ln in out_text.splitlines()}
    lines = {ln for ln in lines if len(ln) > 20 and _HANGUL.search(ln) and ln not in source_all}
    windows = set()
    for ln in lines:
        for i in range(0, len(ln) - WINDOW + 1):
            w = ln[i:i + WINDOW]
            if len(_HANGUL.findall(w)) >= 8 and w not in source_all:
                windows.add(w)
    hits = []
    for f in files:
        try:
            text = Path(f).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        line_hits = sum(1 for ln in lines if ln in text)
        window_hits = sum(1 for i in range(0, max(0, len(text) - WINDOW + 1)) if text[i:i + WINDOW] in windows)
        if line_hits or window_hits:
            hits.append({"file": str(f), "lines": line_hits, "windows": window_hits})
    return {"output_lines": len(lines), "output_windows": len(windows), "files": len(files), "hits": hits}


def band_files():
    """이 밴드의 파일: 기준 커밋에서 바뀐 추적 파일과 아직 추적하지 않는(무시되지 않는) 파일."""
    root = PY_ROOT.parent
    base = os.environ.get("B", "f1b2c77")
    a = subprocess.run(["git", "diff", "--name-only", base], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
    b = subprocess.run(["git", "ls-files", "-o", "--exclude-standard"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted({str(root / x) for x in a + b if x and not x.endswith("uv.lock")})


# ----------------------------------------------------------------------------
# ★1, ★3
# ----------------------------------------------------------------------------
def star1(pdf_paths):
    """`PyPDFLoader` 의 쪽 글과 우리 `pdftext` 의 쪽 글을 전 쪽에서 견준다(langchain-community 가 있어야 한다)."""
    import langchain_community
    import pypdf
    from langchain_community.document_loaders import PyPDFLoader

    from greenwash.pdftext import read_page_texts

    print(f"langchain-community {langchain_community.__version__}, pypdf {pypdf.__version__}")
    bad = 0
    for pdf in pdf_paths:
        theirs = PyPDFLoader(str(pdf)).load()
        mine = read_page_texts(pdf)
        raw = [p.extract_text() for p in pypdf.PdfReader(str(pdf)).pages]
        diff = sum(1 for a, b in zip(mine, theirs) if a != b.page_content) + abs(len(mine) - len(theirs))
        raw_diff = sum(1 for a, b in zip(raw, theirs) if a != b.page_content)
        pages_ok = [d.metadata.get("page") for d in theirs] == list(range(len(theirs)))
        bad += diff + (0 if pages_ok else 1)
        print(f"{Path(pdf).name}: 쪽 {len(theirs)} | 우리 pdftext 와 다른 쪽 {diff} / {len(theirs)} | "
              f"뒤처리 없는 extract_text() 와 다른 쪽 {raw_diff} / {len(theirs)} | page 가 0부터 차례로: {pages_ok}")
    return 1 if bad else 0


def star3(seed=20261013, dim=1536, n_chunks=500, n_queries=100, k=12):
    """길이 1 로 맞춘 float32 벡터에서 우리 정확 L2 차례와 `faiss.IndexFlatL2` 의 차례를 견준다(faiss-cpu 가 있어야 한다)."""
    import faiss
    import numpy as np

    from greenwash.retrieval import l2_order

    rng = np.random.default_rng(seed)
    bad = 0
    for label, make in (("무작위", lambda: rng.standard_normal((n_chunks, dim))),
                        ("가까운 것이 많은 꼴", lambda: rng.standard_normal((1, dim)) + 0.05 * rng.standard_normal((n_chunks, dim)))):
        m = make().astype(np.float32)
        m /= np.linalg.norm(m, axis=1, keepdims=True)
        q = (m[rng.integers(0, n_chunks, n_queries)] + 0.3 * rng.standard_normal((n_queries, dim)).astype(np.float32)).astype(np.float32)
        q /= np.linalg.norm(q, axis=1, keepdims=True)
        index = faiss.IndexFlatL2(dim)
        index.add(m)
        same, ties = 0, 0
        for i in range(n_queries):
            dist, idx = index.search(q[i:i + 1], k)
            if [int(x) for x in idx[0]] == l2_order(m, q[i], k):
                same += 1
            if len(set(float(x) for x in dist[0])) < k:
                ties += 1
        bad += n_queries - same
        print(f"{label}: 상위 {k} 의 차례가 같은 물음 {same} / {n_queries}, 상위 {k} 안에 같은 거리가 있는 물음 {ties} / {n_queries}")
    # 완전히 같은 벡터(동점)
    m = np.zeros((6, 8), dtype=np.float32)
    m[:, 0] = 1.0
    m[3, 0], m[3, 1] = 0.0, 1.0
    index = faiss.IndexFlatL2(8)
    index.add(m)
    q = m[0]
    _, idx = index.search(q[None, :], 6)
    print(f"동점(같은 벡터 다섯): faiss {[int(x) for x in idx[0]]} / 우리 {l2_order(m, q, 6)}")
    print(f"faiss {faiss.__version__}, numpy {np.__version__}")
    return 1 if bad else 0


# ----------------------------------------------------------------------------
def main(argv):
    if len(argv) >= 2 and argv[0] == "--child":
        report = child(argv[1], argv[2:])
        sys.__stdout__.write("\n@@PROBE@@" + json.dumps(report, ensure_ascii=False) + "\n")
        return 0
    if argv and argv[0] == "star1":
        return star1(argv[1:])
    if argv and argv[0] == "star3":
        return star3()
    if len(argv) < 3 or argv[0] != "compare":
        print(__doc__)
        return 2
    leak_extra = []
    args = []
    it = iter(argv[1:])
    for a in it:
        if a == "--leak-file":
            leak_extra.append(next(it))
        else:
            args.append(a)
    notebook, pdfs = args[0], args[1:]
    env = {k: v for k, v in os.environ.items()
           if not (k == "OPENAI_API_KEY" or k.startswith("OPENAI_") or k.startswith("LANGSMITH_") or k.startswith("LANGCHAIN_"))}
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", notebook, *pdfs],
                          capture_output=True, text=True, env=env, cwd=str(PY_ROOT))
    if proc.returncode != 0 or "@@PROBE@@" not in proc.stdout:
        # 하위 프로세스의 stdout 은 찍지 않는다(노트북의 print 가 섞여 있을 수 있다). 오류의 꼬리만.
        print("하위 프로세스 실패. stderr 의 꼬리:\n" + proc.stderr[-3000:])
        return 1
    report = json.loads(proc.stdout.split("@@PROBE@@", 1)[1])
    leak = leak_check(notebook, band_files() + leak_extra)
    bad = 0
    print("| 줄 | 다른 것 / 견준 수 |")
    print("|---|---|")
    for r in report["rows"]:
        bad += r["diff"]
        print(f"| {r['name']} | {r['diff']} / {r['compared']} |")
        for note in r["notes"]:
            print(f"|   ↳ {note} | |")
    n_hit = sum(h["lines"] + h["windows"] for h in leak["hits"])
    bad += n_hit
    print(f"| 새지 않음 | {n_hit} / 저장된 출력의 줄 {leak['output_lines']} · 20자 창 {leak['output_windows']} × 파일 {leak['files']} |")
    for h in leak["hits"]:
        print(f"|   ↳ {h['file']}: 줄 {h['lines']}, 창 {h['windows']} | |")
    for p in report["info"]["pdfs"]:
        print(f"{p['file']}: 쪽 {p['pages']}, 조각 {p['chunks']}, 문장 {p['sentences']}")
    print(f"시나리오 {report['info']['scenarios']}, openai import: {report['info']['openai_imported']}, ChatOpenAI 인자: {report['info']['chat_ctor_kwargs']}")
    c = report["info"]["covered"]
    print(f"스캔이 지나간 것(우리 쪽 합계): 결과 {c['results']}건 {c['levels']}, 실패한 쪽 {c['failed_pages']}, 보낸 메시지 {c['messages']}, 근거 없음으로 나간 물음 {c['no_evidence']}")
    print(f"PROBE_DIFF={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
