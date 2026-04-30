import { useRef, useEffect } from 'react'
import type { Annotation } from '../../types'
import MarkdownRenderer from '../common/MarkdownRenderer'
import { X, Quote, Trash2 } from 'lucide-react'
import { useI18n } from '../../i18n/useI18n'
import { useLocaleStore } from '../../stores/localeStore'
import './AnnotationPopover.css'

interface Props {
  annotation: Annotation
  anchorRect: DOMRect
  onClose: () => void
  onDelete: (id: string) => void
}

export default function AnnotationPopover({ annotation, anchorRect, onClose, onDelete }: Props) {
  const { t } = useI18n()
  const uiLocale = useLocaleStore(s => s.locale)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose()
      }
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    setTimeout(() => document.addEventListener('mousedown', handleOutside), 0)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  // 定位：优先在标记下方，空间不够则上方
  const width = Math.min(380, window.innerWidth - 24)
  const margin = 12
  const left = Math.max(margin, Math.min(anchorRect.left, window.innerWidth - width - margin))
  const top = anchorRect.bottom + 8
  const spaceBelow = window.innerHeight - anchorRect.bottom
  const placeAbove = spaceBelow < 300
  const style: React.CSSProperties = {
    position: 'fixed',
    width,
    left,
    ...(placeAbove
      ? { bottom: window.innerHeight - anchorRect.top + 8 }
      : { top }),
    zIndex: 1100,
  }

  return (
    <div ref={ref} className="ann-popover" style={style}>
      <div className="ann-popover__header">
        <span className="ann-popover__title">{t('annotationPopover.title')}</span>
        <button type="button" className="ann-popover__close" onClick={onClose}><X size={14} /></button>
      </div>

      <div className="ann-popover__quote">
        <Quote size={11} />
        <span>
          {annotation.selected_text.length > 150
            ? annotation.selected_text.slice(0, 150) + '…'
            : annotation.selected_text}
        </span>
      </div>

      <div className="ann-popover__question">{annotation.question}</div>

      {annotation.answer ? (
        <div className="ann-popover__answer">
          <MarkdownRenderer content={annotation.answer} />
        </div>
      ) : (
        <div className="ann-popover__no-answer">{t('annotationPopover.generating')}</div>
      )}

      <div className="ann-popover__footer">
        <span className="ann-popover__time">
          {new Date(annotation.created_at).toLocaleString(uiLocale === 'zh' ? 'zh-CN' : 'en-US', {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
          })}
        </span>
        <button
          type="button"
          className="ann-popover__delete"
          onClick={() => { onDelete(annotation.id); onClose() }}
        >
          <Trash2 size={12} /> {t('annotationPopover.delete')}
        </button>
      </div>
    </div>
  )
}
