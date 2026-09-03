const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1'
const API_KEY = import.meta.env.VITE_API_KEY ?? 'dev-local-key'

export class ApiError extends Error {
  code: string
  status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError('NETWORK', "Couldn't reach the server — is the backend running?", 0)
  }

  if (!response.ok) {
    // Surface the backend's own user_message rather than a generic string:
    // every RazoError carries one written for a buyer to read.
    let code = 'SYSTEM_ERROR'
    let message = 'Something went wrong.'
    try {
      const body = await response.json()
      code = body?.error?.code ?? code
      message = body?.error?.message ?? message
    } catch {
      /* non-JSON error body — keep the defaults */
    }
    throw new ApiError(code, message, response.status)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  createSession: (body: { channel?: string; actor_ref?: string; mandate?: unknown } = {}) =>
    request<{ session_id: string }>('/chat/sessions', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  sendMessage: (sessionId: string, text: string) =>
    request<import('./types').TurnResponse>(`/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),

  getSession: (sessionId: string) =>
    request<import('./types').SessionView>(`/chat/sessions/${sessionId}`),

  getMessages: (sessionId: string) =>
    request<import('./types').MessageView[]>(`/chat/sessions/${sessionId}/messages`),

  // Deterministic actions skip the model entirely: same services, same audit
  // events, same rulebook at checkout — just no LLM round trip in the way.
  addToCart: (sessionId: string, sku: string, qty = 1) =>
    request<import('./types').Cart>(`/cart/${sessionId}/items`, {
      method: 'POST',
      body: JSON.stringify({ sku, qty }),
    }),

  updateCartItem: (sessionId: string, sku: string, qty: number) =>
    request<import('./types').Cart>(`/cart/${sessionId}/items/${sku}`, {
      method: 'PATCH',
      body: JSON.stringify({ qty }),
    }),

  checkout: (sessionId: string) =>
    request<import('./types').CheckoutResult>(`/checkout/${sessionId}`, { method: 'POST' }),

  listApprovals: (state = 'pending') =>
    request<import('./types').Approval[]>(`/approvals?state=${state}`),

  decideApproval: (approvalId: string, decision: 'approve' | 'reject', actor: string, note?: string) =>
    request<import('./types').DecideResult>(`/approvals/${approvalId}/decide`, {
      method: 'POST',
      body: JSON.stringify({ decision, actor, note }),
    }),

  explainSession: (sessionId: string) =>
    request<import('./types').Explanation>(`/audit/session/${sessionId}/explain`),

  verifyChain: () => request<import('./types').ChainVerification>('/audit/verify'),

  liveMetrics: () => request<import('./types').LiveMetrics>('/metrics/live'),
}
