"""버전별 비교 리포트와 차트 (docs/EVAL.md 4, 5장).

    python eval/report.py                 results/에 있는 모든 split
    python eval/report.py --split eval    eval 절만 (집계 수치만 쓴다)

results/*.summary.json에서 split별로 같은 provider 안에서 main 모델마다 버전별 최신 summary를 골라
results/report.md와 results/charts/<split>_<모델>_*.png를 만든다. 모델이 다르면 같은 버전도 다른 실험이라
모델마다 절을 따로 두고 서로 섞지 않는다 (모델은 summary meta의 models.configured.main 기준). 요청별 정보는
summary에 없으므로 eval 리포트도 집계만 담는다 (CLAUDE.md 절대 규칙 1). LangSmith 없이 로컬 결과만으로 만들어진다.

차트: 정체성은 버전이라 범주 색 슬롯 1~3(파랑, 주황, 청록)을 v0, v1, v2에 고정한다. 청록은 밝은 배경에서
대비가 3:1보다 낮아서(2.74) 막대마다 값 라벨을 붙이고 report.md에 같은 수치의 표를 둔다.
"""

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Patch, PathPatch  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402

from app.schemas import KST  # noqa: E402
from eval.evaluators import STRUCTURED_FIELDS  # noqa: E402
from eval.harness import RESULTS_DIR  # noqa: E402

VERSIONS = ("v0", "v1", "v2")
VERSION_NAMES = {"v0": "v0 LLM 단독", "v1": "v1 + Tool, 병렬 에이전트", "v2": "v2 + 이력 RAG, Validator"}

# 범주 색 슬롯 1~3 (blue, orange, aqua). 슬롯 순서가 색각 이상 안전성을 만든다.
VERSION_COLORS = {"v0": "#2a78d6", "v1": "#eb6834", "v2": "#1baf7a"}
SURFACE, GRID = "#fcfcfb", "#e6e5e1"
TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED = "#0b0b0b", "#52514e", "#8a8985"
_FONT_CANDIDATES = ("Apple SD Gothic Neo", "AppleGothic", "NanumGothic", "Noto Sans CJK KR", "Malgun Gothic")


# ---------------------------------------------------------------------------
# summary 고르기
# ---------------------------------------------------------------------------


def load_summaries(results_dir: Path) -> list[dict[str, Any]]:
    summaries = []
    for path in sorted(results_dir.glob("*.summary.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8")))
    return summaries


def select_latest(summaries: list[dict[str, Any]], split: str, provider: str | None) -> tuple[str | None, dict[str, dict]]:
    """split의 provider(없으면 가장 최근 실행의 provider)에서 버전별 최신 summary를 고른다."""
    pool = [s for s in summaries if s["meta"]["split"] == split]
    if not pool:
        return None, {}
    if provider is None:
        provider = max(pool, key=lambda s: s["meta"]["started_at"])["meta"]["provider"]
    chosen: dict[str, dict] = {}
    for summary in pool:
        meta = summary["meta"]
        if meta["provider"] != provider:
            continue
        current = chosen.get(meta["version"])
        if current is None or meta["started_at"] > current["meta"]["started_at"]:
            chosen[meta["version"]] = summary
    return provider, chosen


def main_model(summary: dict[str, Any]) -> str:
    """summary meta에 설정된 main 모델. 이 기록이 없던 시절의 summary는 '(모델 미기록)'이다."""
    return summary["meta"].get("models", {}).get("configured", {}).get("main") or "(모델 미기록)"


def model_slug(model: str) -> str:
    """차트 파일명에 쓰는 모델 이름. 'qwen3:8b'는 'qwen3-8b'가 된다."""
    return re.sub(r"[^A-Za-z0-9.]+", "-", model).strip("-") or "model"


def select_by_model(
    summaries: list[dict[str, Any]], split: str, provider: str | None
) -> tuple[str | None, dict[str, dict[str, dict]]]:
    """같은 provider 안에서 main 모델마다 버전별 최신 summary를 고른다.

    모델이 다르면 같은 버전도 다른 실험이다. 강한 모델에서는 v0도 잘 맞혀 구조의 효과가 안 보이고(EVAL.md 6장),
    약한 모델에서는 구조가 메우는 차이가 보인다. 그래서 모델을 섞어 최신 하나만 고르지 않고 모델마다 따로 고른다.
    모델 순서는 처음 측정한 순서다.
    """
    provider, _ = select_latest(summaries, split, provider)
    if provider is None:
        return None, {}
    pool = [s for s in summaries if s["meta"]["split"] == split and s["meta"]["provider"] == provider]
    first_seen: dict[str, str] = {}
    for summary in pool:
        model, started = main_model(summary), summary["meta"]["started_at"]
        first_seen[model] = min(first_seen.get(model, started), started)
    ordered = sorted(first_seen, key=first_seen.__getitem__)
    return provider, {
        model: select_latest([s for s in pool if main_model(s) == model], split, provider)[1] for model in ordered
    }


# ---------------------------------------------------------------------------
# 표
# ---------------------------------------------------------------------------


def _pct(v):
    return "-" if v is None else f"{v * 100:.1f}%"


def _fixed(digits):
    return lambda v: "-" if v is None else f"{v:.{digits}f}"


def _int(v):
    return "-" if v is None else f"{v:,.0f}"


def _usd(v):
    return "-" if v is None else f"{v:.5f}"


def _count_of(summary: dict[str, Any]) -> str:
    """자기모순 실행 수 / 따진 실행 수. 이 지표가 없던 시절의 summary는 '-'."""
    metrics = summary["metrics"]
    if metrics.get("self_contradiction_n") is None:
        return "-"
    return f"{metrics['self_contradiction_count']} / {metrics['self_contradiction_n']}"


def _delta(kind: str, a, b) -> str:
    if a is None or b is None:
        return "-"
    if kind == "pp":
        return f"{(b - a) * 100:+.1f}%p"
    if kind == "usd":
        return f"{b - a:+.5f}"
    if kind == "int":
        return f"{b - a:+,.0f}"
    return f"{b - a:+.1f}"


# (표시 이름, summary에서 값을 꺼내는 함수, 값 표기, 변화 표기 종류)
HEADLINE_ROWS = [
    ("판정 정확도", lambda s: s["metrics"]["verdict_accuracy"], _pct, "pp"),
    ("위반 조항 F1", lambda s: s["metrics"]["violation_f1"], _pct, "pp"),
    ("일관성 (반복 간 판정 일치)", lambda s: s["metrics"]["consistency"], _pct, "pp"),
    ("판정-위반 자기모순 (낮을수록 좋음)", lambda s: s["metrics"].get("self_contradiction_rate"), _pct, "pp"),
    ("환각 조항 수", lambda s: s["metrics"]["hallucinated_count"], _int, "int"),
    ("RAG 재현율@3", lambda s: s["metrics"]["rag_recall_at_3"], _pct, "pp"),
    ("지연 p95 (초)", lambda s: s["latency"]["p95_s"], _fixed(1), "abs"),
    ("토큰 / 실행 (입력 + 출력)", lambda s: s["usage"]["tokens_per_run"], _int, "int"),
    ("예상 비용 / 실행 (USD)", lambda s: s["cost"]["per_run_usd"], _usd, "usd"),
]

DETAIL_ROWS = [
    ("판정 정확도", lambda s: s["metrics"]["verdict_accuracy"], _pct),
    ("위반 정밀도", lambda s: s["metrics"]["violation_precision"], _pct),
    ("위반 재현율", lambda s: s["metrics"]["violation_recall"], _pct),
    ("위반 F1", lambda s: s["metrics"]["violation_f1"], _pct),
    ("구조화 정확도 (필드 단위)", lambda s: s["metrics"]["structured_accuracy"], _pct),
    ("환각 조항 수", lambda s: s["metrics"]["hallucinated_count"], _int),
    ("오인용 수", lambda s: s["metrics"]["miscited_count"], _int),
    ("오인용 수 (엄격, 허용 목록 없음)", lambda s: s["metrics"]["miscited_strict_count"], _int),
    ("위험도 일치율", lambda s: s["metrics"]["risk_accuracy"], _pct),
    ("긴급 판정 정확도", lambda s: s["metrics"]["emergency_accuracy"], _pct),
    ("판정-위반 자기모순 (낮을수록 좋음)", lambda s: s["metrics"].get("self_contradiction_rate"), _pct),
    ("  자기모순 실행 수 / 따진 실행 수", lambda s: _count_of(s), str),
    ("RAG 재현율@3", lambda s: s["metrics"]["rag_recall_at_3"], _pct),
    ("일관성", lambda s: s["metrics"]["consistency"], _pct),
    ("대안 품질 (Judge 1~5)", lambda s: s["metrics"]["alternative_score"], _fixed(2)),
    ("지연 평균 (초)", lambda s: s["latency"]["mean_s"], _fixed(1)),
    ("지연 p95 (초)", lambda s: s["latency"]["p95_s"], _fixed(1)),
    ("입력 토큰 / 실행", lambda s: s["usage"]["input_tokens_per_run"], _int),
    ("출력 토큰 / 실행", lambda s: s["usage"]["output_tokens_per_run"], _int),
    ("  그중 reasoning 토큰 합계", lambda s: s["usage"]["reasoning_tokens_total"], _int),
    ("LLM 호출 / 실행", lambda s: s["usage"]["llm_calls_per_run"], _fixed(2)),
    ("예상 비용 / 실행 (USD)", lambda s: s["cost"]["per_run_usd"], _usd),
    ("Validator 재작성, Tool 재시도 등 (합계)", lambda s: sum(s["counters"].values()) if s["counters"] else 0, _int),
    ("오류 실행 수", lambda s: s["metrics"]["n_errors"], _int),
]


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def headline_table(chosen: dict[str, dict]) -> str:
    rows = []
    for name, get, fmt, kind in HEADLINE_ROWS:
        values = {v: get(chosen[v]) if v in chosen else None for v in VERSIONS}
        rows.append([name, *[fmt(values[v]) if v in chosen else "-" for v in VERSIONS], _delta(kind, values["v0"], values["v2"])])
    return _table(["지표", "v0", "v1", "v2", "변화 (v0 → v2)"], rows)


def detail_table(chosen: dict[str, dict]) -> str:
    rows = [[name, *[fmt(get(chosen[v])) if v in chosen else "-" for v in VERSIONS]] for name, get, fmt in DETAIL_ROWS]
    return _table(["지표", "v0", "v1", "v2"], rows)


def field_table(chosen: dict[str, dict]) -> str:
    rows = []
    for name in STRUCTURED_FIELDS:
        cells = []
        for v in VERSIONS:
            field = chosen[v]["metrics"]["structured_by_field"][name] if v in chosen else None
            cells.append("-" if field is None or field["accuracy"] is None else f"{_pct(field['accuracy'])} ({field['correct']}/{field['total']})")
        rows.append([name, *cells])
    return _table(["필드", "v0", "v1", "v2"], rows)


def meta_table(chosen: dict[str, dict]) -> str:
    def commit(s):
        git = s["meta"]["git"]
        short = (git["commit"] or "?")[:7]
        return short + (" (dirty)" if git["dirty"] else "")

    def prompts(s):
        return ", ".join(f"{name.removesuffix('.md')} {sha[:8]}" for name, sha in s["meta"]["prompt_sha256"].items())

    items = [
        ("실행 시작", lambda s: s["meta"]["started_at"][:19]),
        ("git 커밋", commit),
        ("main 모델 (설정)", lambda s: s["meta"]["models"]["configured"]["main"]),
        ("관측된 모델", lambda s: ", ".join(s["meta"]["models"]["observed"]) or "-"),
        ("Judge 모델 (독립 여부)", lambda s: f"{s['meta']['judge']['model']} ({'독립' if s['meta']['judge']['independent'] else '비독립'})" if s["meta"]["judge"]["enabled"] else "사용 안 함"),
        ("요청 x 반복", lambda s: f"{s['meta']['n_requests']} x {s['meta']['reps']}"),
        ("프롬프트 해시", prompts),
        ("OLLAMA_NUM_PARALLEL", lambda s: s["meta"].get("ollama_num_parallel") or "-"),
        ("LangSmith 추적", lambda s: s["meta"]["langsmith"]["project"] or "끔"),
        ("warm-up", lambda s: s["meta"]["warmup"]),
    ]
    rows = [[name, *[fn(chosen[v]) if v in chosen else "-" for v in VERSIONS]] for name, fn in items]
    return _table(["항목", "v0", "v1", "v2"], rows)


# ---------------------------------------------------------------------------
# 주석, 경고
# ---------------------------------------------------------------------------

_COMPARABLE_DATA = ("change_policy.md", "rules.json", "cmdb.json", "change_calendar.json", "open_incidents.json")


def comparability_warnings(chosen: dict[str, dict]) -> list[str]:
    """버전끼리 비교가 공정하지 않을 수 있는 차이. 커밋 차이는 단계마다 당연해서 표에만 남긴다."""
    if len(chosen) < 2:
        return []
    metas = {v: s["meta"] for v, s in chosen.items()}
    warnings = []

    def differs(label, get):
        values = {v: get(m) for v, m in metas.items()}
        if len({json.dumps(x, sort_keys=True) for x in values.values()}) > 1:
            warnings.append(f"{label}이(가) 버전마다 다르다: " + ", ".join(f"{v}={x}" for v, x in values.items()))

    differs("main 모델", lambda m: m["models"]["configured"]["main"])
    differs("Judge 모델", lambda m: m["judge"]["model"])
    differs("요청 수", lambda m: m["n_requests"])
    differs("반복 횟수", lambda m: m["reps"])
    differs("LangSmith 추적 여부", lambda m: m["langsmith"]["enabled"])
    differs("Ollama num_ctx", lambda m: m["ollama_num_ctx"])
    differs("정답셋 파일", lambda m: next((sha for name, sha in m["data_sha256"].items() if name.startswith("requests_")), None))
    differs("규정/CMDB/캘린더/티켓 데이터", lambda m: {k: m["data_sha256"].get(k) for k in _COMPARABLE_DATA})
    return warnings


def notes(chosen: dict[str, dict]) -> list[str]:
    lines = []
    first = next(iter(chosen.values()))
    runs = first["metrics"]["n_runs"]
    lines.append(
        f"p95는 nearest-rank로 계산한다. 실행이 {runs}건이면 순위가 {max(1, -(-95 * runs // 100))}번째라 "
        "n=10이면 최댓값과 같다. 표본이 작아서 수치 차이를 통계적 결론으로 읽지 않는다."
    )
    lines.append("v0, v1은 과거 이력을 받지 않아 위험도를 CMDB 등급으로만 정한다. 이력 기반 상향과 RAG 재현율은 v2의 RAG가 더하는 정보다.")
    if any(not s["meta"]["judge"]["independent"] and s["meta"]["judge"]["enabled"] for s in chosen.values()):
        lines.append("Judge 모델이 시스템 모델과 같은 실행은 대안 품질이 비독립 평가다. 단계별 추세를 보는 용도이고, 최종 평가(anthropic, opus)와 직접 비교하지 않는다.")
    if any(s["meta"]["provider"] == "ollama" for v, s in chosen.items() if v != "v0"):
        lines.append(
            "Ollama 서버가 요청을 한 건씩 처리하면(OLLAMA_NUM_PARALLEL=1) v1의 policy_agent와 impact_agent가 보내는 LLM 호출이 "
            "서버에서 줄을 서서 병렬의 지연 이득이 이 수치에 보이지 않는다. 최종 평가(anthropic)는 실제로 병렬이다."
        )
    if any(s["usage"]["reasoning_tokens_total"] for s in chosen.values()):
        lines.append("출력 토큰에는 모델의 reasoning(thinking) 토큰이 포함된다. 지연과 비용에도 같이 반영된다.")
    if any(s["flags"]["possible_truncation_runs"] for s in chosen.values()):
        lines.append("입력이 컨텍스트 한계에 가까운 실행이 있었다. 프롬프트가 잘렸을 수 있어 해당 버전의 수치를 그대로 믿지 않는다.")
    if any(s["metrics"]["n_errors"] for s in chosen.values()):
        lines.append("오류 난 실행은 모든 지표에서 오답으로 센다. 오류 실행 수는 전체 지표 표에 있다.")
    if any(s["meta"].get("rescored") for s in chosen.values()):
        lines.append("meta에 rescored가 있는 실행은 측정 뒤 저장된 결과(jsonl)로 지표를 다시 계산해 더했다. LLM은 다시 부르지 않았다.")
    lines.append("판정-위반 자기모순은 pred 위반 목록의 구분(금지/보완)으로 규정 1장 판정을 다시 계산해 pred 판정과 다른 실행의 비율이다. v1, v2는 판정을 코드가 위반 목록에서 계산하므로 구조적으로 0이다.")
    lines.append("오인용은 gold가 긴급이면 CHG-07, 표준 변경이면 해당 STD 인용을 허용한 수치이고, 허용 목록 없는 엄격 수치를 같이 적는다.")
    lines.append("예상 비용은 eval/pricing.json의 단가가 채워진 모델만 계산한다. 비어 있으면 '-'이다.")
    return lines


# ---------------------------------------------------------------------------
# 차트
# ---------------------------------------------------------------------------


def _pick_font() -> str | None:
    for name in _FONT_CANDIDATES:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except ValueError:
            continue
    return None


def _round_top_bar(ax, center: float, height: float, width: float, color: str, rx: float, ry: float) -> None:
    """위쪽 모서리만 둥근 막대. 아래(기준선)는 직각이다."""
    left, right = center - width / 2, center + width / 2
    rx, ry = min(rx, width / 2), min(ry, height)
    verts = [(left, 0), (left, height - ry), (left, height), (left + rx, height), (right - rx, height),
             (right, height), (right, height - ry), (right, 0), (left, 0)]  # fmt: skip
    codes = [MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO,
             MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO, MplPath.CLOSEPOLY]  # fmt: skip
    ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=color, edgecolor="none", zorder=3))


def render_bar_chart(
    path: Path,
    title: str,
    subtitle: str,
    values: dict[str, float | None],
    *,
    label_fmt,
    ylim: tuple[float, float],
    ticks: list[float],
    tick_fmt=None,
) -> None:
    """버전별 막대 하나짜리 비교 차트 한 장. 값이 없는 버전은 막대 대신 '-'를 남긴다."""
    font = _pick_font()
    if font:
        plt.rcParams["font.family"] = font
    plt.rcParams["axes.unicode_minus"] = False
    dpi, size = 200, (6.4, 3.9)
    fig = plt.figure(figsize=size, dpi=dpi, facecolor=SURFACE)
    axes_box = (0.11, 0.25, 0.85, 0.52)
    ax = fig.add_axes(axes_box, facecolor=SURFACE)

    ax.set_xlim(-0.6, len(VERSIONS) - 0.4)
    ax.set_ylim(*ylim)
    # 막대 모서리 반지름 4 CSS px (200dpi에서 약 8px)를 데이터 좌표로 바꾼다.
    radius_px = 4 * dpi / 96
    rx = radius_px / (size[0] * dpi * axes_box[2]) * (ax.get_xlim()[1] - ax.get_xlim()[0])
    ry = radius_px / (size[1] * dpi * axes_box[3]) * (ylim[1] - ylim[0])
    bar_width = 0.42

    ax.set_yticks(ticks)
    ax.set_yticklabels([(tick_fmt or label_fmt)(t) for t in ticks], color=TEXT_SECONDARY, fontsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8, linestyle="-")
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", length=0, pad=6)
    ax.set_xticks(range(len(VERSIONS)))
    ax.set_xticklabels(VERSIONS, color=TEXT_PRIMARY, fontsize=10)

    span = ylim[1] - ylim[0]
    for index, version in enumerate(VERSIONS):
        value = values.get(version)
        if value is None:
            ax.text(index, span * 0.02, "-", ha="center", va="bottom", color=TEXT_MUTED, fontsize=12)
            continue
        if value > 0:
            _round_top_bar(ax, index, value, bar_width, VERSION_COLORS[version], rx, ry)
        # 값은 막대 끝에 둔다. 글자는 본문 색이고 시리즈 색은 막대만 쓴다.
        ax.text(index, value + span * 0.025, label_fmt(value), ha="center", va="bottom",
                color=TEXT_PRIMARY, fontsize=10, fontweight="bold", zorder=4)  # fmt: skip

    fig.text(0.06, 0.935, title, ha="left", va="center", color=TEXT_PRIMARY, fontsize=14, fontweight="bold")
    fig.text(0.06, 0.875, subtitle, ha="left", va="center", color=TEXT_SECONDARY, fontsize=8.5)
    handles = [Patch(facecolor=VERSION_COLORS[v], edgecolor="none", label=VERSION_NAMES[v]) for v in VERSIONS]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=7.5, handlelength=1.0,
               handleheight=1.0, columnspacing=1.6, labelcolor=TEXT_SECONDARY, bbox_to_anchor=(0.5, 0.01))  # fmt: skip

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, facecolor=SURFACE)
    plt.close(fig)


def _nice_max(value: float) -> float:
    """막대 위 라벨 자리를 남기는 축 상한."""
    if value <= 0:
        return 1.0
    target = value * 1.18
    magnitude = 10 ** math.floor(math.log10(target))  # target이 속한 자릿수. 아래 단계 중 하나는 반드시 target 이상이다
    for step in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if target <= step * magnitude:
            return step * magnitude
    raise AssertionError("unreachable")  # step 10 x magnitude는 항상 target 이상이다


def chart_specs(chosen: dict[str, dict]) -> list[dict[str, Any]]:
    """차트 6장: 판정 정확도, 위반 F1, 지연 p95, 토큰, 일관성, 판정-위반 자기모순."""
    percent = {"label_fmt": lambda v: f"{v * 100:.0f}%", "ylim": (0, 1.12), "ticks": [0, 0.25, 0.5, 0.75, 1.0]}

    def series(get):
        return {v: (get(chosen[v]) if v in chosen else None) for v in VERSIONS}

    latency = series(lambda s: s["latency"]["p95_s"])
    tokens = series(lambda s: s["usage"]["tokens_per_run"])
    top_latency = _nice_max(max([x for x in latency.values() if x is not None], default=1))
    top_tokens = _nice_max(max([x for x in tokens.values() if x is not None], default=1))
    return [
        {"name": "verdict_accuracy", "title": "판정 정확도", "note": "정답 판정과 같은 비율", **percent,
         "values": series(lambda s: s["metrics"]["verdict_accuracy"])},
        {"name": "violation_f1", "title": "위반 조항 F1", "note": "(요청, 조항) 쌍 단위 micro 집계", **percent,
         "values": series(lambda s: s["metrics"]["violation_f1"])},
        {"name": "latency_p95", "title": "지연 p95 (초)", "note": "요청 1건 그래프 실행 시간, nearest-rank p95",
         "label_fmt": lambda v: f"{v:.1f}초", "tick_fmt": lambda v: f"{v:.0f}", "ylim": (0, top_latency),
         "ticks": [top_latency * i / 4 for i in range(5)], "values": latency},
        {"name": "tokens_per_run", "title": "토큰 / 실행", "note": "입력 + 출력 토큰의 실행당 평균",
         "label_fmt": lambda v: f"{v:,.0f}", "tick_fmt": lambda v: f"{v:,.0f}", "ylim": (0, top_tokens),
         "ticks": [top_tokens * i / 4 for i in range(5)], "values": tokens},
        {"name": "consistency", "title": "일관성", "note": "같은 요청을 반복했을 때 판정이 모두 같은 비율 (반복 2회 이상일 때만)", **percent,
         "values": series(lambda s: s["metrics"]["consistency"])},
        {"name": "self_contradiction", "title": "판정-위반 자기모순 (낮을수록 좋음)",
         "note": "자기 위반 목록으로 규정 1장 판정을 다시 계산하면 판정이 달라지는 실행의 비율", **percent,
         "values": series(lambda s: s["metrics"].get("self_contradiction_rate"))},
    ]


def render_charts(chosen: dict[str, dict], split: str, provider: str, charts_dir: Path) -> list[Path]:
    first = next(iter(chosen.values()))["meta"]
    models = {main_model(s) for s in chosen.values()}
    model_text = next(iter(models)) if len(models) == 1 else "모델 혼합"
    paths = []
    for spec in chart_specs(chosen):
        subtitle = f"{split} {first['n_requests']}건 x {first['reps']}회, {provider} ({model_text}). {spec['note']}"
        path = charts_dir / f"{split}_{model_slug(model_text)}_{spec['name']}.png"
        render_bar_chart(path, spec["title"], subtitle, spec["values"], label_fmt=spec["label_fmt"], ylim=spec["ylim"],
                         ticks=spec["ticks"], tick_fmt=spec.get("tick_fmt"))  # fmt: skip
        paths.append(path)
    return paths


# ---------------------------------------------------------------------------
# report.md
# ---------------------------------------------------------------------------


def split_section(split: str, provider: str, chosen: dict[str, dict], chart_paths: list[Path], report_dir: Path) -> str:
    first = next(iter(chosen.values()))["meta"]
    present = [v for v in VERSIONS if v in chosen]
    model = main_model(next(iter(chosen.values())))
    parts = [
        f"## {split} 평가 (main 모델 {model})",
        "",
        f"provider {provider}, main 모델 {model}, 요청 {first['n_requests']}건 x 반복 {first['reps']}회. "
        f"측정한 버전: {', '.join(present)}.",
        "같은 main 모델 안에서 각 버전의 가장 최근 실행을 쓴다. main 모델이 다른 절의 수치와는 직접 비교하지 않는다.",
        "",
        "### Before/After 표",
        "",
        headline_table(chosen),
        "",
        "### 전체 지표",
        "",
        detail_table(chosen),
        "",
        "### 구조화 필드별 정확도",
        "",
        "gold가 null인 restart_required, cert_expires_at은 비교에서 빠진다(괄호는 맞은 수/비교한 수).",
        "",
        field_table(chosen),
        "",
        "### 실험 메타데이터",
        "",
        meta_table(chosen),
        "",
        "### 참고",
        "",
    ]
    for warning in comparability_warnings(chosen):
        parts.append(f"- **경고** {warning}")
    parts += [f"- {note}" for note in notes(chosen)]
    parts += ["", "### 차트", ""]
    for path in chart_paths:
        relative = path.relative_to(report_dir) if path.is_relative_to(report_dir) else path
        parts += [f"![{path.stem}]({relative.as_posix()})", ""]
    return "\n".join(parts)


def build_report(
    results_dir: Path, out_path: Path, charts_dir: Path, splits: list[str], provider: str | None, now: datetime | None = None
) -> list[str]:
    """report.md와 차트를 만들고 쓴 split 목록을 돌려준다."""
    summaries = load_summaries(results_dir)
    now = now or datetime.now(KST)
    sections, written = [], []
    for split in splits:
        chosen_provider, by_model = select_by_model(summaries, split, provider)
        if not by_model:
            continue
        for chosen in by_model.values():
            chart_paths = render_charts(chosen, split, chosen_provider, charts_dir)
            sections.append(split_section(split, chosen_provider, chosen, chart_paths, out_path.parent))
        written.append(split)
    if not written:
        return []
    header = [
        "# GateKeeper 평가 리포트",
        "",
        f"생성 {now:%Y-%m-%d %H:%M} (python eval/report.py). 원자료는 results/*.summary.json이다.",
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(header) + "\n" + "\n\n".join(sections) + "\n", encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="버전별 비교 리포트와 차트를 만든다.")
    parser.add_argument("--split", choices=["dev", "eval", "all"], default="all")
    parser.add_argument("--provider", choices=["fake", "ollama", "anthropic", "openai"], default=None)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--out", type=Path, default=None, help="기본값은 <results-dir>/report.md")
    parser.add_argument("--charts-dir", type=Path, default=None, help="기본값은 <results-dir>/charts")
    args = parser.parse_args(argv)

    out_path = args.out or args.results_dir / "report.md"
    charts_dir = args.charts_dir or args.results_dir / "charts"
    splits = ["dev", "eval"] if args.split == "all" else [args.split]
    written = build_report(args.results_dir, out_path, charts_dir, splits, args.provider)
    if not written:
        print(f"{args.results_dir}에 {'/'.join(splits)} summary가 없다. 먼저 eval/run_eval.py를 실행한다.", file=sys.stderr)
        return 1
    print(f"리포트: {out_path} ({', '.join(written)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
