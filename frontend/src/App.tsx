import { useState, useEffect, useRef, useCallback } from 'react'
import { apiFetch } from './api'
import ChatInterface from './components/ChatInterface'
import LoginPage from './components/LoginPage'
import PlansView from './components/PlansView'
import type { ActiveCard, EventData, Message, OutputOption, User } from './types'
import posthog from './posthog'

type AuthState = 'loading' | 'unauthenticated' | User
type AppView = 'planner' | 'plans'

interface SessionInit {
  sessionId: string
  messages: Message[]
  eventData: EventData | null
  completionScore: number
  isComplete: boolean
  initialActiveCard: ActiveCard
}

// Output options mirror what the WS handler sends; needed to re-attach the picker on restore.
const OUTPUT_OPTIONS: OutputOption[] = [
  { value: 'google_sheet', label: 'Google Sheet', description: 'Formula-driven spreadsheet, quantities auto-adjust' },
  { value: 'google_tasks', label: 'Google Tasks', description: 'Checklist format, great for shopping on your phone' },
  { value: 'in_chat', label: 'In-chat list', description: 'Formatted list right here in the conversation' },
]

function buildRestoredMessages(
  history: { role: string; content: string }[],
): Message[] {
  return history.map(m => ({
    role: m.role as 'user' | 'assistant',
    content: m.content,
    timestamp: new Date(),
  }))
}

async function fetchSessionInit(sessionId: string): Promise<SessionInit> {
  const r = await apiFetch(`/api/sessions/${sessionId}`)
  if (!r.ok) throw new Error('Session not found')
  const data = await r.json()
  const stage: string = data.event_data?.conversation_stage ?? 'gathering'
  const initialActiveCard: ActiveCard = stage === 'selecting_output'
    ? { type: 'output_selection', options: OUTPUT_OPTIONS }
    : null
  posthog.capture('session restored', {
    session_id: sessionId,
    stage,
    message_count: (data.conversation_history ?? []).length,
  })
  return {
    sessionId,
    messages: buildRestoredMessages(data.conversation_history ?? []),
    eventData: data.event_data ?? null,
    completionScore: data.event_data?.completion_score ?? 0,
    isComplete: data.event_data?.is_complete ?? false,
    initialActiveCard,
  }
}

async function createNewSession(): Promise<SessionInit> {
  const r = await apiFetch(`/api/sessions`, { method: 'POST' })
  if (!r.ok) throw new Error('Failed to create session')
  const data = await r.json()
  posthog.capture('session created', {
    session_id: data.session_id,
  })
  return {
    sessionId: data.session_id,
    messages: [],
    eventData: null,
    completionScore: 0,
    isComplete: false,
    initialActiveCard: null,
  }
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center h-screen bg-slate-50">
      <div className="text-center">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-500 mx-auto mb-4" />
        <p className="text-slate-500 font-medium text-sm">{label}</p>
      </div>
    </div>
  )
}

function AppHeader({
  user,
  view,
  onViewChange,
  onLogout,
}: {
  user: User
  view: AppView
  onViewChange: (v: AppView) => void
  onLogout: () => void
}) {
  return (
    <header className="bg-white border-b border-slate-200 px-6 flex items-center gap-6 shrink-0 shadow-sm">
      <div className="flex items-center gap-2 mr-2 py-4">
        <span className="text-lg leading-none">🍽️</span>
        <span className="text-sm font-semibold text-slate-800 tracking-tight">Hosting Helper</span>
      </div>
      <nav className="flex self-stretch">
        {(['planner', 'plans'] as AppView[]).map(v => (
          <button
            key={v}
            onClick={() => onViewChange(v)}
            className={`px-4 text-sm font-medium border-b-2 transition-colors ${
              view === v
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'
            }`}
          >
            {v === 'planner' ? 'Planner' : 'My Plans'}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {user.picture && (
          <img src={user.picture} alt={user.name} className="w-7 h-7 rounded-full ring-2 ring-slate-100" />
        )}
        <span className="text-sm text-slate-600 hidden sm:inline">{user.name}</span>
        <button
          onClick={onLogout}
          className="text-xs text-slate-400 hover:text-slate-700 transition-colors px-2 py-1 rounded hover:bg-slate-100"
        >
          Sign out
        </button>
      </div>
    </header>
  )
}

function App() {
  const [authState, setAuthState] = useState<AuthState>('loading')
  const [view, setView] = useState<AppView>('planner')
  const [sessionInit, setSessionInit] = useState<SessionInit | null>(null)
  const [sessionError, setSessionError] = useState(false)
  const initializingRef = useRef(false)

  useEffect(() => {
    apiFetch(`/api/auth/me`)
      .then(r => (r.ok ? r.json() : null))
      .then(user => setAuthState(user ?? 'unauthenticated'))
      .catch(() => setAuthState('unauthenticated'))
  }, [])

  useEffect(() => {
    if (typeof authState === 'string') return
    const user = authState as User
    posthog.identify(user.id, {
      email: user.email,
      name: user.name,
    })
    posthog.capture('user signed in', {
      email: user.email,
      name: user.name,
    })
  }, [authState])

  useEffect(() => {
    if (typeof authState === 'string') return
    if (initializingRef.current) return
    initializingRef.current = true
    async function initSession() {
      const r = await apiFetch(`/api/sessions`)
      const { sessions } = await r.json() as { sessions: { session_id: string; stage: string }[] }
      const active = sessions.find(s => s.stage !== 'complete')
      if (active) return fetchSessionInit(active.session_id)
      return createNewSession()
    }

    initSession()
      .then(setSessionInit)
      .catch(() => setSessionError(true))
  }, [authState])

  const handleNewSession = useCallback(async () => {
    setSessionInit(null)
    setSessionError(false)
    setView('planner')
    createNewSession()
      .then(setSessionInit)
      .catch(() => setSessionError(true))
  }, [authState])

  const handleLogout = useCallback(async () => {
    await apiFetch(`/api/auth/logout`, { method: 'POST' })
    setAuthState('unauthenticated')
    setSessionInit(null)
    initializingRef.current = false
  }, [])

  if (authState === 'loading') return <Spinner label="Loading..." />
  if (authState === 'unauthenticated') return <LoginPage />

  const user = authState as User

  // Still initializing session
  if (!sessionInit && !sessionError) return <Spinner label="Initializing your event planner..." />

  if (sessionError) {
    return (
      <div className="flex items-center justify-center h-screen bg-slate-50">
        <div className="text-center">
          <p className="text-red-600 mb-4">Failed to initialize session</p>
          <button
            onClick={handleNewSession}
            className="px-4 py-2 text-sm font-medium rounded bg-indigo-600 text-white hover:bg-indigo-700"
          >
            Try again
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      <AppHeader user={user} view={view} onViewChange={setView} onLogout={handleLogout} />

      {view === 'planner' && sessionInit ? (
        <ChatInterface
          key={sessionInit.sessionId}
          sessionId={sessionInit.sessionId}
          initialMessages={sessionInit.messages}
          initialEventData={sessionInit.eventData}
          initialCompletionScore={sessionInit.completionScore}
          initialIsComplete={sessionInit.isComplete}
          initialActiveCard={sessionInit.initialActiveCard}
          onNewSession={handleNewSession}
          userId={user.id}
        />
      ) : view === 'plans' ? (
        <PlansView onStartPlanning={() => setView('planner')} userId={user.id} />
      ) : null}

      <footer className="shrink-0 text-center py-2 text-xs text-slate-400">
        <a href="/privacy.html" className="hover:text-slate-600 transition-colors">Privacy Policy</a>
      </footer>
    </div>
  )
}

export default App
