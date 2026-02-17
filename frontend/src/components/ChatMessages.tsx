import { RefObject } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Message } from '../types'

interface Props {
  messages: Message[]
  isLoading: boolean
  messagesEndRef: RefObject<HTMLDivElement>
}

export default function ChatMessages({ messages, isLoading, messagesEndRef }: Props) {
  const lastMsg = messages[messages.length - 1]
  const isStreaming = isLoading && lastMsg?.role === 'assistant'

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      {messages.length === 0 ? (
        <div className="flex items-center justify-center h-full text-center">
          <div>
            <div className="text-6xl mb-4">🍽️</div>
            <p className="text-slate-600 text-lg">
              Welcome! Tell me about your event and I'll help you plan the perfect menu.
            </p>
          </div>
        </div>
      ) : (
        messages.map((msg, idx) => {
          const isLastAssistant = isStreaming && idx === messages.length - 1
          return (
            <div
              key={idx}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-md lg:max-w-lg px-4 py-3 rounded-lg ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white rounded-br-none'
                    : 'bg-white text-slate-900 border border-slate-200 rounded-bl-none'
                }`}
              >
                <div
                  className={`text-sm chat-markdown ${msg.role === 'user' ? 'chat-markdown--user' : ''}`}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                  {isLastAssistant && (
                    <span className="inline-block w-0.5 h-3.5 bg-slate-500 ml-0.5 align-middle animate-pulse" />
                  )}
                </div>
                {msg.timestamp && (
                  <p className={`text-xs mt-1 ${
                    msg.role === 'user' ? 'text-indigo-100' : 'text-slate-400'
                  }`}>
                    {msg.timestamp.toLocaleTimeString()}
                  </p>
                )}
              </div>
            </div>
          )
        })
      )}

      {isLoading && !isStreaming && (
        <div className="flex justify-start">
          <div className="bg-white border border-slate-200 px-4 py-3 rounded-lg rounded-bl-none">
            <div className="flex space-x-2">
              <div className="w-2 h-2 bg-slate-400 rounded-full animate-bounce"></div>
              <div className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
              <div className="w-2 h-2 bg-slate-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
            </div>
          </div>
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>
  )
}
