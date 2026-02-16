import { useState, useEffect, useRef } from 'react'
import ChatInterface from './components/ChatInterface'

function App() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const initializingRef = useRef(false)

  useEffect(() => {
    if (initializingRef.current) return
    initializingRef.current = true

    const initializeSession = async () => {
      try {
        const response = await fetch('/api/sessions', { method: 'POST' })
        const data = await response.json()
        setSessionId(data.session_id)
      } catch (error) {
        console.error('Failed to create session:', error)
      } finally {
        setLoading(false)
      }
    }

    initializeSession()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto mb-4"></div>
          <p className="text-indigo-600 font-medium">Initializing your event planner...</p>
        </div>
      </div>
    )
  }

  if (!sessionId) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
        <div className="text-center">
          <p className="text-red-600">Failed to initialize session</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      <ChatInterface sessionId={sessionId} />
    </div>
  )
}

export default App
