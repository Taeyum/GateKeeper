import { createRouter, createWebHistory } from 'vue-router'

// 요청자: 작성(/) -> 진행과 결과(/requests/:id). 승인자: 결재함(/approvals).
// 역할 전환은 상단 헤더에서 하고, 역할에 맞는 첫 화면으로 이동한다.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'compose', component: () => import('@/views/ComposeView.vue') },
    { path: '/requests/:id', name: 'request', component: () => import('@/views/RequestView.vue'), props: true },
    { path: '/approvals', name: 'approvals', component: () => import('@/views/ApprovalsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
