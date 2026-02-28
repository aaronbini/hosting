import { useState, useCallback, useRef } from 'react'
import { EventData, Message, OutputOption } from '../types'
import { API_BASE } from '../api'

type WebSocketMessage =
  | { type: 'stream_start'; data: { completion_score: number; is_complete: boolean; event_data: EventData } }
  | { type: 'stream_chunk'; data: { text: string } }
  | { type: 'stream_end' }
  | { type: 'error'; data: { error: string } }
  | { type: 'event_data_update'; data: { completion_score: number; is_complete: boolean; event_data: EventData } }
  | { type: 'agent_progress'; stage: string; message: string }
  | { type: 'agent_review'; stage: string; message: string; shopping_list?: unknown }
  | { type: 'agent_complete'; stage: string; formatted_output?: string; formatted_recipes_output?: string | null; google_sheet_url?: string | null; google_tasks?: { url: string; list_title: string } | null }
  | { type: 'agent_error'; stage: string; message: string }
  | { type: 'output_selection'; options: OutputOption[] }

interface UseChatReturn {
  messages: Message[]
  isLoading: boolean
  error: string | null
  eventData: EventData | null
  completionScore: number
  isComplete: boolean
  isAwaitingReview: boolean
  excludedItems: Set<string>
  toggleExcludedItem: (name: string) => void
  connect: () => WebSocket | undefined
  sendMessage: (message: string) => void
  sendMessageRest: (message: string) => Promise<void>
  approveShoppingList: () => void
  selectOutputs: (formats: string[]) => void
  isConnected: boolean
}

interface UseChatOptions {
  initialMessages?: Message[]
  initialEventData?: EventData | null
  initialCompletionScore?: number
  initialIsComplete?: boolean
}

export const useChat = (sessionId: string, options: UseChatOptions = {}): UseChatReturn => {
  const {
    initialMessages = [],
    initialEventData = null,
    initialCompletionScore = 0,
    initialIsComplete = false,
  } = options
  const [messages, setMessages] = useState<Message[]>(initialMessages)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [eventData, setEventData] = useState<EventData | null>(initialEventData)
  const [completionScore, setCompletionScore] = useState(initialCompletionScore)
  const [isComplete, setIsComplete] = useState(initialIsComplete)
  const [ws, setWs] = useState<WebSocket | null>(null)
  const [isAwaitingReview, setIsAwaitingReview] = useState(false)
  const [excludedItems, setExcludedItems] = useState<Set<string>>(new Set())
  const isStreamingRef = useRef(false)
  const pendingEventDataRef = useRef<EventData | null>(null)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const apiHost = import.meta.env.VITE_API_BASE_URL
      ? new URL(import.meta.env.VITE_API_BASE_URL).host
      : window.location.host
    const wsUrl = `${protocol}//${apiHost}/ws/chat/${sessionId}`

    try {
      const socket = new WebSocket(wsUrl)

      socket.onopen = () => {
        console.log('WebSocket connected')
        setError(null)
      }

      socket.onmessage = (event: MessageEvent) => {
        const data = JSON.parse(event.data) as WebSocketMessage

        if (data.type === 'stream_start') {
          // Store event data but don't apply it yet - wait for first chunk
          // This prevents upload panel from appearing before the message starts
          pendingEventDataRef.current = data.data.event_data
          setCompletionScore(data.data.completion_score)
          setIsComplete(data.data.is_complete)
          isStreamingRef.current = true
        } else if (data.type === 'stream_chunk') {
          // Apply pending event data on first chunk
          if (pendingEventDataRef.current) {
            setEventData(pendingEventDataRef.current)
            pendingEventDataRef.current = null
          }

          setMessages(prev => {
            const updated = [...prev]
            const lastMsg = updated[updated.length - 1]

            // If we're streaming and last message is from assistant, append to it
            // Otherwise, create a new assistant message (first chunk)
            if (isStreamingRef.current && lastMsg?.role === 'assistant') {
              updated[updated.length - 1] = {
                ...lastMsg,
                content: lastMsg.content + data.data.text
              }
            } else {
              // First chunk - create new message
              updated.push({ role: 'assistant', content: data.data.text, timestamp: new Date() })
            }
            return updated
          })
        } else if (data.type === 'stream_end') {
          // Apply pending event data if we never got chunks (edge case)
          if (pendingEventDataRef.current) {
            setEventData(pendingEventDataRef.current)
            pendingEventDataRef.current = null
          }
          isStreamingRef.current = false
          setIsLoading(false)
        } else if (data.type === 'event_data_update') {
          setEventData(data.data.event_data)
          setCompletionScore(data.data.completion_score)
          setIsComplete(data.data.is_complete)
        } else if (data.type === 'error') {
          setError(data.data.error)
          setIsLoading(false)
        } else if (data.type === 'agent_progress') {
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: data.message || 'Working... ',
            timestamp: new Date()
          }])
        } else if (data.type === 'agent_review') {
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: data.message,
            shoppingList: data.shopping_list ?? null,
            timestamp: new Date()
          }])
          setExcludedItems(new Set())
          setIsAwaitingReview(true)
          setIsLoading(false)
        } else if (data.type === 'agent_complete') {
          const extraLinks: string[] = []
          if (data.google_sheet_url) extraLinks.push(`Google Sheet: ${data.google_sheet_url}`)
          if (data.google_tasks) extraLinks.push(`[Open Google Tasks](${data.google_tasks.url}) — look for the list named **${data.google_tasks.list_title}**`)
          const conclusion = "I hope you enjoy your gathering! Let me know if there are any other events you need assistance with."
          const content = [data.formatted_output || 'Your results are ready.', data.formatted_recipes_output, ...extraLinks, conclusion]
            .filter(Boolean)
            .join('\n\n')
          setMessages(prev => [...prev, {
            role: 'assistant',
            content,
            timestamp: new Date()
          }])
          setIsAwaitingReview(false)
          setIsLoading(false)
        } else if (data.type === 'agent_error') {
          setError(data.message || 'Agent error occurred')
          setIsAwaitingReview(false)
          setIsLoading(false)
        } else if (data.type === 'output_selection') {
          setMessages(prev => [...prev, {
            role: 'assistant',
            content: '',
            outputOptions: data.options,
            timestamp: new Date()
          }])
          setIsLoading(false)
        }
      }

      socket.onerror = () => {
        console.error('WebSocket error')
        setError('Connection error occurred')
      }

      socket.onclose = () => {
        console.log('WebSocket disconnected')
      }

      setWs(socket)
      return socket
    } catch (err) {
      setError('Failed to connect')
      console.error('WebSocket connection error:', err)
    }
  }, [sessionId])

  const sendMessage = useCallback((message: string) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setError('Not connected')
      return
    }

    setIsLoading(true)
    setError(null)

    setMessages(prev => [...prev, {
      role: 'user',
      content: message,
      timestamp: new Date()
    }])

    ws.send(JSON.stringify({
      type: 'message',
      data: message
    }))
  }, [ws])

  const sendMessageRest = useCallback(async (message: string) => {
    setIsLoading(true)
    setError(null)

    try {
      setMessages(prev => [...prev, {
        role: 'user',
        content: message,
        timestamp: new Date()
      }])

      const response = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          message
        })
      })

      if (!response.ok) throw new Error('Failed to send message')

      const data = await response.json()
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.message,
        timestamp: new Date()
      }])
      setCompletionScore(data.completion_score)
      setIsComplete(data.is_complete)
      setEventData(data.event_data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setIsLoading(false)
    }
  }, [sessionId])

  const toggleExcludedItem = useCallback((name: string) => {
    setExcludedItems(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }, [])

  const approveShoppingList = useCallback(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'approve', excluded_items: [...excludedItems] }))
    setIsAwaitingReview(false)
    setExcludedItems(new Set())
  }, [ws, excludedItems])

  const selectOutputs = useCallback((formats: string[]) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    setIsLoading(true)
    ws.send(JSON.stringify({ type: 'select_outputs', data: formats }))
  }, [ws])

  return {
    messages,
    isLoading,
    error,
    eventData,
    completionScore,
    isComplete,
    isAwaitingReview,
    excludedItems,
    toggleExcludedItem,
    connect,
    sendMessage,
    sendMessageRest,
    approveShoppingList,
    selectOutputs,
    isConnected: ws?.readyState === WebSocket.OPEN
  }
}
