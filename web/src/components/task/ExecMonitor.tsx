import { useCallback, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  BackgroundVariant,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { useTaskStore } from '../../stores/taskStore'
import type { PathNode, PathEdge } from '../../types'
import { useI18n } from '../../i18n/useI18n'
import HierarchyBar from './HierarchyBar'
import ProgressBar from './ProgressBar'
import SubTaskPanel from './SubTaskPanel'
import ExecPathNode from './ExecPathNode'
import './ExecMonitor.css'

const nodeTypes = { pathNode: ExecPathNode }

export default function ExecMonitor() {
  const { t } = useI18n()
  const nodes: PathNode[] = useTaskStore(s => s.nodes)
  const edges: PathEdge[] = useTaskStore(s => s.edges)
  const selectedNodeId = useTaskStore(s => s.selectedNodeId)
  const statusMessage = useTaskStore(s => s.statusMessage)
  const currentTask = useTaskStore(s => s.currentTask)

  const agentNodes = nodes.filter(n => n.type === 'sub_agent')
  const completed = agentNodes.filter(n => n.status === 'completed').length
  const iterationMeta = useMemo(() => {
    const map = new Map<string, { index: number; title: string; instruction: string; sourceNodeId?: string | null }>()
    ;(currentTask?.iterations ?? []).forEach((iteration, index) => {
      map.set(iteration.id, {
        index: index + 1,
        title: iteration.title || t('execMonitor.roundTitle', { n: index + 1 }),
        instruction: iteration.instruction || '',
        sourceNodeId: iteration.source_node_id,
      })
    })
    return map
  }, [currentTask?.iterations, t])

  const flowNodes: Node[] = useMemo(() =>
    nodes.map((n: PathNode) => {
      const meta = n.iteration_id ? iterationMeta.get(n.iteration_id) : undefined
      return {
        id: n.id,
        type: 'pathNode' as const,
        position: n.position,
        data: {
          nodeId: n.id,
          type: n.type,
          agentRole: n.agent_role,
          stepLabel: n.step_label,
          status: n.status,
          output: n.output,
          iterationIndex: meta?.index,
          iterationTitle: meta?.title,
          iterationInstruction: meta?.instruction,
          isIterationRoot: n.type === 'agent_zero',
        },
        selected: n.id === selectedNodeId,
      }
    }),
    [nodes, selectedNodeId, iterationMeta]
  )

  const flowEdges: Edge[] = useMemo(() =>
    edges.map((e: PathEdge) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'smoothstep' as const,
      animated: e.type === 'diverge' || e.type === 'correction',
      label: e.type === 'diverge' ? t('common.iteration') : undefined,
      labelStyle: e.type === 'diverge' ? { fill: '#7e22ce', fontWeight: 700, fontSize: 11 } : undefined,
      labelBgStyle: e.type === 'diverge' ? { fill: '#faf5ff', fillOpacity: 0.95 } : undefined,
      style: {
        stroke: e.type === 'correction' ? 'var(--accent-warning)' :
                e.type === 'diverge' ? '#a855f7' : 'var(--accent-brand)',
        strokeWidth: e.type === 'diverge' ? 2.5 : 2,
        strokeDasharray: e.type === 'diverge' ? '6 4' : undefined,
      },
    })),
    [edges, t]
  )

  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    useTaskStore.getState().selectNode(node.id)
  }, [])

  return (
    <div className="exec-monitor">
      <HierarchyBar nodes={nodes} statusMessage={statusMessage} />

      <div className="exec-monitor__progress card">
        <span className="exec-monitor__progress-label">{t('execMonitor.progress')}</span>
        <ProgressBar completed={completed} total={agentNodes.length} />
      </div>

      <div className="exec-monitor__body">
        <div className={`exec-monitor__canvas ${selectedNodeId ? 'exec-monitor__canvas--with-panel' : ''}`}>
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            minZoom={0.3}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(0, 0, 0, 0.06)" />
            <Controls className="exec-monitor__controls" />
            <MiniMap
              nodeStrokeColor="var(--accent-brand)"
              nodeColor={(n: Node) => {
                const status = (n.data as Record<string, unknown>)?.status as string
                if (status === 'running') return '#2563eb'
                if (status === 'completed') return '#16a34a'
                if (status === 'error') return '#dc2626'
                return '#eef0f4'
              }}
              maskColor="rgba(255, 255, 255, 0.7)"
              className="exec-monitor__minimap"
            />
          </ReactFlow>
        </div>

        {selectedNodeId && (
          <div className="exec-monitor__panel card">
            <SubTaskPanel />
          </div>
        )}
      </div>
    </div>
  )
}
