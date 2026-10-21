import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

// 개발 서버 설정.
// - 실제 API: /api를 백엔드로 프록시한다. 포트는 sh/run_api.sh의 GK_API_PORT(기본 8100)와 같다.
// - 목업 API: VITE_API_MOCK=1이면 web/mock의 미들웨어가 /api를 대신 받는다(프록시는 끈다).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.GK_API_URL || `http://127.0.0.1:${env.GK_API_PORT || '8100'}`
  const useMock = env.VITE_API_MOCK === '1'

  return {
    plugins: [vue()],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    server: {
      host: '127.0.0.1',
      port: Number(env.GK_WEB_PORT || 5173),
      proxy: useMock ? undefined : { '/api': { target: apiTarget } },
    },
    test: {
      environment: 'node',
      include: ['tests/**/*.test.ts'],
    },
  }
})
