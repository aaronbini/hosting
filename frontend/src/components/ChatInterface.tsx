import { useCallback, useEffect, useRef, useState } from 'react'
import { useChat } from '../hooks/useChat'
import ChatMessages from './ChatMessages'
import ChatInput from './ChatInput'
import EventDataPanel from './EventDataPanel'
import RecipeUploadPanel from './RecipeUploadPanel'

interface Props {
  sessionId: string
}

export default function ChatInterface({ sessionId }: Props) {
  const {
    messages,
    isLoading,
    error,
    eventData,
    completionScore,
    isComplete,
    isAwaitingReview,
    connect,
    sendMessage,
    sendMessageRest,
    approveShoppingList,
    isConnected
  } = useChat(sessionId)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const connectionAttempted = useRef(false)
  const [isGoogleConnected, setIsGoogleConnected] = useState(false)

  const needsGoogleAuth = 
    eventData?.output_formats?.includes('google_tasks') &&
    !isGoogleConnected

  const handleConnectGoogle = useCallback(async () => {
    const res = await fetch(`/api/auth/google/start?session_id=${sessionId}`)
    if (!res.ok) return
    const { auth_url } = await res.json()
    const popup = window.open(auth_url, 'google_oauth', 'width=500,height=650')

    const onMessage = (event: MessageEvent) => {
      if (event.data === 'google_auth_complete') {
        setIsGoogleConnected(true)
        window.removeEventListener('message', onMessage)
        popup?.close()
        // Re-trigger the agent now that credentials are set
        sendMessage("I've connected my Google account.")
      }
    }
    window.addEventListener('message', onMessage)
  }, [sessionId, sendMessage])

  useEffect(() => {
    if (connectionAttempted.current) return
    connectionAttempted.current = true
    connect()
  }, [connect])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSendMessage = (message: string) => {
    if (isConnected) {
      sendMessage(message)
    } else {
      sendMessageRest(message)
    }
  }

  return (
    <div className="flex h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col max-w-4xl mx-auto w-full">
        {/* Header */}
        <div className="bg-white border-b border-slate-200 p-6 shadow-sm">
          <h1 className="text-3xl font-bold text-slate-900">
            Food Event Planner
          </h1>
          <p className="text-slate-600 mt-2">
            Let's plan your perfect event together
          </p>
          {error && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded">
              {error}
            </div>
          )}
        </div>

        {/* Messages Area */}
        <ChatMessages
          messages={messages}
          isLoading={isLoading}
          messagesEndRef={messagesEndRef}
        />

        {/* Recipe upload — visible only while there are recipes awaiting user input */}
        {eventData && eventData.meal_plan?.recipes.filter(r => r.awaiting_user_input).length > 0 && (
          <RecipeUploadPanel
            sessionId={sessionId}
            recipes={eventData.meal_plan.recipes.filter(r => r.awaiting_user_input)}
            onUploadComplete={(dishName) =>
              handleSendMessage(`I uploaded a recipe file for ${dishName}.`)
            }
          />
        )}

        {/* Connect Google — visible during output selection when Google Tasks is chosen */}
        {needsGoogleAuth && (
          <div className="border-t border-slate-200 bg-blue-50 px-4 py-3 flex items-center gap-3">
            <div className="flex-1">
              <p className="text-sm text-slate-600">
                Google Tasks selected — connect your Google account to deliver the list.
              </p>
              <p className="text-xs text-slate-400 mt-0.5">
                Only used to create a new task list. Cannot read, edit, or delete your existing tasks.
              </p>
            </div>
            <button
              onClick={handleConnectGoogle}
              className="px-5 py-2 text-sm font-medium rounded bg-blue-600 text-white hover:bg-blue-700 transition-colors shrink-0"
            >
              Connect Google
            </button>
          </div>
        )}
        {eventData?.output_formats?.includes('google_tasks') &&
          isGoogleConnected && (
          <div className="border-t border-slate-200 bg-green-50 px-4 py-3">
            <p className="text-sm text-green-700">✓ Google account connected</p>
          </div>
        )}

        {/* Approve button — visible while agent awaits review */}
        {isAwaitingReview && (
          <div className="border-t border-slate-200 bg-green-50 px-4 py-3 flex items-center gap-3">
            <p className="text-sm text-slate-600 flex-1">
              Looks good? Approve the list to continue, or type corrections below.
            </p>
            <button
              onClick={approveShoppingList}
              className="px-5 py-2 text-sm font-medium rounded bg-green-600 text-white hover:bg-green-700 transition-colors"
            >
              Approve
            </button>
          </div>
        )}

        {/* Input Area */}
        <ChatInput
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          isConnected={isConnected}
        />
      </div>

      {/* Event Data Sidebar */}
      {eventData && (
        <EventDataPanel
          eventData={eventData}
          completionScore={completionScore}
          isComplete={isComplete}
        />
      )}
    </div>
  )
}
