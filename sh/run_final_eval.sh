#!/usr/bin/env bash
#
# GateKeeper 9단계: 최종 평가(eval 20건)를 한 번에 차례대로 실행한다.
#
#   (a) anthropic  v0, v1, v2  reps 3  --langsmith       main claude-sonnet-5-5, 분류 claude-haiku-5-5, Judge claude-opus-5-5
#   (b) ollama     v0, v1, v2  reps 1  --no-judge        main qwen3:4b, 분류 qwen3:0.6b, OLLAMA_NUM_CTX=10240
#
#   bash sh/run_final_eval.sh                     여섯 개 전부
#   bash sh/run_final_eval.sh anthropic-v1        이름을 주면 그것만 (실패한 것만 다시 돌릴 때)
#                                                 이름: anthropic-v0/v1/v2, ollama-v0/v1/v2
#   GK_DRY=1 bash sh/run_final_eval.sh            점검만 하고 실행할 명령을 출력한다 (LLM 호출 없음)
#
# - 동시 실행하지 않는다. 지연 측정이 오염되고, 16GB 메모리에서 Ollama와 겹치면 안 된다.
# - 하나가 실패해도 다음으로 넘어가고, 끝에 실패 목록을 출력한다(실패가 있으면 종료 코드 1).
# - 개별 사례 본문은 출력하지 않는다(CLAUDE.md 절대 규칙 1). run_eval.py --split eval이 집계만 내고, 이 스크립트는
#   각 실행의 집계표만 옮겨 적는다. 실행 로그 전체는 $GK_FINAL_DIR/logs 아래에 남는다.
# - 결과(summary, jsonl)는 results/가 아니라 $GK_FINAL_DIR(기본 .data/final_eval, git 제외)에 쓴다. results/에 쓰면
#   첫 실행의 summary가 untracked 파일로 남아 두 번째 실행부터 summary의 git 정보가 dirty가 되기 때문이다.
#   끝난 뒤 summary를 results/로 옮겨 커밋한다.
# - 시작할 때 작업 트리가 깨끗하지 않으면 멈춘다(측정이 커밋된 코드에서 돌게). 과금 확인은 GK_YES=1로 건너뛴다.
#
set -uo pipefail
cd "$(dirname "$0")/.."

# python3가 alias로 다른 경로를 가리키는 머신에서도 이 프로젝트의 .venv를 쓰게 한다.
# Windows(Git Bash)의 venv는 bin 대신 Scripts에 python.exe를 둔다.
[ -x ".venv/bin/python" ] && export PATH="$PWD/.venv/bin:$PATH"
[ -x ".venv/Scripts/python.exe" ] && export PATH="$PWD/.venv/Scripts:$PATH"

# 파일로 돌려받는 Python 출력(로그)이 시스템 로케일(cp949)로 깨지지 않게 한다.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

OUT_DIR="${GK_FINAL_DIR:-.data/final_eval}"
LOG_DIR="$OUT_DIR/logs"
ALL_RUNS=(anthropic-v0 anthropic-v1 anthropic-v2 ollama-v0 ollama-v1 ollama-v2)
if [ "$#" -gt 0 ]; then RUNS=("$@"); else RUNS=("${ALL_RUNS[@]}"); fi

for r in "${RUNS[@]}"; do
    case " ${ALL_RUNS[*]} " in
        *" $r "*) ;;
        *) echo "ERROR: 알 수 없는 실행 이름: $r (가능: ${ALL_RUNS[*]})"; exit 2 ;;
    esac
done

# ── 0) 시작 전 점검 ────────────────────────────────────────────────────
if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: 작업 트리가 깨끗하지 않습니다. 커밋한 뒤 다시 실행하세요 (측정이 dirty 없는 커밋에서 돌게 하려는 것)."
    git status --short
    exit 1
fi

# 키 값은 읽지 않고 설정 여부만 확인한다. Windows의 python은 줄 끝에 \r을 붙여서 tr로 지운다.
read -r HAS_KEY HAS_LS OLLAMA_URL EMBED < <(python - <<'PY' | tr -d '\r'
from app.config import get_settings
from eval.tracing import api_key_present

s = get_settings()
print("yes" if s.anthropic_api_key is not None else "no", "yes" if api_key_present() else "no",
      s.ollama_base_url, s.embed_model)
PY
)
[ -n "${EMBED:-}" ] || { echo "ERROR: 설정을 읽지 못했습니다. .venv와 requirements 설치를 확인하세요."; exit 1; }

need_anthropic=0
need_ollama=0
need_rag=0
for r in "${RUNS[@]}"; do
    case "$r" in
        anthropic-*) need_anthropic=1 ;;
        ollama-*) need_ollama=1 ;;
    esac
    [ "${r#*-}" = "v2" ] && need_rag=1
done

if [ "$need_anthropic" = 1 ]; then
    [ "$HAS_KEY" = "yes" ] || { echo "ERROR: Anthropic API 키가 없습니다 (.env 또는 환경 변수)."; exit 1; }
    [ "$HAS_LS" = "yes" ] || { echo "ERROR: --langsmith에 필요한 LANGCHAIN_API_KEY가 없습니다."; exit 1; }
fi

# Ollama는 (b)의 LLM이고, v2의 임베딩(항상 로컬 Ollama)이기도 하다.
if [ "$need_ollama" = 1 ] || [ "$need_rag" = 1 ]; then
    TAGS="$(curl -s --max-time 3 "$OLLAMA_URL/api/tags" 2>/dev/null || true)"
    [ -n "$TAGS" ] || { echo "ERROR: ollama serve에 연결할 수 없습니다 ($OLLAMA_URL)."; exit 1; }
    models=("$EMBED")
    [ "$need_ollama" = 1 ] && models+=("qwen3:4b" "qwen3:0.6b")
    for m in "${models[@]}"; do
        echo "$TAGS" | grep -Eq "\"name\":\"${m}(:[^\"]*)?\"" \
            || { echo "ERROR: Ollama 모델 $m 미설치. 'bash sh/setup_ollama.sh' 후 다시 실행하세요."; exit 1; }
    done
fi

# v2는 이력 색인이 준비돼 있어야 한다. 하네스도 LLM 호출 전에 확인하지만, 앞의 실행을 몇 시간 돌린 뒤가 아니라
# 시작할 때 알려고 여기서 먼저 본다. 준비가 안 됐으면 한 번 다시 만든다.
rag_status() {
    python - <<'PY' 2>&1 | tr -d '\r'
import sys

from eval.harness import RunConfigError, apply_run_env, check_rag_ready

try:
    check_rag_ready(apply_run_env("v2", "anthropic"))
except RunConfigError as exc:
    print(exc)
    sys.exit(1)
print("READY")
PY
}
if [ "$need_rag" = 1 ]; then
    if ! rag_status >/dev/null; then
        echo "RAG 색인이 준비되지 않아 다시 만듭니다 (bash sh/build_index.sh)..."
        bash sh/build_index.sh >/dev/null 2>&1 || true
        RAG_MSG="$(rag_status)" || { echo "ERROR: $RAG_MSG"; exit 1; }
    fi
fi

COMMIT="$(git rev-parse --short HEAD)"
echo "################################################################"
echo " GateKeeper 최종 평가 (split=eval)"
echo " 커밋 $COMMIT, 실행: ${RUNS[*]}"
echo " 결과 폴더: $OUT_DIR (git 제외)"
echo "################################################################"

if [ "$need_anthropic" = 1 ] && [ "${GK_YES:-0}" != "1" ]; then
    echo "주의: Claude API(Sonnet/Haiku/Opus Judge)를 실제로 호출해 비용이 청구됩니다 (eval 20건 x 3회 x 버전 3개)."
    read -r -p "계속하시겠습니까? [y/N] " CONFIRM
    case "$CONFIRM" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "중단합니다."; exit 1 ;;
    esac
fi

mkdir -p "$LOG_DIR"
trap 'echo; echo "중단됨"; exit 130' INT TERM

# ── 1) 실행 ────────────────────────────────────────────────────────────
FAILED=()
DONE=()

# run_one <이름> <env 설정...> python eval/run_eval.py ...
run_one() {
    local label="$1"
    shift
    local log="$LOG_DIR/$label.log"
    local began=$SECONDS
    echo
    echo "[$(date '+%H:%M:%S')] 시작 $label"
    if [ "${GK_DRY:-0}" = "1" ]; then
        echo "(점검만) env $*"
        DONE+=("$label")
        return
    fi
    env "$@" >"$log" 2>&1
    local code=$?
    local took=$((SECONDS - began))
    if [ "$code" -eq 0 ]; then
        echo "[$(date '+%H:%M:%S')] 완료 $label (${took}초)"
        # 집계표와 그 아래 주석, 결과 파일 경로만 옮겨 적는다 (요청별 정보는 로그에도 없다).
        sed -n '/^== /,$p' "$log" | tr -d '\r'
        local project
        project="$(grep -o '"project": "[^"]*"' "$(sed -n 's/^집계: //p' "$log" | tr -d '\r' | tail -1)" 2>/dev/null | tail -1)"
        [ -n "$project" ] && echo "LangSmith $project"
        DONE+=("$label")
    else
        echo "[$(date '+%H:%M:%S')] 실패 $label (종료 코드 $code, ${took}초)"
        # 설정 오류는 요청 값이 없는 고정 문장이라 그대로 보여 주고, 나머지는 로그 파일에서 본다.
        grep -E '^(설정 오류|아직 없는 버전이다)' "$log" | head -3
        echo "로그: $log"
        FAILED+=("$label")
    fi
}

run_anthropic() {
    run_one "anthropic-$1" \
        MODEL_MAIN=claude-sonnet-5-5 MODEL_SMALL=claude-haiku-5-5 MODEL_JUDGE=claude-opus-5-5 \
        python eval/run_eval.py --version "$1" --split eval --reps 3 --provider anthropic --langsmith \
        --results-dir "$OUT_DIR"
}

# MODEL_JUDGE는 --no-judge라 쓰이지 않지만, .env에 claude- 모델이 있으면 하네스의 provider-모델 점검이 멈추므로 같이 고정한다.
run_ollama() {
    run_one "ollama-$1" \
        MODEL_MAIN=qwen3:4b MODEL_SMALL=qwen3:0.6b MODEL_JUDGE=qwen3:4b OLLAMA_NUM_CTX=10240 \
        python eval/run_eval.py --version "$1" --split eval --reps 1 --provider ollama --no-judge \
        --results-dir "$OUT_DIR"
}

for r in "${RUNS[@]}"; do
    case "$r" in
        anthropic-*) run_anthropic "${r#*-}" ;;
        ollama-*) run_ollama "${r#*-}" ;;
    esac
done

# ── 2) 끝 ──────────────────────────────────────────────────────────────
echo
echo "################################################################"
echo " 끝 [$(date '+%H:%M:%S')]  성공 ${#DONE[@]}건, 실패 ${#FAILED[@]}건 (커밋 $COMMIT)"
[ "${#DONE[@]}" -gt 0 ] && echo " 성공: ${DONE[*]}"
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo " 실패: ${FAILED[*]}"
    echo " 다시 돌리려면: bash sh/run_final_eval.sh ${FAILED[*]}"
fi
echo "################################################################"
[ "${#FAILED[@]}" -eq 0 ]
