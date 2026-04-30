import { useCallback } from 'react'
import { useLocaleStore, type Locale } from '../stores/localeStore'
import { translate } from './t'

export function useI18n() {
  const locale = useLocaleStore((s) => s.locale)
  const setLocale = useLocaleStore((s) => s.setLocale)

  const t = useCallback(
    (path: string, vars?: Record<string, string | number>) => translate(locale, path, vars),
    [locale],
  )

  function toggleLocale() {
    setLocale(locale === 'zh' ? 'en' : 'zh')
  }

  return { locale, setLocale, toggleLocale, t }
}

export type { Locale }
