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
    <form onSubmit={handleSubmit} className="bg-white border-t border-slate-200 p-6">
      <div className="flex gap-3">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Tell me about your event..."
          disabled={isLoading}
          className="flex-1 px-4 py-3 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-600 disabled:bg-slate-100"
        />
        <button
          type="submit"
          disabled={isLoading || !input.trim()}
          className="bg-indigo-600 text-white px-6 py-3 rounded-lg hover:bg-indigo-700 disabled:bg-slate-400 flex items-center gap-2 transition-colors"
        >
          <Send size={18} />
          <span className="hidden sm:inline">Send</span>
        </button>
      </div>
      {!isConnected && (
        <p className="text-xs text-amber-600 mt-2">
          ⚠️ Using fallback connection mode
        </p>
      )}
    </form>
  )
}
