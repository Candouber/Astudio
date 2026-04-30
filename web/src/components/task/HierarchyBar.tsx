import { useEffect, useState } from 'react'
import type { PathNode } from '../../types'
import { Brain, Users, CheckCircle, Activity, AlertTriangle, WifiOff } from 'lucide-react'
import { useI18n } from '../../i18n/useI18n'
import { translateStatusMessage } from '../../i18n/translateStatusMessage'
import { useTaskStore } from '../../stores/taskStore'
import './HierarchyBar.css'

interface Props {
  nodes: PathNode[]
  statusMessage: string
}

// 卡死判定阈值（毫秒）
const STALE_NODE_MS = 60_000           // 节点 60s 无动静 → 提示"疑似卡住"
const STALE_HEARTBEAT_MS = 12_000      // 服务端 12s 没心跳 → 提示"无响应"

function formatElapsed(ms: number): string {
  if (ms < 1000) return '0s'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  return rs ? `${m}m${rs}s` : `${m}m`
}

export default function HierarchyBar({ nodes, statusMessage }: Props) {
  const { t, locale } = useI18n()
  const ceoNode = nodes.find(n => n.type === 'agent_zero')
  const agentNodes = nodes.filter(n => n.type === 'sub_agent')
  const completed = agentNodes.filter(n => n.status === 'completed').length
  const total = agentNodes.length

  const nodeActivity = useTaskStore(s => s.nodeActivity)
  const lastHeartbeat = useTaskStore(s => s.lastHeartbeat)
  const taskStatus = useTaskStore(s => s.currentTask?.status)

  // 1Hz tick：让"X 秒前"等相对时间能持续刷新
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const runningNodes = nodes.filter(n => n.status === 'running')
  const taskActive = taskStatus === 'planning' || taskStatus === 'executing'

  // 取"上次更新最久"的运行节点作为"是否疑似卡住"的代表
  const stalest = runningNodes
    .map(n => ({ node: n, act: nodeActivity[n.id] }))
    .filter(x => x.act)
    .sort((a, b) => (a.act!.lastUpdatedAt) - (b.act!.lastUpdatedAt))[0]

  const stalestMs = stalest ? now - stalest.act!.lastUpdatedAt : 0
  const isNodeStale = stalest && stalestMs > STALE_NODE_MS

  const heartbeatAgeMs = lastHeartbeat > 0 ? now - lastHeartbeat : -1
  const heartbeatStale = taskActive && heartbeatAgeMs > STALE_HEARTBEAT_MS

  return (
    <div className="hierarchy-bar card">
      <div className="hierarchy-bar__step">
        <Brain size={16} />
        <span className="hierarchy-bar__label">{t('hierarchyBar.ceoLabel')}</span>
        <span className={`hierarchy-bar__dot ${ceoNode ? 'hierarchy-bar__dot--done' : ''}`} />
      </div>

      <div className="hierarchy-bar__arrow">→</div>

      <div className="hierarchy-bar__step">
        <Users size={16} />
        <span className="hierarchy-bar__label">{t('hierarchyBar.studioLeaderLabel')}</span>
        <span className="hierarchy-bar__dot hierarchy-bar__dot--done" />
      </div>

      <div className="hierarchy-bar__arrow">→</div>

      <div className="hierarchy-bar__step">
        <CheckCircle size={16} />
        <span className="hierarchy-bar__label">{t('hierarchyBar.workersLabel')}</span>
        <span className="hierarchy-bar__progress">{completed}/{total}</span>
      </div>

      {/* 实时心跳 / 卡死提示 */}
      {taskActive && (
        <div className="hierarchy-bar__live">
          {heartbeatStale ? (
            <span className="hierarchy-bar__pill hierarchy-bar__pill--danger" title={t('hierarchyBar.serverDownTitle')}>
              <WifiOff size={13} /> {t('hierarchyBar.serverNoHeartbeat', { time: formatElapsed(heartbeatAgeMs) })}
            </span>
          ) : isNodeStale ? (
            <span className="hierarchy-bar__pill hierarchy-bar__pill--warn"
              title={stalest?.act?.latestMessage || ''}>
              <AlertTriangle size={13} />{' '}
              {t('hierarchyBar.nodeStale', {
                role: stalest!.node.agent_role,
                time: formatElapsed(stalestMs),
              })}
            </span>
          ) : runningNodes.length > 0 ? (
            <span className="hierarchy-bar__pill hierarchy-bar__pill--ok">
              <Activity size={13} className="animate-pulse" />
              {t('hierarchyBar.stepsRunning', { count: runningNodes.length })}
              {lastHeartbeat > 0 && t('hierarchyBar.heartbeatSuffix', { time: formatElapsed(heartbeatAgeMs) })}
            </span>
          ) : (
            <span className="hierarchy-bar__pill hierarchy-bar__pill--neutral">
              <Activity size={13} /> {t('hierarchyBar.waiting')}
            </span>
          )}
        </div>
      )}

      {statusMessage && (
        <div className="hierarchy-bar__status">{translateStatusMessage(locale, statusMessage)}</div>
      )}
    </div>
  )
}
