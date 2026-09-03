import type { Decision, Finding, PolicyView } from '../api/types'

const STYLES: Record<Decision, { box: string; chip: string; label: string }> = {
  ALLOW: {
    box: 'border-emerald-300 bg-emerald-50',
    chip: 'bg-emerald-600 text-white',
    label: 'ALLOWED',
  },
  REQUIRE_APPROVAL: {
    box: 'border-amber-300 bg-amber-50',
    chip: 'bg-amber-600 text-white',
    label: 'MERCHANT APPROVAL NEEDED',
  },
  DENY: {
    box: 'border-red-300 bg-red-50',
    chip: 'bg-red-600 text-white',
    label: 'REFUSED',
  },
}

function FindingRow({ finding }: { finding: Finding }) {
  const failed = finding.outcome !== 'pass'
  return (
    <li className="flex gap-2 py-1 text-sm">
      <span
        className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold ${
          failed ? 'bg-slate-800 text-white' : 'bg-slate-200 text-slate-600'
        }`}
      >
        {finding.rule_id}
      </span>
      <span className={failed ? 'text-slate-900' : 'text-slate-500'}>{finding.reason}</span>
    </li>
  )
}

/**
 * Renders the server's verdict verbatim — decision, summary, and every rule
 * with its reason. The UI computes nothing here; when a rule blocks a
 * purchase on video, the viewer sees the rule ids and reasons straight from
 * the backend.
 */
export function PolicyBanner({ policy }: { policy: PolicyView }) {
  const style = STYLES[policy.decision] ?? STYLES.DENY
  const violations = policy.violations ?? []
  const passed = (policy.findings ?? []).filter((f) => f.outcome === 'pass')

  return (
    <div className={`rounded-lg border p-3 ${style.box}`}>
      <div className="flex items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-[11px] font-bold tracking-wide ${style.chip}`}>
          {style.label}
        </span>
        <span className="text-xs text-slate-500">the rulebook, not the AI</span>
      </div>

      <p className="mt-2 text-sm font-medium text-slate-900">{policy.reason_summary}</p>

      {violations.length > 0 && (
        <ul className="mt-2 border-t border-black/10 pt-2">
          {violations.map((f) => (
            <FindingRow key={f.rule_id} finding={f} />
          ))}
        </ul>
      )}

      {passed.length > 0 && (
        <details className="mt-2 border-t border-black/10 pt-2">
          <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">
            All {policy.findings.length} rules ran — show the {passed.length} that passed
          </summary>
          <ul className="mt-1">
            {passed.map((f) => (
              <FindingRow key={f.rule_id} finding={f} />
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
