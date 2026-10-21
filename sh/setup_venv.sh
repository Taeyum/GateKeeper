#!/usr/bin/env bash
#
# GateKeeper: 이 프로젝트 전용 .venv를 만들고 requirements.txt를 설치한다.
#
# activate에 기대지 않고 .venv 안의 python을 직접 호출한다. python3가 alias로
# 다른 경로를 가리키는 머신에서도 패키지가 시스템 전역에 잘못 설치되지 않게 하려는 것.
# uv가 있으면 uv로, 없으면 venv + pip로 설치한다.
#
# uv venv를 버전 없이 부르면 PATH 밖의 최신 Python(예: 3.15)을 잡을 수 있어서
# 기본은 3.11로 고정한다. 바꾸려면 GK_PYTHON_VERSION=3.12 처럼 지정한다.
#
# 실행:
#   bash sh/setup_venv.sh
#
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VERSION="${GK_PYTHON_VERSION:-3.11}"

# Windows(Git Bash)의 venv는 bin 대신 Scripts에 python.exe를 둔다. 만들어진 쪽을 찾아 쓴다.
find_venv_python() {
    local candidate
    for candidate in .venv/bin/python .venv/Scripts/python.exe; do
        if [ -x "$candidate" ]; then echo "$candidate"; return 0; fi
    done
    return 1
}

# ── 1) .venv 생성 (이미 있으면 건너뜀) ──────────────────────────────────
if find_venv_python >/dev/null; then
    echo ".venv가 이미 있습니다. 생성은 건너뛰고 설치만 다시 확인합니다."
elif command -v uv >/dev/null 2>&1; then
    echo "uv venv --python $PY_VERSION 로 가상환경을 만듭니다..."
    uv venv --python "$PY_VERSION"
else
    command -v "python$PY_VERSION" >/dev/null 2>&1 \
        || { echo "ERROR: python$PY_VERSION 도 uv도 없습니다. uv를 설치하거나 Python $PY_VERSION 을 설치하세요."; exit 1; }
    echo "python$PY_VERSION -m venv .venv 로 가상환경을 만듭니다..."
    "python$PY_VERSION" -m venv .venv
fi

VENV_PY="$(find_venv_python)" \
    || { echo "ERROR: .venv에서 python을 찾지 못했습니다."; exit 1; }

"$VENV_PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || { echo "ERROR: .venv의 Python이 3.11 미만입니다. .venv를 지우고 다시 실행하세요."; exit 1; }

# ── 2) 의존성 설치 ─────────────────────────────────────────────────────
echo "requirements.txt 설치 중..."
if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_PY" -r requirements.txt
else
    "$VENV_PY" -m pip install --upgrade pip --quiet
    "$VENV_PY" -m pip install -r requirements.txt
fi

# ── 3) 설치 확인 ───────────────────────────────────────────────────────
echo
echo "설치 확인:"
"$VENV_PY" - <<'PY'
from importlib.metadata import version

for pkg in ("pydantic", "pydantic-settings", "python-dotenv", "langchain-core",
            "langchain-anthropic", "langchain-ollama", "langchain-openai", "pytest"):
    print(f"  {pkg:<22} {version(pkg)}")
PY

cat <<EOF

################################################################
 .venv 준비 완료
################################################################

다음 순서로 진행합니다:
  $VENV_PY -m pytest -q          테스트 (실제 LLM 호출 제외)
  bash sh/setup_ollama.sh                Ollama 모델 받기 (qwen3:8b, qwen3:0.6b, bge-m3)
  LLM_PROVIDER=ollama bash sh/run_api.sh API 실행 (7단계부터)
EOF
