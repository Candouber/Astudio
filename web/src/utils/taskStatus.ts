import type { TaskStatus } from '../types'
import { useLocaleStore } from '../stores/localeStore'
import { translate } from '../i18n/t'

export const ACTIVE_TASK_STATUSES = new Set<TaskStatus>([
  'planning',
  'need_clarification',
  'await_leader_plan_approval',
  'executing',
])

function locale() {
  return useLocaleStore.getState().locale
}

export function getTaskStatusLabel(status?: string) {
  if (!status) return translate(locale(), 'taskStatus.syncing')
  const path = `taskStatus.labels.${status}`
  const resolved = translate(locale(), path)
  if (resolved !== path) return resolved
  return status
}

export function getTaskProgressText(status?: string) {
  const loc = locale()
  if (!status) return translate(loc, 'taskStatus.progress.default')
  const path = `taskStatus.progress.${status}`
  const resolved = translate(loc, path)
  if (resolved !== path) return resolved
  return translate(loc, 'taskStatus.progress.default')
}
