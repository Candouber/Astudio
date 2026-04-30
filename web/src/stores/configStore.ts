import { create } from 'zustand'
import type { AppConfig } from '../types'
import { api } from '../api/client'

interface ConfigState {
  config: AppConfig | null;
  isLoading: boolean;
  isSaving: boolean;
  isSettingsModalOpen: boolean;

  fetchConfig: () => Promise<void>;
  updateConfig: (newConfig: AppConfig) => Promise<void>;
  openModal: () => void;
  closeModal: () => void;
}

export const useConfigStore = create<ConfigState>((set) => ({
  config: null,
  isLoading: false,
  isSaving: false,
  isSettingsModalOpen: false,

  fetchConfig: async () => {
    set({ isLoading: true })
    try {
      const data = await api.getConfig()
      set({ config: data, isLoading: false })
    } catch (e) {
      console.error("Failed to fetch config", e)
      set({ isLoading: false })
    }
  },

  updateConfig: async (newConfig: AppConfig) => {
    set({ isSaving: true })
    try {
      const data = await api.updateConfig(newConfig)
      set({ config: data, isSaving: false })
    } catch (e) {
      console.error("Failed to save config", e)
      set({ isSaving: false })
    }
  },

  openModal: () => set({ isSettingsModalOpen: true }),
  closeModal: () => set({ isSettingsModalOpen: false }),
}))
