import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { ExecutionDetail } from '../../../types'
import { turnSql } from './model'

export type PageTab = 'turns' | 'judge' | 'selfcheck' | 'snapshot' | 'raw'
export type ViewMode = 'chat' | 'compare'
export type StatusFilter = 'all' | 'pass' | 'warn' | 'fail' | 'unknown'
export type DrawerSection = 'turn' | 'judge' | 'exec' | 'evid' | 'context'
export type EvidTab = 'req' | 'res' | 'follow' | 'clarify' | 'chain' | 'sql' | 'judge'

export interface DrawerState {
  open: boolean
  turnIndex: number | null
  section: DrawerSection
  evidTab: EvidTab
  /** 点击错误归因后要高亮的标签 */
  focusTag: string | null
  /** 就地展开的行：jprompt / jres / step:<id> / grp:<phase> 等 */
  openRows: Set<string>
}

const PAGE_TABS: PageTab[] = ['turns', 'judge', 'selfcheck', 'snapshot', 'raw']
const SECTIONS: DrawerSection[] = ['turn', 'judge', 'exec', 'evid', 'context']
const EVID_TABS: EvidTab[] = ['req', 'res', 'follow', 'clarify', 'chain', 'sql', 'judge']

/** 旧链接的页签名 → 现在唯一页签（纯映射，不做锚点） */
const LEGACY_TAB: Record<string, PageTab> = {
  turns: 'turns',
  judge: 'judge',
  judges: 'judge',
  contract: 'judge',
  rules: 'judge',
  selfcheck: 'selfcheck',
  snapshot: 'snapshot',
  raw: 'raw',
  payload: 'raw',
  chain: 'raw',
}

/** 旧链接的段落名 → 五段之一 */
const LEGACY_SECTION: Record<string, DrawerSection> = {
  overview: 'turn',
  content: 'turn',
  turn: 'turn',
  judge: 'judge',
  judges: 'judge',
  contract: 'judge',
  rules: 'judge',
  exec: 'exec',
  chain: 'exec',
  evid: 'evid',
  evidence: 'evid',
  text: 'evid',
  payload: 'evid',
  context: 'context',
  selfcheck: 'context',
}

/** 旧链接的证据子页签 → 现有七项 */
const LEGACY_EVID: Record<string, EvidTab> = {
  req: 'req',
  request: 'req',
  prompt: 'req',
  res: 'res',
  response: 'res',
  follow: 'follow',
  clarify: 'clarify',
  chain: 'chain',
  sql: 'sql',
  judge: 'judge',
  jprompt: 'judge',
  jres: 'judge',
  rules: 'judge',
  contract: 'judge',
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/** 对话轮次视图状态：页面页签、对话/对照、筛选搜索、抽屉五段，并与 URL 双向同步。 */
export function useTraceView(detail: () => ExecutionDetail | null, initialTab: () => string | null | undefined) {
  const route = useRoute()
  const router = useRouter()

  const tab = ref<PageTab>('turns')
  const mode = ref<ViewMode>('chat')
  const status = ref<StatusFilter>('all')
  const query = ref('')
  const selfOpen = ref(true)
  const drawer = reactive<DrawerState>({
    open: false,
    turnIndex: null,
    section: 'turn',
    evidTab: 'req',
    focusTag: null,
    openRows: new Set<string>(),
  })

  function mapTab(value: string | null | undefined): PageTab | null {
    if (!value) return null
    return LEGACY_TAB[value] ?? null
  }

  function applyInitial(): void {
    const q = route.query
    const rawTab = str(q.tab) || str(initialTab())
    const mapped = mapTab(rawTab)
    if (mapped) tab.value = mapped
    if (str(q.cmp) === '1') mode.value = 'compare'
    const rawStatus = str(q.status)
    if (
      rawStatus === 'pass' ||
      rawStatus === 'warn' ||
      rawStatus === 'fail' ||
      rawStatus === 'unknown' ||
      rawStatus === 'all'
    ) {
      status.value = rawStatus
    }
    if (str(q.q)) query.value = str(q.q)

    const rawDrawer = str(q.drawer)
    if (rawDrawer) {
      const index = Number(rawDrawer)
      if (Number.isFinite(index) && index > 0) {
        drawer.open = true
        drawer.turnIndex = index
      }
    }
    const rawSection = str(q.sec)
    if (rawSection) drawer.section = LEGACY_SECTION[rawSection] ?? 'turn'
    const rawText = str(q.text)
    if (rawText) drawer.evidTab = LEGACY_EVID[rawText] ?? 'req'
    const rawDj = str(q.dj)
    if (rawDj === 'jprompt' || rawDj === 'jres') {
      drawer.section = 'judge'
      drawer.openRows = new Set([rawDj])
    }
    // 兼容旧协议：?text=... 直接带着轮次打开证据段
    if (str(q.turn) && !drawer.open) {
      const index = Number(q.turn)
      if (Number.isFinite(index) && index > 0) {
        drawer.open = true
        drawer.turnIndex = index
      }
    }
  }

  applyInitial()

  let urlTimer: number | null = null
  function writeUrl(): void {
    if (urlTimer !== null) window.clearTimeout(urlTimer)
    // 搜索输入防抖，避免每个键都写一次 history
    urlTimer = window.setTimeout(() => {
      const next: Record<string, string> = {}
      Object.entries(route.query).forEach(([key, value]) => {
        if (typeof value === 'string') next[key] = value
      })
      next.tab = tab.value
      if (mode.value === 'compare') next.cmp = '1'
      else delete next.cmp
      if (status.value !== 'all') next.status = status.value
      else delete next.status
      if (query.value) next.q = query.value
      else delete next.q
      if (drawer.open && drawer.turnIndex !== null) {
        next.drawer = String(drawer.turnIndex)
        next.sec = drawer.section
        if (drawer.section === 'evid') next.text = drawer.evidTab
        else delete next.text
        if (drawer.section === 'judge' && drawer.openRows.has('jprompt') && !drawer.openRows.has('jres')) {
          next.dj = 'jprompt'
        } else if (drawer.section === 'judge' && drawer.openRows.has('jres') && !drawer.openRows.has('jprompt')) {
          next.dj = 'jres'
        } else {
          delete next.dj
        }
      } else {
        delete next.drawer
        delete next.sec
        delete next.text
        delete next.dj
      }
      delete next.turn
      const same = Object.keys(next).every((key) => next[key] === route.query[key])
      const sameLength = Object.keys(next).length === Object.keys(route.query).length
      if (same && sameLength) return
      void router.replace({ query: next }).catch(() => undefined)
    }, 150)
  }

  watch([tab, mode, status, query], writeUrl)
  watch(
    () => [drawer.open, drawer.turnIndex, drawer.section, drawer.evidTab, [...drawer.openRows].join(',')],
    writeUrl,
  )

  // 外部（TraceView / 调试跳转）改了 tab 参数时同步过来
  watch(initialTab, (value) => {
    const mapped = mapTab(str(value))
    if (mapped && mapped !== tab.value) tab.value = mapped
  })

  const turns = computed(() => detail()?.turns ?? [])

  function openDrawer(
    index: number,
    section: DrawerSection = 'turn',
    focusTag: string | null = null,
    evidTab?: EvidTab,
  ): void {
    drawer.open = true
    drawer.turnIndex = index
    drawer.section = section
    drawer.focusTag = focusTag
    if (evidTab) drawer.evidTab = evidTab
    if (section === 'evid') {
      const turn = turns.value.find((item) => item.turn_index === index)
      const hasSql = Boolean(turnSql(turn))
      if (drawer.evidTab === 'sql' && !hasSql) drawer.evidTab = 'req'
    }
  }

  function closeDrawer(): void {
    drawer.open = false
    drawer.focusTag = null
  }

  function setSection(section: DrawerSection): void {
    drawer.section = section
    if (section !== 'judge') drawer.focusTag = null
  }

  function setEvidTab(value: EvidTab): void {
    drawer.evidTab = value
  }

  function switchTurn(index: number): void {
    drawer.turnIndex = index
    drawer.focusTag = null
    // 新轮没有 SQL 时回落到请求页签，避免落在空页
    const turn = turns.value.find((item) => item.turn_index === index)
    if (drawer.evidTab === 'sql' && !turnSql(turn)) drawer.evidTab = 'req'
  }

  function toggleRow(key: string): void {
    const next = new Set(drawer.openRows)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    drawer.openRows = next
  }

  function hasRow(key: string): boolean {
    return drawer.openRows.has(key)
  }

  function focusTag(tag: string, index: number): void {
    openDrawer(index, 'judge', tag)
    drawer.openRows = new Set(drawer.openRows)
  }

  return {
    tab,
    mode,
    status,
    query,
    selfOpen,
    drawer,
    openDrawer,
    closeDrawer,
    setSection,
    setEvidTab,
    switchTurn,
    toggleRow,
    hasRow,
    focusTag,
    PAGE_TABS,
    SECTIONS,
    EVID_TABS,
  }
}
