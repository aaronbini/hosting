import { RefObject } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Message } from '../types'
import ShoppingListReview from './ShoppingListReview'
import OutputOptionsCard from './OutputOptionsCard'
import MenuConfirmPanel from './MenuConfirmPanel'

interface Props {
  messages: Message[]
  isLoading: boolean
  messagesEndRef: RefObject<HTMLDivElement>
  excludedItems: Set<string>
  onToggleExcluded: (name: string) => void
  onSelectOutputs: (formats: string[]) => void
  onConfirmMenu: (ownRecipeNames: string[]) => void
  onConfirmRecipes: () => void
  isAwaitingReview: boolean
  onApprove: () => void
}

export default function ChatMessages({ messages, isLoading, messagesEndRef, excludedItems, onToggleExcluded, onSelectOutputs, onConfirmMenu, onConfirmRecipes, isAwaitingReview, onApprove }: Props) {
  const lastMsg = messages[messages.length - 1]
  const isStreaming = isLoading && lastMsg?.role === 'assistant'
  const lastShoppingListIdx = messages.reduce((last, m, i) => m.shoppingList ? i : last, -1)

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
              <div className={`flex flex-col gap-2 ${msg.role === 'user' ? 'items-end' : 'items-start'} max-w-lg w-full`}>
                {msg.content && (
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
                )}
                {msg.shoppingList && (
                  <>
                    <ShoppingListReview
                      shoppingList={msg.shoppingList}
                      excludedItems={excludedItems}
                      onToggle={onToggleExcluded}
                    />
                    {isAwaitingReview && idx === lastShoppingListIdx && (
                      <div className="border border-slate-200 bg-green-50 rounded-lg px-4 py-3 flex items-center gap-3 max-w-md">
                        <p className="text-sm text-slate-600 flex-1">
                          {excludedItems.size > 0
                            ? `Removing ${excludedItems.size} item${excludedItems.size > 1 ? 's' : ''} you already have.`
                            : 'Check off anything you already have, then approve to continue.'}
                        </p>
                        <button
                          onClick={onApprove}
                          className="px-5 py-2 text-sm font-medium rounded bg-green-600 text-white hover:bg-green-700 transition-colors shrink-0"
                        >
                          {excludedItems.size > 0 ? 'Approve with edits' : 'Approve'}
                        </button>
                      </div>
                    )}
                  </>
                )}
                {msg.outputOptions && (
                  <OutputOptionsCard
                    options={msg.outputOptions}
                    onConfirm={onSelectOutputs}
                  />
                )}
                {msg.menuConfirmRecipes && (
                  <MenuConfirmPanel
                    recipes={msg.menuConfirmRecipes}
                    onConfirm={onConfirmMenu}
                    isLoading={isLoading}
                  />
                )}
                {msg.recipeConfirmPrompt && (
                  <div className="border border-slate-200 bg-amber-50 rounded-lg px-4 py-3 flex items-center gap-3 max-w-md">
                    <p className="text-sm text-slate-600 flex-1">
                      If the ingredient lists look right, confirm to continue. You can also swap in your own recipe by replying in the chat.
                    </p>
                    <button
                      onClick={onConfirmRecipes}
                      disabled={isLoading}
                      className="px-4 py-2 text-sm font-medium rounded bg-amber-600 text-white hover:bg-amber-700 transition-colors disabled:opacity-50 shrink-0"
                    >
                      Looks Good
                    </button>
                  </div>
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
