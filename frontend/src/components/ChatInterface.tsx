import { useEffect, useRef } from 'react'
import { useChat } from '../hooks/useChat'
import ChatMessages from './ChatMessages'
import ChatInput from './ChatInput'
import EventDataPanel from './EventDataPanel'

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
    connect,
    sendMessage,
    sendMessageRest,
    isConnected
  } = useChat(sessionId)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const connectionAttempted = useRef(false)

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
