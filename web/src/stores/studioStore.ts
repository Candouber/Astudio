import { create } from 'zustand'
import type { Studio } from '../types'
import { api } from '../api/client'
import { translate } from '../i18n/t'
import { useLocaleStore } from './localeStore'

function getErrorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

interface StudioState {
  studios: Studio[];
  loading: boolean;
  error: string | null;
  fetchStudios: () => Promise<void>;
  deleteStudio: (id: string) => Promise<void>;
}

export const useStudioStore = create<StudioState>((set) => ({
  studios: [],
  loading: false,
  error: null,
  fetchStudios: async () => {
    set({ loading: true, error: null })
    try {
      const studios = await api.getStudios()
      set({ studios, loading: false })
    } catch (err: unknown) {
      set({
        error: getErrorMessage(
          err,
          translate(useLocaleStore.getState().locale, 'errors.fetchStudiosFailed'),
        ),
        loading: false,
      })
    }
  },
  deleteStudio: async (id: string) => {
    set({ error: null })
    try {
      await api.deleteStudio(id)
      set(state => ({ studios: state.studios.filter(studio => studio.id !== id) }))
    } catch (err: unknown) {
      set({
        error: getErrorMessage(
          err,
          translate(useLocaleStore.getState().locale, 'errors.deleteStudioFailed'),
        ),
      })
      throw err
    }
  }
}))
