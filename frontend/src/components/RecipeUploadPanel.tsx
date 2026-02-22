import { useState, useRef, useEffect } from 'react'
import { Recipe } from '../types'

interface UploadResult {
  success: boolean
  message: string
  ingredientCount?: number
}

interface Props {
  sessionId: string
  recipes: Recipe[]  // recipes awaiting user-provided ingredients
  onUploadComplete?: (dishName: string) => void  // called after a successful upload
}

const ACCEPTED_TYPES = '.pdf,.txt,.jpg,.jpeg,.png,.webp'
const ACCEPTED_LABEL = 'PDF, TXT, JPG, PNG, WEBP'

export default function RecipeUploadPanel({ sessionId, recipes, onUploadComplete }: Props) {
  const [selectedDish, setSelectedDish] = useState(recipes[0]?.name ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Re-sync selection whenever the recipe list changes
  useEffect(() => {
    const validDish = recipes.find(r => r.name === selectedDish) ? selectedDish : recipes[0]?.name ?? ''
    setSelectedDish(validDish)
    setResult(null)
  }, [recipes, selectedDish])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!file || !selectedDish) return

    setUploading(true)
    setResult(null)

    const formData = new FormData()
    formData.append('file', file)

    try {
      const res = await fetch(
        `/api/sessions/${sessionId}/upload-recipe?dish_name=${encodeURIComponent(selectedDish)}`,
        { method: 'POST', body: formData }
      )

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setResult({ success: false, message: err.detail ?? `Upload failed (HTTP ${res.status}).` })
        return
      }

      const data = await res.json()
      const count = data.ingredients?.length ?? 0
      if (count === 0) {
        setResult({
          success: false,
          message: "Couldn't find an ingredient list in that file. Try a screenshot of the recipe or describe the ingredients in the chat.",
        })
      } else {
        setResult({
          success: true,
          message: `Got it — extracted ${count} ingredient${count !== 1 ? 's' : ''} from your recipe for ${selectedDish}.`,
          ingredientCount: count,
        })
        setFile(null)
        if (fileInputRef.current) fileInputRef.current.value = ''

        // Automatically advance the conversation — no need to type a follow-up message
        onUploadComplete?.(selectedDish)
      }
    } catch {
      setResult({ success: false, message: 'Network error — check your connection and try again.' })
    } finally {
      setUploading(false)
    }
  }

  if (recipes.length === 0) return null

  return (
    <div className="border-t border-slate-200 bg-slate-50 px-4 py-3">
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
        Upload a recipe file
      </p>
      <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-2">
        {/* Dish selector — only shows recipes awaiting user input */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-slate-500">Dish</label>
          <select
            value={selectedDish}
            onChange={e => setSelectedDish(e.target.value)}
            className="text-sm border border-slate-300 rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            {recipes.map(recipe => (
              <option key={recipe.name} value={recipe.name}>{recipe.name}</option>
            ))}
          </select>
        </div>

        {/* File input */}
        <div className="flex flex-col gap-1">
          <label className="text-xs text-slate-500">File ({ACCEPTED_LABEL})</label>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_TYPES}
            onChange={e => setFile(e.target.files?.[0] ?? null)}
            className="text-sm text-slate-600 file:mr-2 file:py-1 file:px-3 file:rounded file:border-0 file:text-xs file:font-medium file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100 cursor-pointer"
          />
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={!file || !selectedDish || uploading}
          className="px-4 py-1.5 text-sm font-medium rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {uploading ? 'Extracting…' : 'Upload'}
        </button>
      </form>

      {/* Processing message */}
      {uploading && (
        <p className="mt-2 text-xs text-slate-600 italic">
          Extracting ingredients from your file (this may take a little while, hold tight)…
        </p>
      )}

      {/* Result */}
      {result && (
        <p className={`mt-2 text-xs ${result.success ? 'text-green-700' : 'text-red-700'}`}>
          {result.message}
        </p>
      )}
    </div>
  )
}
