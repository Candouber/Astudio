import { useEffect, useState, useCallback } from 'react'
import { MessageSquarePlus, Sparkles } from 'lucide-react'
import './SelectionToolbar.css'
import { useI18n } from '../../i18n/useI18n'

interface SelectionPayload {
  selectedText: string
  nodeId: string
  anchorRect: DOMRect
}

interface Props {
  /** 限定监听区域的 CSS 选择器 */
  containerSelector: string
  onAnnotate: (payload: SelectionPayload) => void
  onProcess: (payload: SelectionPayload) => void
}

export default function SelectionToolbar({ containerSelector, onAnnotate, onProcess }: Props) {
  const { t } = useI18n()
  const [visible, setVisible] = useState(false)
  const [pos, setPos] = useState({ x: 0, y: 0 })
  const [pending, setPending] = useState<SelectionPayload | null>(null)

  const handleMouseUp = useCallback(() => {
    const sel = window.getSelection()
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      setVisible(false)
      return
    }

    const text = sel.toString().trim()
    if (text.length < 2) {
      setVisible(false)
      return
    }

    // 确保选区在目标容器内
    const anchor = sel.anchorNode
    const container = document.querySelector(containerSelector)
    if (!container || !anchor || !container.contains(anchor)) {
      setVisible(false)
      return
    }

    // 寻找最近的 data-node-id（可能在 deliverable-item__content 或 synthesis 上）
    let el = anchor instanceof HTMLElement ? anchor : anchor.parentElement
    let nodeId = ''
    while (el && el !== container) {
      if (el.dataset?.nodeId) {
        nodeId = el.dataset.nodeId
        break
      }
      el = el.parentElement
    }
    // 综合总结区域没有 nodeId，用特殊标识
    if (!nodeId) {
      const synthWrap = container.querySelector('.result-view__synthesis-content')
      if (synthWrap && synthWrap.contains(anchor)) {
        nodeId = '__synthesis__'
      }
    }
    if (!nodeId) {
      setVisible(false)
      return
    }

    const range = sel.getRangeAt(0)
    const rect = range.getBoundingClientRect()

    setPos({
      x: rect.left + rect.width / 2,
      y: rect.top - 8,
    })
    setPending({ selectedText: text, nodeId, anchorRect: rect })
    setVisible(true)
  }, [containerSelector])

  const handleAnnotateClick = () => {
    if (pending) {
      onAnnotate(pending)
    }
    setVisible(false)
    window.getSelection()?.removeAllRanges()
  }

  const handleProcessClick = () => {
    if (pending) onProcess(pending)
    setVisible(false)
    window.getSelection()?.removeAllRanges()
  }

  useEffect(() => {
    document.addEventListener('mouseup', handleMouseUp)
    const hideOnScroll = () => setVisible(false)
    window.addEventListener('scroll', hideOnScroll, true)
    return () => {
      document.removeEventListener('mouseup', handleMouseUp)
      window.removeEventListener('scroll', hideOnScroll, true)
    }
  }, [handleMouseUp])

  if (!visible) return null

  return (
    <div
      className="selection-toolbar"
      style={{
        left: pos.x,
        top: pos.y,
      }}
      onMouseDown={e => e.preventDefault()}
    >
      <button type="button" className="selection-toolbar__btn" onClick={handleAnnotateClick}>
        <MessageSquarePlus size={14} />
        <span>{t('selectionToolbar.annotate')}</span>
      </button>
      <button type="button" className="selection-toolbar__btn selection-toolbar__btn--accent" onClick={handleProcessClick}>
        <Sparkles size={14} />
        <span>{t('selectionToolbar.process')}</span>
      </button>
    </div>
  )
}
