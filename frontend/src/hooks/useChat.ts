import { useState, useCallback } from 'react'
import { EventData, Message } from '../types'

type WebSocketMessage =
  | { type: 'stream_start'; data: { completion_score: number; is_complete: boolean; event_data: EventData } }
  | { type: 'stream_chunk'; data: { text: string } }
  | { type: 'stream_end' }
  | { type: 'error'; data: { error: string } }

interface UseChatReturn {
  messages: Message[]
  isLoading: boolean
  error: string | null
  eventData: EventData | null
  completionScore: number
  isComplete: boolean
  connect: () => WebSocket | undefined
  sendMessage: (message: string) => void
  sendMessageRest: (message: string) => Promise<void>
  isConnected: boolean
}

/**
 * Hook for managing WebSocket chat connection
 *
 * TODO: Implement WebSocket reconnection logic
 * TODO: Handle connection timeouts
 * TODO: Implement message queuing for offline scenarios
 */
export const useChat = (sessionId: string): UseChatReturn => {
  const [messages, setMessages] = useState<Message[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [eventData, setEventData] = useState<EventData | null>(null)
  const [completionScore, setCompletionScore] = useState(0)
  const [isComplete, setIsComplete] = useState(false)
  const [ws, setWs] = useState<WebSocket | null>(null)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/chat/${sessionId}`

    try {
      const socket = new WebSocket(wsUrl)

      socket.onopen = () => {
        console.log('WebSocket connected')
        setError(null)
      }

      socket.onmessage = (event: MessageEvent) => {
        const data = JSON.parse(event.data) as WebSocketMessage

        if (data.type === 'stream_start') {
          setCompletionScore(data.data.completion_score)
          setIsComplete(data.data.is_complete)
          setEventData(data.data.event_data)
          setMessages(prev => [...prev, { role: 'assistant', content: '', timestamp: new Date() }])
        } else if (data.type === 'stream_chunk') {
          setMessages(prev => {
            const updated = [...prev]
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              content: updated[updated.length - 1].content + data.data.text
            }
            return updated
          })
        } else if (data.type === 'stream_end') {
          setIsLoading(false)
        } else if (data.type === 'error') {
          setError(data.data.error)
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

      const response = await fetch('/api/chat', {
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

  return {
    messages,
    isLoading,
    error,
    eventData,
    completionScore,
    isComplete,
    connect,
    sendMessage,
    sendMessageRest,
    isConnected: ws?.readyState === WebSocket.OPEN
  }
}
