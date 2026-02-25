import { useState, useEffect, useRef, useCallback } from 'react'
import ChatInterface from './components/ChatInterface'
import LoginPage from './components/LoginPage'
import PlansView from './components/PlansView'
import type { EventData, Message, OutputOption, User } from './types'

type AuthState = 'loading' | 'unauthenticated' | User
type AppView = 'planner' | 'plans'

interface SessionInit {
  sessionId: string
  messages: Message[]
  eventData: EventData | null
  completionScore: number
  isComplete: boolean
}

// Output options mirror what the WS handler sends; needed to re-attach the picker on restore.
const OUTPUT_OPTIONS: OutputOption[] = [
  { value: 'google_sheet', label: 'Google Sheet', description: 'Formula-driven spreadsheet, quantities auto-adjust' },
  { value: 'google_tasks', label: 'Google Tasks', description: 'Checklist format, great for shopping on your phone' },
  { value: 'in_chat', label: 'In-chat list', description: 'Formatted list right here in the conversation' },
]

function buildRestoredMessages(
  history: { role: string; content: string }[],
  stage: string,
): Message[] {
  const messages: Message[] = history.map(m => ({
    role: m.role as 'user' | 'assistant',
    content: m.content,
    timestamp: new Date(),
  }))
  if (stage === 'selecting_output') {
    messages.push({
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      outputOptions: OUTPUT_OPTIONS,
    })
  }
  return messages
}

async function fetchSessionInit(sessionId: string): Promise<SessionInit> {
  const r = await fetch(`/api/sessions/${sessionId}`, { credentials: 'include' })
  if (!r.ok) throw new Error('Session not found')
  const data = await r.json()
  const stage: string = data.event_data?.conversation_stage ?? 'gathering'
  return {
    sessionId,
    messages: buildRestoredMessages(data.conversation_history ?? [], stage),
    eventData: data.event_data ?? null,
    completionScore: data.event_data?.completion_score ?? 0,
    isComplete: data.event_data?.is_complete ?? false,
  }
}

async function createNewSession(): Promise<SessionInit> {
  const r = await fetch('/api/sessions', { method: 'POST', credentials: 'include' })
  if (!r.ok) throw new Error('Failed to create session')
  const data = await r.json()
  return {
    sessionId: data.session_id,
    messages: [],
    eventData: null,
    completionScore: 0,
    isComplete: false,
  }
}

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto mb-4" />
        <p className="text-indigo-600 font-medium">{label}</p>
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
    <header className="bg-white border-b border-slate-200 px-6 py-3 flex items-center gap-6 shrink-0 shadow-sm">
      <span className="text-lg font-bold text-slate-900 mr-2">Food Event Planner</span>
      <nav className="flex gap-1">
        {(['planner', 'plans'] as AppView[]).map(v => (
          <button
            key={v}
            onClick={() => onViewChange(v)}
            className={`px-4 py-1.5 text-sm font-medium rounded transition-colors ${
              view === v
                ? 'bg-indigo-100 text-indigo-700'
                : 'text-slate-600 hover:bg-slate-100'
            }`}
          >
            {v === 'planner' ? 'Planner' : 'My Plans'}
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3">
        {user.picture && (
          <img src={user.picture} alt={user.name} className="w-7 h-7 rounded-full" />
        )}
        <span className="text-sm text-slate-700 hidden sm:inline">{user.name}</span>
        <button
          onClick={onLogout}
          className="text-sm text-slate-500 hover:text-slate-800 transition-colors"
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
    fetch('/api/auth/me', { credentials: 'include' })
      .then(r => (r.ok ? r.json() : null))
      .then(user => setAuthState(user ?? 'unauthenticated'))
      .catch(() => setAuthState('unauthenticated'))
  }, [])

  useEffect(() => {
    if (typeof authState === 'string') return
    if (initializingRef.current) return
    initializingRef.current = true

    async function initSession() {
      const r = await fetch('/api/sessions', { credentials: 'include' })
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
  }, [])

  const handleLogout = useCallback(async () => {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
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
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
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
    <div className="h-screen flex flex-col bg-gradient-to-br from-blue-50 to-indigo-100">
      <AppHeader user={user} view={view} onViewChange={setView} onLogout={handleLogout} />

      {view === 'planner' && sessionInit ? (
        <ChatInterface
          key={sessionInit.sessionId}
          sessionId={sessionInit.sessionId}
          initialMessages={sessionInit.messages}
          initialEventData={sessionInit.eventData}
          initialCompletionScore={sessionInit.completionScore}
          initialIsComplete={sessionInit.isComplete}
          onNewSession={handleNewSession}
        />
      ) : view === 'plans' ? (
        <PlansView onStartPlanning={() => setView('planner')} />
      ) : null}
    </div>
  )
}

export default App
