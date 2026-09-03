import type { Cart } from '../api/types'
import { formatPaise } from '../lib/money'

type Props = {
  cart: Cart | null
  busy?: boolean
  onChangeQty?: (sku: string, qty: number) => void
  onCheckout?: () => void
}

export function CartPanel({ cart, busy = false, onChangeQty, onCheckout }: Props) {
  const items = cart?.items ?? []
  // A locked cart is already committed to a verdict; editing it would change
  // the order out from under a decision the merchant has already been asked for.
  const locked = cart?.state === 'locked'
  const editable = Boolean(onChangeQty) && !locked && !busy

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-line bg-white">
      <header className="border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold">Cart</h2>
        {locked && (
          <p className="mt-1 text-xs text-amber-700">
            Locked while the merchant decides — it can't be changed.
          </p>
        )}
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {items.length === 0 ? (
          <p className="text-sm text-muted">Nothing added yet.</p>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <li key={item.sku} className="text-sm">
                <div className="flex justify-between gap-2">
                  <span className="font-mono text-xs text-slate-500">{item.sku}</span>
                  <span className="font-medium">{formatPaise(item.line_total_paise)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => onChangeQty?.(item.sku, item.qty - 1)}
                      disabled={!editable}
                      aria-label={`Decrease ${item.sku}`}
                      className="h-6 w-6 rounded border border-line text-xs hover:border-slate-400 disabled:opacity-40"
                    >
                      −
                    </button>
                    <span className="w-6 text-center text-xs tabular-nums">{item.qty}</span>
                    <button
                      onClick={() => onChangeQty?.(item.sku, item.qty + 1)}
                      disabled={!editable || item.qty >= 10}
                      aria-label={`Increase ${item.sku}`}
                      className="h-6 w-6 rounded border border-line text-xs hover:border-slate-400 disabled:opacity-40"
                    >
                      +
                    </button>
                  </div>
                  <span className="text-xs text-muted">{formatPaise(item.unit_price_paise)} ea</span>
                </div>
                {editable && (
                  <button
                    onClick={() => onChangeQty?.(item.sku, 0)}
                    className="mt-1 text-[11px] text-slate-500 underline hover:text-red-700"
                  >
                    remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {items.length > 0 && (
        <footer className="border-t border-line px-4 py-3">
          <div className="flex justify-between text-sm font-semibold">
            <span>Total</span>
            <span>{formatPaise(cart!.total_paise)}</span>
          </div>

          {onCheckout && (
            <button
              onClick={onCheckout}
              disabled={busy || locked}
              className="mt-3 w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {busy ? 'Checking…' : 'Check out'}
            </button>
          )}

          <p className="mt-2 text-[11px] text-muted">
            Priced by the server from the catalog — never by the assistant. Checkout runs all 11
            rules whether you click it or ask for it.
          </p>
        </footer>
      )}
    </aside>
  )
}
