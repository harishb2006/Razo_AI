import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import type { AuditStep } from '../api/types'
import { formatTime } from '../lib/money'

const ACTOR_TONE: Record<string, string> = {
  buyer: 'bg-slate-200 text-slate-700',
  agent: 'bg-blue-100 text-blue-800',
  catalog: 'bg-slate-200 text-slate-700',
  cart: 'bg-slate-200 text-slate-700',
  policy: 'bg-amber-100 text-amber-900',
  payments: 'bg-teal-100 text-teal-900',
  merchant: 'bg-purple-100 text-purple-900',
  webhook: 'bg-teal-100 text-teal-900',
  system: 'bg-slate-200 text-slate-700',
}

const OUTCOME_TONE: Record<string, string> = {
  ok: 'border-slate-300',
  denied: 'border-red-400',
  escalated: 'border-amber-400',
  degraded: 'border-amber-400',
  failed: 'border-red-400',
}

function Step({ step }: { step: AuditStep }) {
  return (
    <li className={`relative border-l-2 pb-5 pl-5 ${OUTCOME_TONE[step.outcome] ?? 'border-slate-300'}`}>
      <span className="absolute -left-[7px] top-1 h-3 w-3 rounded-full border-2 border-white bg-slate-400" />

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-slate-400">{step.step}</span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
            ACTOR_TONE[step.actor] ?? 'bg-slate-200 text-slate-700'
          }`}
        >
          {step.actor}
        </span>
        <span className="text-sm font-medium">{step.headline}</span>
        <span className="text-xs text-muted">({step.outcome_word})</span>
        {step.latency_ms !== null && (
          <span className="text-[11px] text-slate-400">{step.latency_ms} ms</span>
        )}
        <span className="ml-auto font-mono text-[11px] text-slate-400">
          seq {step.seq} · {formatTime(step.at)}
        </span>
      </div>

      <p className="mt-1 text-sm text-slate-600">{step.reason}</p>
    </li>
  )
}

export function AuditPage() {
  const { sessionId = '' } = useParams()

  const explanation = useQuery({
    queryKey: ['audit', 'explain', sessionId],
    queryFn: () => api.explainSession(sessionId),
    enabled: Boolean(sessionId),
  })

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <Link to="/console" className="text-xs text-slate-500 underline hover:text-slate-800">
        ← back to console
      </Link>

      <h2 className="mt-3 text-lg font-semibold">Session trail</h2>
      <p className="font-mono text-xs text-muted">{sessionId}</p>

      {explanation.isLoading && (
        <div className="mt-4 space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg border border-line bg-white" />
          ))}
        </div>
      )}

      {explanation.isError && (
        <p className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          Couldn't load the trail for this session.
        </p>
      )}

      {explanation.isSuccess && (
        <>
          <p className="mt-3 rounded-lg border border-line bg-white p-3 text-sm">
            {explanation.data.summary}
          </p>

          <ol className="mt-6">
            {explanation.data.steps.map((step) => (
              <Step key={step.seq} step={step} />
            ))}
          </ol>

          <p className="mt-2 text-xs text-muted">
            Every entry is written once and hash-chained to the one before it. Altering any of them
            breaks the chain, and the console reports exactly where.
          </p>
        </>
      )}
    </div>
  )
}
