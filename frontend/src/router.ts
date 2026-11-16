import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { getToken } from './api/client'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  {
    path: '/',
    component: () => import('./components/AppShell.vue'),
    children: [
      {
        path: '',
        name: 'dashboard',
        component: () => import('./views/DashboardView.vue'),
        meta: { title: '测试大盘' },
      },
      {
        path: 'tasks',
        name: 'tasks',
        component: () => import('./views/TasksView.vue'),
        meta: { title: '批量任务' },
      },
      {
        path: 'cases',
        name: 'cases',
        component: () => import('./views/CasesView.vue'),
        meta: { title: '用例资产' },
      },
      {
        path: 'prompts',
        name: 'prompts',
        component: () => import('./views/PromptsView.vue'),
        meta: { title: 'Prompt 资产' },
      },
      {
        path: 'trace',
        name: 'trace',
        component: () => import('./views/TraceView.vue'),
        meta: { title: '执行记录' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('./views/SettingsView.vue'),
        meta: { title: '环境与模型' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  const authenticated = Boolean(getToken())
  if (!to.meta.public && !authenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && authenticated) {
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : '测试平台'
  document.title = `${title} · 问账批量测试平台`
})
