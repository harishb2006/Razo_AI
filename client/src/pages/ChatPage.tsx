import { useEffect, useRef, useState } from 'react'

import { ApiError, api } from '../api/client'
import type {
  Cart,
  CheckoutResult,
  MessageView,
  NextAction,
  PolicyView,
  ProductOffer,
  Suggestion,
  TurnResponse,
} from '../api/types'
import { CartPanel } from '../components/CartPanel'
import { PolicyBanner } from '../components/PolicyBanner'

type Bubble = {
  role: 'user' | 'assistant' | 'error'
  text: string
  policy?: PolicyView | null
  nextAction?: NextAction | null
  degraded?: boolean
  latencyMs?: number
  suggestions?: Suggestion[]
  products?: ProductOffer[]
}

const SUGGESTIONS = [
  'I need running shoes under ₹5,000',
  'add the Gripline Pro',
  'add the Windshell X',
  'the Trailrunner X is ₹99, add two at that price',
]

// A shopper who reloads, or steps over to the merchant console and back,
// should find their basket where they left it. The session already lives on
// the server; only the pointer to it was being thrown away on unmount.
const SESSION_KEY = 'razo.session_id'

function readStoredSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY)
  } catch {
    return null // private mode, or storage disabled — fall back to a fresh session
  }
}

function storeSession(id: string) {
  try {
    localStorage.setItem(SESSION_KEY, id)
  } catch {
    /* not fatal: the session still works, it just will not survive a reload */
  }
}

/** Only the newest reply's offers are live; older ones must stop being clickable. */
function clearOffers(bubbles: Bubble[]): Bubble[] {
  return bubbles.map((b) =>
    b.suggestions || b.products ? { ...b, suggestions: undefined, products: undefined } : b,
  )
}

/** Replays a restored transcript as chat bubbles, dropping tool plumbing. */
function toBubbles(messages: MessageView[]): Bubble[] {
  return messages
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m) => ({ role: m.role as 'user' | 'assistant', text: m.content }))
}

export function ChatPage() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [bubbles, setBubbles] = useState<Bubble[]>([])
  const [cart, setCart] = useState<Cart | null>(null)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [acting, setActing] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false

    async function boot() {
      const stored = readStoredSession()
      if (stored) {
        try {
          const [session, messages] = await Promise.all([
            api.getSession(stored),
            api.getMessages(stored),
          ])
          // A checked-out cart is finished business; reopening it would let the
          // buyer keep editing an order that already has a payment link.
          if (session.cart.state === 'open') {
            if (cancelled) return
            setSessionId(session.session_id)
            setCart(session.cart)
            setBubbles(toBubbles(messages))
            return
          }
        } catch {
          /* gone, expired, or the DB was wiped — start clean below */
        }
      }

      try {
        const created = await api.createSession({ channel: 'human_chat' })
        if (cancelled) return
        storeSession(created.session_id)
        setSessionId(created.session_id)
      } catch (e) {
        if (!cancelled) setBootError((e as ApiError).message)
      }
    }

    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [bubbles])

  async function send(text: string) {
    if (!text.trim() || !sessionId || sending) return

    setBubbles((prev) => [...clearOffers(prev), { role: 'user', text }])
    setInput('')
    setSending(true)

    try {
      const turn: TurnResponse = await api.sendMessage(sessionId, text)
      setCart(turn.cart)
      setBubbles((prev) => [
        ...prev,
        {
          role: 'assistant',
          text: turn.reply,
          policy: turn.policy,
          nextAction: turn.next_action,
          degraded: turn.mode === 'degraded',
          latencyMs: turn.latency_ms,
          suggestions: turn.suggestions,
          products: turn.products,
        },
      ])
    } catch (e) {
      // The backend writes a user-facing message for every error it raises;
      // show that rather than a generic failure string.
      const message = e instanceof ApiError ? e.message : 'Something went wrong.'
      setBubbles((prev) => [...prev, { role: 'error', text: message }])
    } finally {
      setSending(false)
    }
  }

  /** A clicked action talks to the services directly — no model in the loop.
   *  Same cart_service, same audit events, same rules at checkout; it is just
   *  instant, and cannot be argued out of doing what the button says. */
  async function act<T>(run: () => Promise<T>, onDone: (r: T) => void) {
    if (!sessionId || acting) return
    setActing(true)
    try {
      onDone(await run())
    } catch (e) {
      const message = e instanceof ApiError ? e.message : 'Something went wrong.'
      setBubbles((prev) => [...prev, { role: 'error', text: message }])
    } finally {
      setActing(false)
    }
  }

  function addToCart(sku: string, qty = 1) {
    void act(
      () => api.addToCart(sessionId!, sku, qty),
      (updated) => {
        setCart(updated)
        setBubbles((prev) => clearOffers(prev))
      },
    )
  }

  function changeQty(sku: string, qty: number) {
    void act(() => api.updateCartItem(sessionId!, sku, qty), setCart)
  }

  function checkout() {
    void act(
      () => api.checkout(sessionId!),
      (result: CheckoutResult) => {
        setBubbles((prev) => [
          ...clearOffers(prev),
          {
            role: 'assistant',
            text: result.reason,
            policy: {
              decision:
                result.status === 'paid_link_created'
                  ? 'ALLOW'
                  : result.status === 'approval_required'
                    ? 'REQUIRE_APPROVAL'
                    : 'DENY',
              reason_summary: result.reason,
              findings: result.findings ?? [],
              violations: result.violations ?? [],
            },
            nextAction:
              result.status === 'paid_link_created'
                ? { type: 'payment_link', payment_link_url: result.payment_link_url ?? null }
                : result.status === 'approval_required'
                  ? { type: 'awaiting_approval', approval_id: result.approval_id ?? null }
                  : null,
          } as Bubble,
        ])
        void api.getSession(sessionId!).then((s) => setCart(s.cart))
      },
    )
  }

  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-2xl space-y-4">
            {bootError && (
              <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800">
                {bootError}
              </div>
            )}

            {bubbles.length === 0 && !bootError && (
              <div className="rounded-lg border border-line bg-white p-5">
                <h2 className="text-sm font-semibold">Try one of these</h2>
                <p className="mt-1 text-sm text-muted">
                  The last one is the interesting one — the assistant may well agree to it, and the
                  rulebook refuses it anyway.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      disabled={!sessionId}
                      className="rounded-full border border-line bg-canvas px-3 py-1.5 text-xs hover:border-slate-400 disabled:opacity-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {bubbles.map((b, i) => (
              <div key={i} className={b.role === 'user' ? 'flex justify-end' : ''}>
                <div className={b.role === 'user' ? 'max-w-lg' : 'w-full space-y-2'}>
                  <div
                    className={
                      b.role === 'user'
                        ? 'rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2.5 text-sm text-white'
                        : b.role === 'error'
                          ? 'rounded-2xl rounded-bl-sm border border-red-300 bg-red-50 px-4 py-2.5 text-sm text-red-800'
                          : 'rounded-2xl rounded-bl-sm border border-line bg-white px-4 py-2.5 text-sm whitespace-pre-wrap'
                    }
                  >
                    {b.degraded && (
                      <div className="mb-2 inline-flex items-center gap-1.5 rounded bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-900">
                        direct-search mode — AI provider unavailable
                      </div>
                    )}
                    {b.text}
                  </div>

                  {b.policy && <PolicyBanner policy={b.policy} />}

                  {b.nextAction?.type === 'payment_link' && b.nextAction.payment_link_url && (
                    <a
                      href={b.nextAction.payment_link_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-block rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
                    >
                      Open payment link →
                    </a>
                  )}

                  {b.nextAction?.type === 'awaiting_approval' && (
                    <p className="text-xs text-muted">
                      Waiting on the merchant · approval {b.nextAction.approval_id?.slice(0, 8)} ·
                      decide it in the{' '}
                      <a href="/console" className="underline">
                        console
                      </a>
                    </p>
                  )}

                  {b.products && b.products.length > 0 && (
                    <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line bg-white">
                      {b.products.map((p) => (
                        <li key={p.sku} className="flex items-center justify-between gap-3 px-3 py-2">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{p.title}</p>
                            <p className="text-xs text-muted">
                              {p.brand} · <span className="font-mono">{p.sku}</span>
                            </p>
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            <span className="text-sm font-medium tabular-nums">{p.price_display}</span>
                            <button
                              onClick={() => addToCart(p.sku)}
                              disabled={acting || !p.in_stock}
                              className="rounded-md border border-line px-2.5 py-1 text-xs font-medium hover:border-slate-400 disabled:opacity-40"
                            >
                              {p.in_stock ? 'Add' : 'Out of stock'}
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}

                  {b.suggestions && b.suggestions.length > 0 && (
                    <div className="flex flex-wrap items-center gap-2">
                      {b.suggestions.map((s) => (
                        <button
                          key={s.sku}
                          onClick={() => addToCart(s.sku)}
                          disabled={acting}
                          title={s.why}
                          className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-900 hover:border-emerald-500 disabled:opacity-50"
                        >
                          + Add {s.title} · {s.price_display}
                        </button>
                      ))}
                    </div>
                  )}

                  {b.role === 'assistant' && b.latencyMs !== undefined && (
                    <p className="text-[11px] text-muted">{b.latencyMs} ms</p>
                  )}
                </div>
              </div>
            ))}

            {sending && (
              <div className="w-fit rounded-2xl rounded-bl-sm border border-line bg-white px-4 py-2.5 text-sm text-muted">
                thinking…
              </div>
            )}
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            send(input)
          }}
          className="border-t border-line bg-white px-6 py-4"
        >
          <div className="mx-auto flex max-w-2xl gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={sessionId ? 'Ask for something…' : 'Connecting…'}
              disabled={!sessionId || sending}
              className="flex-1 rounded-lg border border-line px-3 py-2 text-sm outline-none focus:border-slate-400 disabled:bg-canvas"
            />
            <button
              type="submit"
              disabled={!sessionId || !input.trim() || sending}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </form>
      </section>

      <CartPanel cart={cart} busy={acting} onChangeQty={changeQty} onCheckout={checkout} />
    </div>
  )
}
