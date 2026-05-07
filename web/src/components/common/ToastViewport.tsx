import { AlertTriangle, CheckCircle, Info, XCircle } from 'lucide-react'
import { useToastStore, type ToastTone } from '../../stores/toastStore'
import './ToastViewport.css'

function Icon({ tone }: { tone: ToastTone }) {
  if (tone === 'success') return <CheckCircle size={16} />
  if (tone === 'warning') return <AlertTriangle size={16} />
  if (tone === 'error') return <XCircle size={16} />
  return <Info size={16} />
}

export default function ToastViewport() {
  const items = useToastStore(s => s.items)
  const dismissToast = useToastStore(s => s.dismissToast)

  if (!items.length) return null

  return (
    <div className="toast-viewport" role="status" aria-live="polite">
      {items.map(item => (
        <div key={item.id} className={`toast-bubble toast-bubble--${item.tone}`}>
          <span className="toast-bubble__icon"><Icon tone={item.tone} /></span>
          <div className="toast-bubble__copy">
            <strong>{item.title}</strong>
            {item.message && <span>{item.message}</span>}
          </div>
          <button
            type="button"
            className="toast-bubble__close"
            onClick={() => dismissToast(item.id)}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  )
}
