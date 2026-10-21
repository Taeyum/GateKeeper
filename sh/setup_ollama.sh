#!/usr/bin/env bash
#
# GateKeeper: 로컬 Ollama에 필요한 모델을 받는다.
#
# 이미 받아져 있는 모델은 그대로 두고 없는 것만 pull한다. 이미 떠 있는 ollama serve는
# 건드리지 않고, 떠 있지 않을 때만 임시로 띄웠다가 끝나면 그 프로세스만 종료한다.
#
# 모델 (기본값)
#   qwen3:8b     MODEL_MAIN   구조화, 서브에이전트 해석, 설명
#   qwen3:0.6b   MODEL_SMALL  변경 유형 라우팅
#   bge-m3       EMBED_MODEL  RAG 임베딩 (provider와 상관없이 항상 Ollama)
# LLM_PROVIDER가 ollama가 아니면 MODEL_MAIN/MODEL_SMALL(예: claude-*)은 Ollama 모델이 아니므로
# 무시하고 기본값을 받는다.
#
# 실행:
#   bash sh/setup_ollama.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

# 환경 변수 우선, 없으면 .env, 없으면 기본값. .env를 source하지 않고 필요한 줄만 읽는다.
env_value() {
    local name="$1" default="$2" current="${!1:-}"
    if [ -n "$current" ]; then echo "$current"; return; fi
    local from_file=""
    [ -f .env ] && from_file="$(grep -E "^${name}=" .env | tail -1 | cut -d= -f2- | tr -d '"' || true)"
    echo "${from_file:-$default}"
}

PROVIDER="$(env_value LLM_PROVIDER ollama)"
if [ "$PROVIDER" = "ollama" ]; then
    MAIN_MODEL="$(env_value MODEL_MAIN qwen3:8b)"
    SMALL_MODEL="$(env_value MODEL_SMALL qwen3:0.6b)"
else
    MAIN_MODEL="qwen3:8b"
    SMALL_MODEL="qwen3:0.6b"
fi
EMBED="$(env_value EMBED_MODEL bge-m3)"
OLLAMA_URL="$(env_value OLLAMA_BASE_URL http://127.0.0.1:11434)"

command -v ollama >/dev/null 2>&1 \
    || { echo "ERROR: ollama가 설치되어 있지 않습니다. https://ollama.com 에서 설치 후 재실행하세요."; exit 1; }

echo "대상 모델: main=$MAIN_MODEL / small=$SMALL_MODEL / embed=$EMBED"

# ── 1) ollama serve 확인 (없을 때만 임시로 띄운다) ──────────────────────
STARTED_PID=""
if ! curl -s --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
    echo "ollama serve가 떠 있지 않아 임시로 띄웁니다..."
    nohup ollama serve > /tmp/gatekeeper_ollama_setup.log 2>&1 &
    STARTED_PID=$!
    for _ in $(seq 1 30); do
        curl -s --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && break
        sleep 0.5
    done
fi
curl -s --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 \
    || { echo "ERROR: ollama serve에 연결할 수 없습니다 ($OLLAMA_URL)"; exit 1; }

cleanup() {
    if [ -n "$STARTED_PID" ]; then
        echo
        echo "임시로 띄운 ollama serve를 종료합니다..."
        kill "$STARTED_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── 2) 없는 모델만 pull ─────────────────────────────────────────────────
has_model() {
    # grep -q는 pipefail에서 SIGPIPE를 일으키므로 전체를 읽는 형태로 검사한다.
    ollama list | awk 'NR>1 {print $1}' | grep -Fx -e "$1" -e "$1:latest" >/dev/null
}

for m in "$MAIN_MODEL" "$SMALL_MODEL" "$EMBED"; do
    if has_model "$m"; then
        echo "이미 받아져 있어 재사용: $m"
        continue
    fi
    echo
    echo "모델 받는 중: $m"
    ollama pull "$m"
done

cat <<EOF

################################################################
 Ollama 환경 준비 완료
   main=$MAIN_MODEL / small=$SMALL_MODEL / embed=$EMBED
################################################################

다음 확인:
  .venv/bin/python -m pytest -q -m llm -s    실제 구조화 출력 점검
EOF
