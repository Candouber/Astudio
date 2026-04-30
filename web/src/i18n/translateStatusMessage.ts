import type { Locale } from '../stores/localeStore'
import { translate } from './t'

/** Must match `PREFIX` in server/i18n/status_message_codec.py */
export const TASK_STATUS_I18N_PREFIX = '__i18n__:'

/**
 * Decode backend-encoded progress strings; pass through legacy plain text.
 */
export function translateStatusMessage(locale: Locale, raw: string): string {
  if (!raw.startsWith(TASK_STATUS_I18N_PREFIX)) return raw
  try {
    const payload = JSON.parse(raw.slice(TASK_STATUS_I18N_PREFIX.length)) as {
      k: string
      p?: Record<string, string | number>
    }
    return translate(locale, payload.k, payload.p)
  } catch {
    return raw
  }
}
