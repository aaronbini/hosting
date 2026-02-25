import { useState } from 'react'
import { OutputOption } from '../types'

interface Props {
  options: OutputOption[]
  onConfirm: (formats: string[]) => void
}

export default function OutputOptionsCard({ options, onConfirm }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const toggle = (value: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(value) ? next.delete(value) : next.add(value)
      return next
    })
  }

  return (
    <div className="border border-slate-200 bg-white rounded-lg px-4 py-3 max-w-md w-full">
      <p className="text-sm font-medium text-slate-700 mb-3">
        How would you like your shopping list delivered? You can select multiple.
      </p>
      <ul className="space-y-2 mb-4">
        {options.map(opt => (
          <li key={opt.value}>
            <label className="flex items-start gap-2 cursor-pointer group">
              <input
                type="checkbox"
                checked={selected.has(opt.value)}
                onChange={() => toggle(opt.value)}
                className="mt-0.5 w-4 h-4 rounded border-slate-300 text-indigo-600 cursor-pointer"
              />
              <span className="text-sm text-slate-700">
                <span className="font-medium">{opt.label}</span>
                {opt.description && (
                  <span className="text-slate-400"> — {opt.description}</span>
                )}
              </span>
            </label>
          </li>
        ))}
      </ul>
      <button
        disabled={selected.size === 0}
        onClick={() => onConfirm([...selected])}
        className="px-4 py-2 text-sm font-medium rounded bg-indigo-600 text-white hover:bg-indigo-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Confirm
      </button>
    </div>
  )
}
