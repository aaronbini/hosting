import { RefObject } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ActiveCard, Message } from '../types'
import ShoppingListReview from './ShoppingListReview'
import OutputOptionsCard from './OutputOptionsCard'
import MenuConfirmPanel from './MenuConfirmPanel'

interface Props {
  messages: Message[]
  isLoading: boolean
  messagesEndRef: RefObject<HTMLDivElement>
  excludedItems: Set<string>
  activeCard: ActiveCard
  onToggleExcluded: (name: string) => void
  onSelectOutputs: (formats: string[]) => void
  onConfirmMenu: (ownRecipeNames: string[]) => void
  onConfirmRecipes: () => void
  isAwaitingReview: boolean
  onApprove: () => void
}

export default function ChatMessages({ messages, isLoading, messagesEndRef, excludedItems, activeCard, onToggleExcluded, onSelectOutputs, onConfirmMenu, onConfirmRecipes, isAwaitingReview, onApprove }: Props) {
  const lastMsg = messages[messages.length - 1]
  const isStreaming = isLoading && lastMsg?.role === 'assistant'
  const lastShoppingListIdx = messages.reduce((last, m, i) => m.shoppingList ? i : last, -1)

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      {messages.length === 0 ? (
        <div className="flex items-center justify-center h-full text-center">
          <div>
            <div className="text-5xl mb-4">🍽️</div>
            <p className="text-slate-500 text-base font-medium">
              Welcome! Tell me about your event and I'll help you plan the perfect menu.
            </p>
          </div>
        </div>
      ) : (
        messages.map((msg, idx) => {
          const isLastAssistant = isStreaming && idx === messages.length - 1
          const isUser = msg.role === 'user'
          return (
            <div
              key={idx}
              className={`flex items-end gap-2 message-fade-in ${isUser ? 'justify-end' : 'justify-start'}`}
            >
              {!isUser && (
                <div className="w-7 h-7 rounded-full bg-indigo-100 border border-indigo-200 flex items-center justify-center text-base shrink-0 mb-1">
                  🍽️
                </div>
              )}
              <div className={`flex flex-col gap-2 ${isUser ? 'items-end' : 'items-start'} max-w-lg w-full`}>
                {msg.content && (
                  <div
                    className={`max-w-md lg:max-w-lg px-4 py-3 rounded-2xl ${
                      isUser
                        ? 'bg-indigo-600 text-white rounded-br-sm shadow-md'
                        : 'bg-white text-slate-900 border border-slate-100 rounded-bl-sm shadow-sm'
                    }`}
                  >
                    <div
                      className={`text-sm chat-markdown ${isUser ? 'chat-markdown--user' : ''} ${isLastAssistant ? 'chat-markdown--streaming' : ''}`}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                    {msg.timestamp && (
                      <p className={`text-xs mt-1.5 ${
                        isUser ? 'text-indigo-200' : 'text-slate-400'
                      }`}>
                        {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
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
                      <div className="border border-slate-200 bg-green-50 rounded-xl px-4 py-3 flex items-center gap-3 max-w-md shadow-sm">
                        <p className="text-sm text-slate-600 flex-1">
                          {excludedItems.size > 0
                            ? `Removing ${excludedItems.size} item${excludedItems.size > 1 ? 's' : ''} you already have.`
                            : 'Check off anything you already have, then approve to continue.'}
                        </p>
                        <button
                          onClick={onApprove}
                          className="px-4 py-2 text-sm font-medium rounded-lg bg-green-600 text-white hover:bg-green-700 transition-all shadow-sm shrink-0"
                        >
                          {excludedItems.size > 0 ? 'Approve with edits' : 'Approve'}
                        </button>
                      </div>
                    )}
                  </>
                )}
              </div>
              {isUser && (
                <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-xs font-semibold text-white shrink-0 mb-1">
                  You
                </div>
              )}
            </div>
          )
        })
      )}

      {isLoading && !isStreaming && (
        <div className="flex justify-start items-end gap-2">
          <div className="w-7 h-7 rounded-full bg-indigo-100 border border-indigo-200 flex items-center justify-center text-base shrink-0">
            🍽️
          </div>
          <div className="bg-white border border-slate-100 px-4 py-3 rounded-2xl rounded-bl-sm shadow-sm">
            <div className="flex space-x-1.5 items-center h-3">
              <div className="w-2 h-2 bg-indigo-300 rounded-full animate-bounce"></div>
              <div className="w-2 h-2 bg-indigo-300 rounded-full animate-bounce" style={{ animationDelay: '0.15s' }}></div>
              <div className="w-2 h-2 bg-indigo-300 rounded-full animate-bounce" style={{ animationDelay: '0.3s' }}></div>
            </div>
          </div>
        </div>
      )}

      {activeCard && (
        <div className="flex items-end gap-2 justify-start message-fade-in">
          <div className="w-7 h-7 rounded-full bg-indigo-100 border border-indigo-200 flex items-center justify-center text-base shrink-0 mb-1">
            🍽️
          </div>
          <div className="flex flex-col gap-2 items-start max-w-lg w-full">
            {activeCard.type === 'menu_confirm' && (
              <MenuConfirmPanel
                recipes={activeCard.recipes}
                onConfirm={onConfirmMenu}
                isLoading={isLoading}
              />
            )}
            {activeCard.type === 'recipe_confirm' && (
              <div className="border border-amber-100 bg-amber-50 rounded-xl px-4 py-3 flex items-center gap-3 max-w-md shadow-sm">
                <p className="text-sm text-slate-600 flex-1">
                  If the ingredient lists look right, confirm to continue. You can also swap in your own recipe by replying in the chat.
                </p>
                <button
                  onClick={onConfirmRecipes}
                  disabled={isLoading}
                  className="px-4 py-2 text-sm font-medium rounded-lg bg-amber-500 text-white hover:bg-amber-600 transition-all shadow-sm disabled:opacity-50 shrink-0"
                >
                  Looks Good
                </button>
              </div>
            )}
            {activeCard.type === 'output_selection' && (
              <OutputOptionsCard
                options={activeCard.options}
                onConfirm={onSelectOutputs}
              />
            )}
          </div>
        </div>
      )}

      <div ref={messagesEndRef} />
    </div>
  )
}
