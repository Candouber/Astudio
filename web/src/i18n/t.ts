import type { Locale } from '../stores/localeStore'
import { translations } from './translations'
import type { TranslationTree } from './types'

export function resolveTree(tree: TranslationTree, path: string): string | undefined {
  const keys = path.split('.')
  let cur: string | TranslationTree | undefined = tree
  for (const k of keys) {
    if (cur === undefined || typeof cur === 'string') return undefined
    cur = cur[k] as string | TranslationTree | undefined
  }
  return typeof cur === 'string' ? cur : undefined
}

export function translate(
  locale: Locale,
  path: string,
  vars?: Record<string, string | number>,
): string {
  let s = resolveTree(translations[locale], path) ?? path
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.replaceAll(`{{${k}}}`, String(v))
    }
  }
  return s
}
