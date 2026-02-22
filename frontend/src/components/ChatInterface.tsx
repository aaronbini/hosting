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
    eventData?.conversation_stage === 'selecting_output' &&
    eventData.output_formats?.includes('google_tasks') &&
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
      }
    }
    window.addEventListener('message', onMessage)
  }, [sessionId])

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

        {/* Recipe upload — visible during recipe_confirmation, when promises are pending, or when a file upload is pending */}
        {eventData && (eventData.conversation_stage === 'recipe_confirmation' || (eventData.recipe_promises && eventData.recipe_promises.length > 0) || !!eventData.pending_upload_dish) && (
          <RecipeUploadPanel
            sessionId={sessionId}
            mealPlan={eventData.meal_plan}
            pendingDish={eventData.pending_upload_dish}
            onUploadComplete={(dishName) =>
              handleSendMessage(`I uploaded a recipe file for ${dishName}.`)
            }
          />
        )}

        {/* Connect Google — visible during output selection when Google Tasks is chosen */}
        {needsGoogleAuth && (
          <div className="border-t border-slate-200 bg-blue-50 px-4 py-3 flex items-center gap-3">
            <p className="text-sm text-slate-600 flex-1">
              Google Tasks selected — connect your Google account to deliver the list.
            </p>
            <button
              onClick={handleConnectGoogle}
              className="px-5 py-2 text-sm font-medium rounded bg-blue-600 text-white hover:bg-blue-700 transition-colors"
            >
              Connect Google
            </button>
          </div>
        )}
        {eventData?.conversation_stage === 'selecting_output' &&
          eventData.output_formats?.includes('google_tasks') &&
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
