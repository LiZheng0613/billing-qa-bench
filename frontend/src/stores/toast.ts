import { ref } from 'vue'
import { defineStore } from 'pinia'

export type ToastType = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  type: ToastType
  message: string
}

let seed = 0

export const useToastStore = defineStore('toast', () => {
  const items = ref<ToastItem[]>([])

  function remove(id: number): void {
    items.value = items.value.filter((item) => item.id !== id)
  }

  function push(type: ToastType, message: string, timeout = 4200): number {
    seed += 1
    const id = seed
    items.value = [...items.value, { id, type, message }]
    if (timeout > 0) {
      window.setTimeout(() => remove(id), timeout)
    }
    return id
  }

  const success = (message: string) => push('success', message)
  const error = (message: string) => push('error', message, 6000)
  const info = (message: string) => push('info', message)

  return { items, push, remove, success, error, info }
})
