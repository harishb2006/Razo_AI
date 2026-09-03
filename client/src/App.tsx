import { NavLink, Route, Routes } from 'react-router-dom'

import { AuditPage } from './pages/AuditPage'
import { ChatPage } from './pages/ChatPage'
import { ConsolePage } from './pages/ConsolePage'

function Tab({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `rounded-lg px-3 py-1.5 text-sm font-medium ${
          isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-canvas'
        }`
      }
    >
      {children}
    </NavLink>
  )
}

export default function App() {
  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center gap-4 border-b border-line bg-white px-6 py-3">
        <div>
          <h1 className="text-sm font-bold tracking-tight">Razo_AI</h1>
          <p className="text-[11px] text-muted">The AI proposes. Ordinary code disposes.</p>
        </div>
        <nav className="ml-auto flex gap-1">
          <Tab to="/">Buyer chat</Tab>
          <Tab to="/console">Merchant console</Tab>
        </nav>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<ChatPage />} />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/console/audit/:sessionId" element={<AuditPage />} />
        </Routes>
      </main>
    </div>
  )
}
