/**
 * 批量任务实时事件流（全局单连接）。
 *
 * 多个页面（任务列表 / 测试大盘 / 用例详情 / 执行详情）共用一条 `/ws/tasks`
 * WebSocket，按需订阅与退订；最后一个订阅者离开时关闭连接。
 * 页面不再使用定时器轮询，刷新完全由服务端事件驱动。
 */

import { onBeforeUnmount, onMounted } from 'vue'

import { buildWebSocketUrl } from '../api/client'
import type { TaskProgressEvent } from '../types'

type EventHandler = (event: TaskProgressEvent) => void
type OpenHandler = () => void

const handlers = new Set<EventHandler>()
const openHandlers = new Set<OpenHandler>()
let socket: WebSocket | null = null
let reconnectTimer: number | null = null
let connecting = false

function scheduleReconnect(): void {
  if (reconnectTimer !== null || handlers.size === 0) return
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null
    connect()
  }, 3000)
}

function connect(): void {
  if (socket || connecting || handlers.size === 0) return
  connecting = true
  try {
    const ws = new WebSocket(buildWebSocketUrl('/ws/tasks'))
    socket = ws
    ws.onopen = () => {
      connecting = false
      openHandlers.forEach((handler) => handler())
    }
    ws.onmessage = (message) => {
      try {
        const event = JSON.parse(String(message.data)) as TaskProgressEvent
        handlers.forEach((handler) => handler(event))
      } catch {
        // 忽略非 JSON 帧
      }
    }
    ws.onerror = () => {
      try {
        ws.close()
      } catch {
        // ignore
      }
    }
    ws.onclose = () => {
      connecting = false
      if (socket === ws) socket = null
      scheduleReconnect()
    }
  } catch {
    connecting = false
    scheduleReconnect()
  }
}

function closeIfIdle(): void {
  if (handlers.size > 0) return
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (socket) {
    socket.onclose = null
    socket.onerror = null
    socket.close()
    socket = null
  }
  connecting = false
}

export function useTaskEventStream(
  onEvent: EventHandler,
  options: { onOpen?: OpenHandler } = {},
): void {
  handlers.add(onEvent)
  if (options.onOpen) openHandlers.add(options.onOpen)
  onMounted(connect)
  onBeforeUnmount(() => {
    handlers.delete(onEvent)
    if (options.onOpen) openHandlers.delete(options.onOpen)
    closeIfIdle()
  })
}
