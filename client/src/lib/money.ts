/**
 * Formatting only — no arithmetic. Totals, limits and eligibility all arrive
 * from the server, so what appears on screen is provably the server's
 * decision rather than a UI approximation of it.
 */
export function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`
}

export function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString('en-IN', { hour12: false })
}

export function minutesUntil(iso: string): number | null {
  const d = new Date(iso).getTime()
  if (Number.isNaN(d)) return null
  return Math.round((d - Date.now()) / 60000)
}
