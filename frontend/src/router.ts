import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { getToken } from './api/client'
import { useAuthStore } from './stores/auth'

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
        meta: { title: '测试大盘', permission: 'dashboard:read' },
      },
      {
        path: 'tasks',
        name: 'tasks',
        component: () => import('./views/TasksView.vue'),
        meta: { title: '批量任务', permission: 'task:read' },
      },
      {
        path: 'tasks/:id',
        name: 'task-detail',
        component: () => import('./views/TaskDetailView.vue'),
        meta: {
          title: '任务详情',
          nav: 'tasks',
          permission: 'task:read',
          breadcrumb: [{ label: '批量任务', to: '/tasks' }],
        },
      },
      {
        path: 'cases',
        name: 'cases',
        component: () => import('./views/CasesView.vue'),
        meta: { title: '用例资产', permission: 'case:read' },
      },
      {
        path: 'cases/:id',
        name: 'case-detail',
        component: () => import('./views/CaseDetailView.vue'),
        meta: {
          title: '用例详情',
          nav: 'cases',
          permission: 'case:read',
          breadcrumb: [{ label: '用例资产', to: '/cases' }],
        },
      },
      {
        path: 'prompts',
        name: 'prompts',
        component: () => import('./views/PromptsView.vue'),
        meta: { title: 'Prompt 资产', permission: 'prompt:read' },
      },
      {
        path: 'trace',
        name: 'trace',
        component: () => import('./views/TraceView.vue'),
        meta: { title: '单笔执行结果', permission: 'execution:read' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('./views/SettingsView.vue'),
        meta: { title: '环境与模型', permission: 'environment:read' },
      },
      {
        path: 'ops',
        name: 'ops',
        component: () => import('./views/OpsView.vue'),
        meta: { title: '运营后台', permission: 'stats:read' },
      },
      { path: 'access', redirect: '/access/users' },
      {
        path: 'access/users',
        name: 'access-users',
        component: () => import('./access/UsersView.vue'),
        meta: {
          title: '用户管理',
          nav: 'access',
          requiresAdmin: true,
          permission: 'user:read',
          accessTab: 'users',
          breadcrumb: [{ label: '用户与权限', to: '/access/users' }],
        },
      },
      {
        path: 'access/roles',
        name: 'access-roles',
        component: () => import('./access/RolesView.vue'),
        meta: {
          title: '角色权限',
          nav: 'access',
          requiresAdmin: true,
          permission: 'role:read',
          accessTab: 'roles',
          breadcrumb: [{ label: '用户与权限', to: '/access/users' }],
        },
      },
      {
        path: 'access/audit',
        name: 'access-audit',
        component: () => import('./access/AuditView.vue'),
        meta: {
          title: '审计日志',
          nav: 'access',
          requiresAdmin: true,
          permission: 'audit:read',
          accessTab: 'audit',
          breadcrumb: [{ label: '用户与权限', to: '/access/users' }],
        },
      },
    ],
  },
  {
    path: '/403',
    name: 'forbidden',
    component: () => import('./views/ForbiddenView.vue'),
    meta: { title: '无访问权限' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!getToken()) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  const auth = useAuthStore()
  if (!auth.loaded) {
    try {
      await auth.fetchProfile()
    } catch {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
  }

  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return { name: 'forbidden' }
  }

  const requiredPermission = to.meta.permission
  if (typeof requiredPermission === 'string' && !auth.can(requiredPermission)) {
    return { name: 'forbidden' }
  }
  return true
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : '测试平台'
  document.title = `${title} · 问账批量测试平台`
})
