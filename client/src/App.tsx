import { useEffect, useRef, useState } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1'

type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

type CartView = {
  items: { sku: string; qty: number; line_total_paise: number }[]
  total_paise: number
}

function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`
}

function App() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [cart, setCart] = useState<CartView | null>(null)
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetch(`${API_BASE}/chat/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    })
      .then((r) => r.json())
      .then((data) => setSessionId(data.session_id))
      .catch(() => setSessionId(null))
  }, [])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight })
  }, [messages])

  async function send() {
    const text = input.trim()
    if (!text || !sessionId || sending) return

    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setInput('')
    setSending(true)

    try {
      const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      })
      const data = await res.json()
      setMessages((prev) => [...prev, { role: 'assistant', content: data.reply }])
      setCart(data.cart)
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: "Couldn't reach the server — is the backend running?" },
      ])
    } finally {
      setSending(false)
    }
  }

  return (
    <div id="app">
      <header>
        <h1>Razo_AI</h1>
        {cart && cart.items.length > 0 && (
          <span className="cart-pill">
            {cart.items.reduce((n, i) => n + i.qty, 0)} items · {formatPaise(cart.total_paise)}
          </span>
        )}
      </header>

      <div className="messages" ref={listRef}>
        {messages.length === 0 && (
          <p className="empty">Ask for something — e.g. "I need running shoes under ₹5,000".</p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.content}
          </div>
        ))}
        {sending && <div className="bubble assistant pending">…</div>}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={sessionId ? 'Type a message…' : 'Connecting…'}
          disabled={!sessionId}
        />
        <button type="submit" disabled={!sessionId || !input.trim() || sending}>
          Send
        </button>
      </form>
    </div>
  )
}

export default App
