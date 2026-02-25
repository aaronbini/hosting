import { useState, useEffect } from 'react'
import type { SavedPlan, SavedPlanSummary } from '../types'
import ReactMarkdown from 'react-markdown'

interface Props {
  onStartPlanning: () => void
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function PlanCard({
  summary,
  onDelete,
}: {
  summary: SavedPlanSummary
  onDelete: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<SavedPlan | null>(null)
  const [loading, setLoading] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const dishes = summary.event_data.meal_plan?.recipes?.map(r => r.name) ?? []

  async function handleExpand() {
    if (expanded) {
      setExpanded(false)
      return
    }
    setExpanded(true)
    if (detail) return
    setLoading(true)
    try {
      const r = await fetch(`/api/plans/${summary.id}`, { credentials: 'include' })
      if (r.ok) setDetail(await r.json())
    } finally {
      setLoading(false)
    }
  }

  async function handleDelete() {
    await fetch(`/api/plans/${summary.id}`, { method: 'DELETE', credentials: 'include' })
    onDelete(summary.id)
  }

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      {/* Card header */}
      <div className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h3 className="text-lg font-semibold text-slate-900 truncate">{summary.name}</h3>
            <p className="text-sm text-slate-500 mt-0.5">{formatDate(summary.created_at)}</p>
            <div className="flex flex-wrap gap-2 mt-2">
              {summary.event_data.total_guests != null && (
                <span className="px-2 py-0.5 text-xs rounded-full bg-indigo-50 text-indigo-700">
                  {summary.event_data.total_guests} guests
                </span>
              )}
              {summary.event_data.meal_type && (
                <span className="px-2 py-0.5 text-xs rounded-full bg-slate-100 text-slate-600">
                  {summary.event_data.meal_type}
                </span>
              )}
              {summary.event_data.event_date && (
                <span className="px-2 py-0.5 text-xs rounded-full bg-slate-100 text-slate-600">
                  {summary.event_data.event_date}
                </span>
              )}
            </div>
            {dishes.length > 0 && (
              <p className="text-sm text-slate-500 mt-2 line-clamp-2">
                {dishes.join(', ')}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {confirmDelete ? (
              <>
                <button
                  onClick={() => setConfirmDelete(false)}
                  className="px-3 py-1.5 text-xs rounded border border-slate-300 text-slate-600 hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleDelete}
                  className="px-3 py-1.5 text-xs rounded bg-red-600 text-white hover:bg-red-700"
                >
                  Confirm
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => setConfirmDelete(true)}
                  className="px-3 py-1.5 text-xs rounded border border-slate-200 text-slate-500 hover:bg-red-50 hover:text-red-600 hover:border-red-200"
                >
                  Delete
                </button>
                <button
                  onClick={handleExpand}
                  className="px-3 py-1.5 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-700"
                >
                  {expanded ? 'Hide' : 'View details'}
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-slate-100 bg-slate-50 p-5">
          {loading && (
            <div className="flex items-center gap-2 text-slate-500">
              <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-600" />
              <span className="text-sm">Loading...</span>
            </div>
          )}
          {detail && (
            <div className="space-y-6">
              {detail.formatted_output && (
                <div>
                  <h4 className="text-sm font-semibold text-slate-700 mb-2">Shopping List</h4>
                  <div className="prose prose-sm max-w-none text-slate-700">
                    <ReactMarkdown>{detail.formatted_output}</ReactMarkdown>
                  </div>
                </div>
              )}
              {detail.formatted_recipes_output && (
                <div>
                  <h4 className="text-sm font-semibold text-slate-700 mb-2">Recipes</h4>
                  <div className="prose prose-sm max-w-none text-slate-700">
                    <ReactMarkdown>{detail.formatted_recipes_output}</ReactMarkdown>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function PlansView({ onStartPlanning }: Props) {
  const [plans, setPlans] = useState<SavedPlanSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetch('/api/plans', { credentials: 'include' })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(data => setPlans(data.plans))
      .catch(() => setError(true))
      .finally(() => setLoading(false))
  }, [])

  function handleDelete(id: string) {
    setPlans(prev => prev.filter(p => p.id !== id))
  }

  return (
    <div className="flex-1 overflow-y-auto bg-gradient-to-br from-blue-50 to-indigo-100 p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-bold text-slate-900">My Plans</h2>
          <button
            onClick={onStartPlanning}
            className="px-4 py-2 text-sm font-medium rounded bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
          >
            Plan a new event
          </button>
        </div>

        {loading && (
          <div className="flex justify-center py-16">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-600" />
          </div>
        )}

        {error && (
          <p className="text-red-600 text-center py-16">Failed to load plans. Refresh to try again.</p>
        )}

        {!loading && !error && plans.length === 0 && (
          <div className="text-center py-16">
            <p className="text-slate-500 mb-4">No saved plans yet.</p>
            <p className="text-slate-400 text-sm mb-6">
              Complete an event planning session and it will appear here automatically.
            </p>
            <button
              onClick={onStartPlanning}
              className="px-5 py-2.5 text-sm font-medium rounded bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
            >
              Start planning
            </button>
          </div>
        )}

        {!loading && plans.length > 0 && (
          <div className="space-y-4">
            {plans.map(plan => (
              <PlanCard key={plan.id} summary={plan} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
