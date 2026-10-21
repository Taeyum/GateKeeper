#!/usr/bin/env bash
#
# GateKeeper: 이력 RAG 색인을 다시 만든다.
#
# history.json 100건을 title + description으로 임베딩(EMBED_MODEL, 항상 로컬 Ollama)해 GK_RAG_DIR(기본
# .data/rag) 아래에 색인한다. 항상 처음부터 다시 만든다. 임베딩 모델이나 history.json을 바꾼 뒤에는 이 명령으로
# 다시 만들어야 하고, 안 하면 v2 검색이 "색인이 오래됐다"며 멈춘다.
#
# 실행:
#   bash sh/build_index.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

# python3가 alias로 다른 경로를 가리키는 머신에서도 이 프로젝트의 .venv를 쓰게 한다.
# Windows(Git Bash)의 venv는 bin 대신 Scripts에 python.exe를 둔다.
[ -x ".venv/bin/python" ] && export PATH="$PWD/.venv/bin:$PATH"
[ -x ".venv/Scripts/python.exe" ] && export PATH="$PWD/.venv/Scripts:$PATH"

# 설정은 앱이 직접 읽는다(환경 변수가 .env보다 우선). 키 값은 읽지 않는다.
# Windows의 python은 줄 끝에 \r을 붙여서 마지막 값(URL)이 깨지므로 tr로 지운다.
read -r PROVIDER EMBED OLLAMA_URL < <(python - <<'PY' | tr -d '\r'
from app.config import get_settings

s = get_settings()
print(s.llm_provider, s.embed_model, s.ollama_base_url)
PY
)

if [ "$PROVIDER" != "fake" ]; then
    # 임베딩은 LLM provider와 상관없이 항상 Ollama다.
    # ollama CLI가 PATH에 없는 머신(Windows 등)에서도 되도록 서버 API로 모델을 확인한다.
    TAGS="$(curl -s --max-time 2 "$OLLAMA_URL/api/tags" 2>/dev/null || true)"
    [ -n "$TAGS" ] \
        || { echo "ERROR: ollama serve에 연결할 수 없습니다 ($OLLAMA_URL). 서버를 띄운 뒤 다시 실행하세요."; exit 1; }
    echo "$TAGS" | grep -Eq "\"name\":\"${EMBED}(:[^\"]*)?\"" \
        || { echo "ERROR: 임베딩 모델 $EMBED 미설치. 'bash sh/setup_ollama.sh' 후 다시 실행하세요."; exit 1; }
fi

python scripts/build_index.py "$@"
