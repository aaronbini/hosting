import { useState } from 'react'
import { Send } from 'lucide-react'

interface Props {
  onSendMessage: (message: string) => void
  isLoading: boolean
  isConnected: boolean
}

export default function ChatInput({ onSendMessage, isLoading, isConnected }: Props) {
  const [input, setInput] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!input.trim() || isLoading) return

    onSendMessage(input)
    setInput('')
  }

  return (
    <form onSubmit={handleSubmit} className="bg-white border-t border-slate-100 px-6 py-4">
      <div className="flex gap-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Tell me about your event..."
          disabled={isLoading}
          className="flex-1 px-4 py-2.5 border border-slate-200 rounded-lg shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent disabled:bg-slate-50 disabled:text-slate-400 transition-shadow text-sm"
        />
        <button
          type="submit"
          disabled={isLoading || !input.trim()}
          className="bg-indigo-600 text-white px-5 py-2.5 rounded-lg hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed flex items-center gap-2 transition-all shadow-sm text-sm font-medium"
        >
          <Send size={16} />
          <span className="hidden sm:inline">Send</span>
        </button>
      </div>
      {!isConnected && (
        <p className="text-xs text-amber-500 mt-2">
          ⚠️ Using fallback connection mode
        </p>
      )}
    </form>
  )
}
