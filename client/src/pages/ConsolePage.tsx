import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import { ApprovalCard } from '../components/ApprovalCard'

function MetricTile({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-white p-3">
      <p className="text-[11px] tracking-wide text-muted uppercase">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${tone ?? ''}`}>{value}</p>
    </div>
  )
}

export function ConsolePage() {
  const queryClient = useQueryClient()

  // Polling rather than sockets: free, and it cannot fail live on stage.
  const approvals = useQuery({
    queryKey: ['approvals', 'pending'],
    queryFn: () => api.listApprovals('pending'),
    refetchInterval: 3000,
  })

  const metrics = useQuery({
    queryKey: ['metrics', 'live'],
    queryFn: () => api.liveMetrics(),
    refetchInterval: 3000,
  })

  const chain = useQuery({
    queryKey: ['audit', 'verify'],
    queryFn: () => api.verifyChain(),
    refetchInterval: 10000,
  })

  const pending = approvals.data ?? []

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      <section>
        <h2 className="text-sm font-semibold">Since boot</h2>
        <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <MetricTile label="Rulebook runs" value={metrics.data?.policy_evaluations ?? '—'} />
          <MetricTile
            label="Refused"
            value={metrics.data?.denials ?? '—'}
            tone="text-red-700"
          />
          <MetricTile
            label="Escalated"
            value={metrics.data?.escalations ?? '—'}
            tone="text-amber-700"
          />
          <MetricTile
            label="Paid / link sent"
            value={metrics.data?.orders_paid_or_sent ?? '—'}
            tone="text-emerald-700"
          />
          <MetricTile label="Audit entries" value={metrics.data?.audit_events_total ?? '—'} />
        </div>

        {chain.data && (
          <p
            className={`mt-3 inline-flex items-center gap-2 rounded px-3 py-1.5 text-xs ${
              chain.data.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'
            }`}
          >
            <span className="font-medium">
              {chain.data.ok ? 'Audit chain intact' : 'AUDIT CHAIN BROKEN'}
            </span>
            <span>
              {chain.data.checked} entries verified
              {chain.data.broken_at !== null && ` · breaks at seq ${chain.data.broken_at}`}
            </span>
          </p>
        )}
      </section>

      <section className="mt-8">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-semibold">Approval inbox</h2>
          <span className="text-xs text-muted">
            {pending.length} waiting · polling every 3s
          </span>
        </div>

        <div className="mt-3 space-y-3">
          {approvals.isLoading && (
            <div className="h-32 animate-pulse rounded-lg border border-line bg-white" />
          )}

          {approvals.isError && (
            <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">
              Couldn't load approvals — is the backend running?
            </p>
          )}

          {approvals.isSuccess && pending.length === 0 && (
            <p className="rounded-lg border border-line bg-white p-6 text-center text-sm text-muted">
              Nothing waiting. Orders at or above ₹5,000 land here for a decision.
            </p>
          )}

          {pending.map((approval) => (
            <ApprovalCard
              key={approval.approval_id}
              approval={approval}
              onDecided={() => {
                queryClient.invalidateQueries({ queryKey: ['approvals'] })
                queryClient.invalidateQueries({ queryKey: ['metrics'] })
              }}
            />
          ))}
        </div>
      </section>
    </div>
  )
}
