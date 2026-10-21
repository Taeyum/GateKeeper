#!/usr/bin/env bash
#
# GateKeeper 화면(web/)을 개발 서버로 띄운다.
#
#   bash sh/run_web.sh                          실제 API로 프록시 (기본 http://127.0.0.1:8100)
#   VITE_API_MOCK=1 bash sh/run_web.sh          목업 API (백엔드 없이 화면만 확인)
#   VITE_API_MOCK=1 VITE_MOCK_DROP=1 bash sh/run_web.sh
#                                               목업이 SSE를 세 번째 이벤트 뒤에 끊는다
#                                               (끊긴 뒤 GET /api/requests/{id}로 복구하는 경로 확인)
#
# 환경 변수
#   GK_WEB_PORT   화면 포트 (기본 5173)
#   GK_API_PORT   실제 API 포트 (기본 8100, sh/run_api.sh와 같다)
#   GK_API_URL    실제 API 전체 주소 (GK_API_PORT보다 우선)
#
# 처음 실행하면 web/에서 npm install을 한다. 종료하려면 이 터미널에서 Ctrl+C.
#
set -euo pipefail
cd "$(dirname "$0")/../web"

command -v node >/dev/null 2>&1 || { echo "ERROR: node가 없습니다. Node.js 22 이상을 설치하세요."; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm이 없습니다."; exit 1; }

WEB_PORT="${GK_WEB_PORT:-5173}"
API_PORT="${GK_API_PORT:-8100}"
API_URL="${GK_API_URL:-http://127.0.0.1:$API_PORT}"

if lsof -ti:"$WEB_PORT" >/dev/null 2>&1; then
    echo "ERROR: 포트 $WEB_PORT 를 이미 쓰고 있습니다. GK_WEB_PORT로 다른 포트를 지정하거나 기존 프로세스를 종료하세요."
    exit 1
fi

if [ ! -d node_modules ]; then
    echo "node_modules가 없어 npm install을 합니다..."
    npm install
fi

if [ "${VITE_API_MOCK:-0}" = "1" ]; then
    MODE="목업 API (VITE_API_MOCK=1${VITE_MOCK_DROP:+, SSE 끊김 시험})"
else
    MODE="실제 API 프록시 ($API_URL)"
    if ! curl -s --max-time 2 "$API_URL/docs" >/dev/null 2>&1; then
        echo "주의: $API_URL 에 API가 보이지 않습니다. 백엔드가 아직 없으면 VITE_API_MOCK=1로 실행하세요."
    fi
fi

cat <<EOF

################################################################
 GateKeeper 화면 (http://127.0.0.1:$WEB_PORT)
 $MODE
################################################################
 종료하려면 Ctrl+C
EOF

export GK_API_URL="$API_URL"
exec npm run dev
