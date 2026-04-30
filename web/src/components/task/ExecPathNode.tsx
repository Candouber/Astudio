import { memo, useEffect, useState } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import { useI18n } from '../../i18n/useI18n'
import { useTaskStore } from '../../stores/taskStore'
import { AlertTriangle, Activity } from 'lucide-react'
import './ExecPathNode.css'

interface PathNodeData {
  nodeId: string
  type: string
  agentRole: string
  stepLabel: string
  status: string
  output: string
  iterationIndex?: number
  iterationTitle?: string
  iterationInstruction?: string
  isIterationRoot?: boolean
}

const STALE_THRESHOLD_MS = 60_000

function formatElapsed(ms: number): string {
  if (ms < 1000) return '0s'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  return rs ? `${m}m${rs}s` : `${m}m`
}

function ExecPathNode({ data }: NodeProps) {
  const { t } = useI18n()
  const d = data as unknown as PathNodeData
  const selectNode = useTaskStore(s => s.selectNode)
  const streamBuffers = useTaskStore(s => s.streamBuffers)
  const activity = useTaskStore(s => s.nodeActivity[d.nodeId])
  const streamContent = streamBuffers[d.nodeId] || ''

  const isRunning = d.status === 'running'

  // 1Hz tick：让相对时间持续刷新
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [isRunning])

  const elapsedMs = activity ? now - activity.startedAt : 0
  const idleMs = activity ? now - activity.lastUpdatedAt : 0
  const isStale = isRunning && idleMs > STALE_THRESHOLD_MS

  // 优先用 progress callback 推上来的"模型在做什么"消息，
  // 兜底用 node.output 截尾
  const liveMessage = activity?.latestMessage || d.output || ''

  const typeIcon = d.type === 'agent_zero' ? '🧠' : d.type === 'sub_agent' ? '⚡' : '👤'

  return (
    <div
      className={`exec-node exec-node--${d.status} ${isStale ? 'exec-node--stale' : ''}`}
      onClick={() => selectNode(d.nodeId)}
    >
      <Handle type="target" position={Position.Top} className="exec-node__handle" />

      <div className="exec-node__header">
        <span className="exec-node__icon">{typeIcon}</span>
        <span className="exec-node__role">{d.agentRole}</span>
        {d.iterationIndex && (
          <span className="exec-node__iteration">{t('execPathNode.roundSuffix', { n: d.iterationIndex })}</span>
        )}
        <span className={`exec-node__dot exec-node__dot--${d.status}`} />
      </div>

      <div className="exec-node__label">{d.stepLabel}</div>

      {d.isIterationRoot && d.iterationIndex && d.iterationIndex > 1 && (
        <div className="exec-node__iteration-note" title={d.iterationInstruction}>
          <strong>
            {d.iterationTitle || t('execPathNode.iterationDefault', { n: d.iterationIndex })}
          </strong>
          {d.iterationInstruction && (
            <span>{d.iterationInstruction.slice(0, 92)}{d.iterationInstruction.length > 92 ? '…' : ''}</span>
          )}
        </div>
      )}

      {isRunning && (
        <div className="exec-node__live">
          <span className={`exec-node__live-tag ${isStale ? 'exec-node__live-tag--stale' : ''}`}>
            {isStale ? <AlertTriangle size={11} /> : <Activity size={11} className="animate-pulse" />}
            {isStale
              ? t('execPathNode.stale', { time: formatElapsed(idleMs) })
              : t('execPathNode.running', { time: formatElapsed(elapsedMs) })}
          </span>
          {liveMessage && (
            <div className="exec-node__live-msg" title={liveMessage}>
              {liveMessage.slice(0, 80)}{liveMessage.length > 80 ? '…' : ''}
            </div>
          )}
        </div>
      )}

      {isRunning && streamContent && (
        <div className="exec-node__stream">
          {streamContent.slice(-100)}
          <span className="exec-node__cursor">▍</span>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="exec-node__handle" />
    </div>
  )
}

export default memo(ExecPathNode)
