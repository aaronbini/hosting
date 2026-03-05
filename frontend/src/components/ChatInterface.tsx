import { useCallback, useEffect, useRef, useState } from 'react'
import { useChat } from '../hooks/useChat'
import ChatMessages from './ChatMessages'
import ChatInput from './ChatInput'
import EventDataPanel from './EventDataPanel'
import RecipeUploadPanel from './RecipeUploadPanel'
import type { ActiveCard, EventData, Message } from '../types'
import { apiFetch } from '../api'


interface Props {
  sessionId: string
  initialMessages?: Message[]
  initialEventData?: EventData | null
  initialCompletionScore?: number
  initialIsComplete?: boolean
  initialActiveCard?: ActiveCard
  onNewSession: () => void
}

export default function ChatInterface({
  sessionId,
  initialMessages,
  initialEventData,
  initialCompletionScore,
  initialIsComplete,
  initialActiveCard,
  onNewSession,
}: Props) {
  const {
    messages,
    isLoading,
    error,
    eventData,
    completionScore,
    isComplete,
    isAwaitingReview,
    excludedItems,
    activeCard,
    toggleExcludedItem,
    connect,
    sendMessage,
    approveShoppingList,
    confirmMenu,
    confirmRecipes,
    selectOutputs,
    isConnected
  } = useChat(sessionId, { initialMessages, initialEventData, initialCompletionScore, initialIsComplete, initialActiveCard })

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const connectionAttempted = useRef(false)
  const [isGoogleConnected, setIsGoogleConnected] = useState(false)
  const [showGoogleConnectedBanner, setShowGoogleConnectedBanner] = useState(false)
  const [bannerVisible, setBannerVisible] = useState(false)

  const needsGoogleOutput =
    eventData?.output_formats?.includes('google_tasks') ||
    eventData?.output_formats?.includes('google_sheet')

  const needsGoogleAuth = needsGoogleOutput && !isGoogleConnected

  const handleConnectGoogle = useCallback(async () => {
    const res = await apiFetch(`/api/auth/google/start?session_id=${sessionId}`)
    if (!res.ok) return
    const { auth_url } = await res.json()
    const popup = window.open(auth_url, 'google_oauth', 'width=500,height=650')

    const onMessage = (event: MessageEvent) => {
      if (event.data === 'google_auth_complete') {
        setIsGoogleConnected(true)
        setShowGoogleConnectedBanner(true)
        setTimeout(() => setBannerVisible(true), 10)
        setTimeout(() => setBannerVisible(false), 2500)
        setTimeout(() => setShowGoogleConnectedBanner(false), 3000)
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
    sendMessage(message)
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-h-0 max-w-4xl mx-auto w-full">
        {/* Header */}
        <div className="bg-white border-b border-slate-200 px-6 py-3 shrink-0">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500">Let's plan your perfect event together</p>
            <button
              onClick={onNewSession}
              className="px-3 py-1.5 text-sm font-medium rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 hover:border-slate-300 transition-all shadow-sm shrink-0"
            >
              + New Event
            </button>
          </div>
          {error && (
            <div className="mt-2 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
              {error}
            </div>
          )}
        </div>

        {/* Messages Area */}
        <ChatMessages
          messages={messages}
          isLoading={isLoading}
          messagesEndRef={messagesEndRef}
          excludedItems={excludedItems}
          activeCard={activeCard}
          onToggleExcluded={toggleExcludedItem}
          onSelectOutputs={selectOutputs}
          onConfirmMenu={confirmMenu}
          onConfirmRecipes={confirmRecipes}
          isAwaitingReview={isAwaitingReview}
          onApprove={approveShoppingList}
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

        {/* Connect Google — visible when a Google output is selected and not yet authenticated */}
        {needsGoogleAuth && (
          <div className="border-t border-slate-100 bg-blue-50 px-5 py-3 flex items-center gap-4">
            <div className="flex-1">
              <p className="text-sm font-medium text-slate-700">Connect your Google account to continue</p>
              <p className="text-xs text-slate-400 mt-0.5">
                Only used to create your Tasks list or Sheet. Read-only access is never requested.
              </p>
            </div>
            <button
              onClick={handleConnectGoogle}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-all shadow-sm shrink-0"
            >
              Connect Google
            </button>
          </div>
        )}
        {needsGoogleOutput && showGoogleConnectedBanner && (
          <div
            className="border-t border-slate-100 bg-green-50 px-5 py-3 transition-opacity duration-500"
            style={{ opacity: bannerVisible ? 1 : 0 }}
          >
            <p className="text-sm font-medium text-green-700">✓ Google account connected</p>
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
