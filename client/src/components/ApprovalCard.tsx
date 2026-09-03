import { useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../api/client'
import type { Approval, DecideResult } from '../api/types'
import { minutesUntil } from '../lib/money'

export function ApprovalCard({
  approval,
  onDecided,
}: {
  approval: Approval
  onDecided: () => void
}) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [result, setResult] = useState<DecideResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const expiresIn = minutesUntil(approval.expires_at)

  async function decide(decision: 'approve' | 'reject') {
    setBusy(decision)
    setError(null)
    try {
      const r = await api.decideApproval(approval.approval_id, decision, 'merchant@razo')
      setResult(r)
      onDecided()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <article className="rounded-lg border border-line bg-white p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-lg font-semibold">{approval.amount_display}</p>
          <p className="mt-0.5 font-mono text-[11px] text-muted">
            session {approval.session_id.slice(0, 10)}…
          </p>
        </div>
        {expiresIn !== null && approval.state === 'pending' && (
          <span
            className={`rounded px-2 py-0.5 text-[11px] font-medium ${
              expiresIn <= 5 ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-600'
            }`}
          >
            {expiresIn > 0 ? `${expiresIn} min left` : 'expiring'}
          </span>
        )}
      </div>

      <p className="mt-3 rounded bg-amber-50 px-3 py-2 text-sm text-amber-900">{approval.reason}</p>

      <ul className="mt-3 space-y-1">
        {approval.cart_items.map((item) => (
          <li key={item.sku} className="flex justify-between text-sm">
            <span>
              <span className="font-mono text-xs text-slate-500">{item.sku}</span>{' '}
              <span className="text-muted">
                {item.qty} × {item.unit_price_display} · {item.category}
              </span>
            </span>
            <span className="font-medium">{item.line_total_display}</span>
          </li>
        ))}
      </ul>

      {approval.violations.length > 0 && (
        <ul className="mt-3 border-t border-line pt-2">
          {approval.violations.map((f) => (
            <li key={f.rule_id} className="flex gap-2 py-0.5 text-xs">
              <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-white">
                {f.rule_id}
              </span>
              <span className="text-slate-700">{f.reason}</span>
            </li>
          ))}
        </ul>
      )}

      {result ? (
        <div
          className={`mt-3 rounded p-3 text-sm ${
            result.status === 'paid_link_created'
              ? 'bg-emerald-50 text-emerald-900'
              : result.status === 'rejected'
                ? 'bg-slate-100 text-slate-700'
                : 'bg-red-50 text-red-900'
          }`}
        >
          <p className="font-medium">{result.status.replace(/_/g, ' ')}</p>
          <p className="mt-0.5">{result.reason}</p>
          {result.status !== 'rejected' && result.status !== 'paid_link_created' && (
            <p className="mt-1 text-xs">
              The rulebook was re-run against the live cart before spending anything — this is the
              honest case where an approval no longer holds.
            </p>
          )}
        </div>
      ) : (
        <div className="mt-4 flex items-center gap-2">
          <button
            onClick={() => decide('approve')}
            disabled={busy !== null}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {busy === 'approve' ? 'Re-checking…' : 'Approve'}
          </button>
          <button
            onClick={() => decide('reject')}
            disabled={busy !== null}
            className="rounded-lg border border-line px-4 py-2 text-sm font-medium hover:bg-canvas disabled:opacity-50"
          >
            Reject
          </button>
          <Link
            to={`/console/audit/${approval.session_id}`}
            className="ml-auto text-xs text-slate-500 underline hover:text-slate-800"
          >
            view trail
          </Link>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
    </article>
  )
}
