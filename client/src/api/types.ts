export type Decision = 'ALLOW' | 'REQUIRE_APPROVAL' | 'DENY'

export type Finding = {
  rule_id: string
  outcome: 'pass' | 'require_approval' | 'deny'
  reason: string
  observed?: unknown
  limit?: unknown
}

export type PolicyView = {
  decision: Decision
  reason_summary: string
  findings: Finding[]
  violations: Finding[]
}

export type NextAction = {
  type: 'payment_link' | 'awaiting_approval' | 'none'
  payment_link_url?: string | null
  approval_id?: string | null
  expires_at?: string | null
  order_id?: string | null
}

export type CartItem = {
  sku: string
  qty: number
  unit_price_paise: number
  line_total_paise: number
}

export type Cart = {
  version: number
  state: string
  items: CartItem[]
  subtotal_paise: number
  total_paise: number
  currency: string
}

export type Suggestion = {
  sku: string
  title: string
  price_paise: number
  price_display: string
  category: string
  why: string
}

export type ProductOffer = {
  sku: string
  title: string
  brand: string
  category: string
  price_paise: number
  price_display: string
  in_stock: boolean
}

export type CheckoutResult = {
  status: 'paid_link_created' | 'approval_required' | 'denied'
  reason: string
  amount_paise?: number
  payment_link_url?: string
  approval_id?: string
  findings?: Finding[]
  violations?: Finding[]
}

export type TurnResponse = {
  session_id: string
  turn: number
  mode: 'normal' | 'degraded'
  reply: string
  cart: Cart
  latency_ms: number
  policy: PolicyView | null
  next_action: NextAction | null
  suggestions: Suggestion[]
  products: ProductOffer[]
  trace_id: string | null
}

export type ApprovalCartItem = {
  sku: string
  qty: number
  unit_price_paise: number
  unit_price_display: string
  line_total_paise: number
  line_total_display: string
  category: string
}

export type Approval = {
  approval_id: string
  session_id: string
  amount_paise: number
  amount_display: string
  state: 'pending' | 'approved' | 'rejected' | 'expired'
  reason: string
  cart_items: ApprovalCartItem[]
  findings: Finding[]
  violations: Finding[]
  expires_at: string
  decided_by: string | null
  decided_at: string | null
  created_at: string
}

export type DecideResult = {
  status: string
  reason: string
  payment_link_url?: string
  order_id?: string
}

export type AuditStep = {
  step: number
  at: string
  actor: string
  action: string
  headline: string
  reason: string
  outcome: string
  outcome_word: string
  latency_ms: number | null
  seq: number
}

export type Explanation = {
  session_id: string
  step_count: number
  summary: string
  steps: AuditStep[]
}

export type ChainVerification = {
  ok: boolean
  checked: number
  broken_at: number | null
  detail: string
}

export type LiveMetrics = {
  orders_total: number
  orders_paid_or_sent: number
  orders_failed: number
  audit_events_total: number
  policy_evaluations: number
  denials: number
  escalations: number
  degraded_events: number
  llm_calls_total: number
  llm_calls_by_status: Record<string, number>
}

export type SessionView = {
  session_id: string
  channel: string
  state: string
  turn_count: number
  cart: Cart
}

export type MessageView = {
  turn: number
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  tool_name: string | null
}
