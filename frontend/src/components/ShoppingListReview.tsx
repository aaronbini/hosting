interface Props {
  shoppingList: any
  excludedItems: Set<string>
  onToggle: (name: string) => void
}

export default function ShoppingListReview({ shoppingList, excludedItems, onToggle }: Props) {
  if (!shoppingList?.grouped || typeof shoppingList.grouped !== 'object') return null

  const entries = Object.entries(shoppingList.grouped) as [string, any[]][]

  return (
    <div className="border-t border-slate-200 bg-white px-4 py-3 max-h-72 overflow-y-auto">
      {entries.map(([category, items]) => {
        if (!Array.isArray(items) || items.length === 0) return null
        const label = category.replace(/_/g, ' ')
        const categoryLabel = label.charAt(0).toUpperCase() + label.slice(1)

        return (
          <div key={category} className="mb-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1">
              {categoryLabel}
            </p>
            <ul className="space-y-1">
              {items.map((item: any) => {
                const name: string = item?.name ?? 'Item'
                const qty = item?.total_quantity ?? item?.quantity
                const unit = item?.unit?.value ?? item?.unit
                const qtyStr = typeof qty === 'number' ? `${Math.ceil(qty)}` : (qty != null ? String(qty) : '')
                const unitStr = unit ? ` ${unit}` : ''
                const detail = qtyStr ? `: ${qtyStr}${unitStr}` : ''
                const checked = excludedItems.has(name)

                return (
                  <li key={name}>
                    <label className="flex items-center gap-2 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => onToggle(name)}
                        className="w-4 h-4 rounded border-slate-300 text-green-600 cursor-pointer"
                      />
                      <span className={`text-sm ${checked ? 'line-through text-slate-400' : 'text-slate-700'}`}>
                        {name}{detail}
                      </span>
                      {checked && (
                        <span className="text-xs text-slate-400 italic">already have</span>
                      )}
                    </label>
                  </li>
                )
              })}
            </ul>
          </div>
        )
      })}
    </div>
  )
}
