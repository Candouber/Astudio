import { create } from 'zustand'

export type ToastTone = 'info' | 'success' | 'warning' | 'error'

export interface ToastItem {
  id: string
  title: string
  message?: string
  tone: ToastTone
}

interface ToastState {
  items: ToastItem[]
  showToast: (toast: Omit<ToastItem, 'id'>) => void
  dismissToast: (id: string) => void
}

export const useToastStore = create<ToastState>((set) => ({
  items: [],
  showToast: (toast) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const item: ToastItem = { id, ...toast }
    set(state => ({ items: [...state.items, item].slice(-4) }))
    window.setTimeout(() => {
      set(state => ({ items: state.items.filter(current => current.id !== id) }))
    }, 4200)
  },
  dismissToast: (id) => set(state => ({
    items: state.items.filter(item => item.id !== id),
  })),
}))
