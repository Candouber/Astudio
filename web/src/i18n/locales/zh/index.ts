import type { TranslationTree } from '../../types'
import core from './core'
import features from './features'
import settings from './settings'
import result from './result'
import skillPool from './skillPool'
import backendTaskStatus from './backendTaskStatus'

export const zh: TranslationTree = {
  ...core,
  ...features,
  ...settings,
  ...result,
  ...skillPool,
  ...backendTaskStatus,
}
