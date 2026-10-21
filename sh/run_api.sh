#!/usr/bin/env bash
#
# GateKeeper API 서버를 띄운다. LLM_PROVIDER 하나로 provider를 고른다.
#
#   LLM_PROVIDER=ollama    bash sh/run_api.sh     로컬 Ollama (기본)
#   LLM_PROVIDER=anthropic bash sh/run_api.sh     Claude API (과금)
#   LLM_PROVIDER=openai    bash sh/run_api.sh     OpenAI API (과금, 예비)
#   LLM_PROVIDER=fake      bash sh/run_api.sh     가짜 모델 (테스트 전용)
#
# 설정(provider, 모델, 키)은 환경 변수와 .env를 앱이 직접 읽는다. 이 스크립트는 .env를 source하지
# 않으므로 명령줄에서 준 환경 변수가 .env보다 우선한다.
# 과금 provider는 확인 프롬프트를 거친다. 건너뛰려면 GK_YES=1.
#
# v2는 시작할 때 이력 색인과 임베딩이 준비됐는지 확인하고, 안 됐으면 서버를 띄우지 않는다(bash sh/build_index.sh).
# 종료하려면 이 터미널에서 Ctrl+C.
#
set -euo pipefail
cd "$(dirname "$0")/.."

# python3가 alias로 다른 경로를 가리키는 머신에서도 이 프로젝트의 .venv를 쓰게 한다.
# Windows(Git Bash)의 venv는 bin 대신 Scripts에 python.exe를 둔다.
[ -x ".venv/bin/python" ] && export PATH="$PWD/.venv/bin:$PATH"
[ -x ".venv/Scripts/python.exe" ] && export PATH="$PWD/.venv/Scripts:$PATH"

PORT="${GK_API_PORT:-8100}"

# ── 0) 설정 읽기 (키 값은 읽지 않고 설정 여부만 확인) ───────────────────
read -r PROVIDER MAIN_MODEL SMALL_MODEL OLLAMA_URL HAS_KEY < <(python - <<'PY'
from app.config import get_settings

s = get_settings()
key = {"anthropic": s.anthropic_api_key, "openai": s.openai_api_key}.get(s.llm_provider)
print(s.llm_provider, s.model_for("main"), s.model_for("small"), s.ollama_base_url,
      "yes" if key is not None else "no")
PY
)
echo "provider=$PROVIDER / main=$MAIN_MODEL / small=$SMALL_MODEL"

if lsof -ti:"$PORT" >/dev/null 2>&1; then
    echo "ERROR: 포트 $PORT 를 이미 쓰고 있습니다. GK_API_PORT로 다른 포트를 지정하거나 기존 프로세스를 종료하세요."
    exit 1
fi

STARTED_OLLAMA=""
cleanup() {
    echo
    echo "API를 종료합니다..."
    [ -n "$STARTED_OLLAMA" ] && kill "$STARTED_OLLAMA" 2>/dev/null || true
}
trap cleanup EXIT

case "$PROVIDER" in
ollama)
    # ── 1) Ollama 서버와 모델 확인 (서버가 없으면 띄운다) ─────────────
    if ! curl -s --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
        echo "ollama serve가 없어 백그라운드로 띄웁니다..."
        nohup ollama serve > /tmp/gatekeeper_ollama.log 2>&1 &
        STARTED_OLLAMA=$!
        for _ in $(seq 1 30); do
            curl -s --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 && break
            sleep 0.5
        done
    fi
    curl -s --max-time 2 "$OLLAMA_URL/api/tags" >/dev/null 2>&1 \
        || { echo "ERROR: ollama serve에 연결할 수 없습니다 ($OLLAMA_URL)"; exit 1; }
    for m in "$MAIN_MODEL" "$SMALL_MODEL"; do
        ollama list | awk 'NR>1 {print $1}' | grep -Fx -e "$m" -e "$m:latest" >/dev/null \
            || { echo "ERROR: 모델 $m 미설치. 'bash sh/setup_ollama.sh' 후 다시 실행하세요."; exit 1; }
    done
    ;;
anthropic|openai)
    # ── 1) 키 확인과 과금 확인 ────────────────────────────────────────
    if [ "$HAS_KEY" != "yes" ]; then
        echo "ERROR: ${PROVIDER} API 키가 없습니다. .env 또는 환경 변수에 설정하세요 (.env.example 참고)."
        exit 1
    fi
    echo "주의: 서버가 떠 있는 동안 보내는 요청마다 실제 $PROVIDER API가 호출되어 비용이 청구됩니다."
    if [ "${GK_YES:-0}" != "1" ]; then
        read -r -p "계속하시겠습니까? [y/N] " CONFIRM
        case "$CONFIRM" in
            [yY]|[yY][eE][sS]) ;;
            *) echo "중단합니다."; exit 1 ;;
        esac
    fi
    ;;
fake)
    echo "fake provider는 테스트 전용입니다. 응답 큐가 비어 있으면 요청이 실패합니다."
    ;;
*)
    echo "ERROR: 알 수 없는 LLM_PROVIDER: $PROVIDER"
    exit 1
    ;;
esac

# ── 2) 서버 기동 ───────────────────────────────────────────────────────
cat <<EOF

################################################################
 GateKeeper API (http://127.0.0.1:$PORT, LLM_PROVIDER=$PROVIDER)
################################################################
 API 문서: http://127.0.0.1:$PORT/docs
 종료하려면 Ctrl+C
EOF

# 앱을 import할 때가 아니라 서버가 뜰 때 설정을 읽도록 팩토리로 실행한다.
python -m uvicorn app.api.main:create_app --factory --host 127.0.0.1 --port "$PORT"
