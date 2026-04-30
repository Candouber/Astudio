import type { TranslationTree } from './types'
import { zh } from './locales/zh'
import { en } from './locales/en'

export type { TranslationTree }

export const translations: Record<'zh' | 'en', TranslationTree> = {
  zh,
  en,
}
