import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import clsx from 'clsx'

interface Toast {
  id: number
  message: string
  kind: 'success' | 'error' | 'info'
}

interface ToastContextValue {
  push: (message: string, kind?: Toast['kind']) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const push = useCallback((message: string, kind: Toast['kind'] = 'success') => {
    const id = Date.now() + Math.random()
    setToasts((current) => [...current, { id, message, kind }])
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id))
    }, 5000)
  }, [])

  const value = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            role="status"
            aria-live="polite"
            className={clsx(
              'pointer-events-auto animate-fade-in rounded-lg border px-3 py-2 text-xs shadow-lg',
              toast.kind === 'error'
                ? 'border-danger/50 bg-danger/15 text-danger'
                : toast.kind === 'info'
                  ? 'border-info/50 bg-info/10 text-info'
                  : 'border-ok/50 bg-ok/15 text-ok',
            )}
          >
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside a ToastProvider')
  return context
}
