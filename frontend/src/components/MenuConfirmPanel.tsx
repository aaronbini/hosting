import { useState } from 'react'
import type { MenuConfirmItem } from '../types'

interface Props {
  recipes: MenuConfirmItem[]
  onConfirm: (ownRecipeNames: string[]) => void
  isLoading: boolean
}

export default function MenuConfirmPanel({ recipes, onConfirm, isLoading }: Props) {
  const homemade = recipes.filter(r => !r.store_bought)
  const storeBought = recipes.filter(r => r.store_bought)
  const [checked, setChecked] = useState<Set<string>>(new Set())

  const toggle = (name: string) =>
    setChecked(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })

  const nOwn = checked.size

  return (
    <div className="border border-slate-200 bg-blue-50 rounded-lg px-4 py-3">
      <p className="text-sm font-medium text-slate-700 mb-1">
        Ready to confirm this menu?
      </p>
      <p className="text-xs text-slate-400 mb-3">
        Check any dishes where you'd like to provide your own recipe — the rest will have ingredient lists generated automatically.
        Or type in the chat to adjust the menu first.
      </p>
      <div className="flex flex-col gap-1.5 mb-3">
        {homemade.map(({ name }) => (
          <label key={name} className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
            <input
              type="checkbox"
              checked={checked.has(name)}
              onChange={() => toggle(name)}
            />
            {name}
          </label>
        ))}
        {storeBought.map(({ name }) => (
          <label key={name} className="flex items-center gap-2 text-sm text-slate-400 cursor-not-allowed">
            <input type="checkbox" disabled />
            {name}
            <span className="text-xs bg-slate-200 text-slate-500 rounded px-1.5 py-0.5 leading-none">Store-bought</span>
          </label>
        ))}
      </div>
      <button
        onClick={() => onConfirm(Array.from(checked))}
        disabled={isLoading}
        className="px-5 py-2 text-sm font-medium rounded bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-50 shrink-0"
      >
        {nOwn > 0 ? `Confirm (${nOwn} own recipe${nOwn > 1 ? 's' : ''})` : 'Confirm — generate all'}
      </button>
    </div>
  )
}
