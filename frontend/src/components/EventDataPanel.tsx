import { EventData, DietaryRestriction } from '../types'

interface Props {
  eventData: EventData
  completionScore: number
  isComplete: boolean
}

interface DetailItemProps {
  label: string
  value: string
}

export default function EventDataPanel({ eventData, completionScore }: Props) {
  const getCompletionColor = () => {
    if (completionScore >= 0.8) return 'bg-green-500'
    if (completionScore >= 0.5) return 'bg-yellow-500'
    return 'bg-slate-400'
  }

  const stageLabels: Record<string, string> = {
    gathering: 'Gathering Info',
    recipe_confirmation: 'Confirming Recipes',
    selecting_output: 'Choosing Output',
    agent_running: 'Calculating...',
    complete: 'Planning Complete',
  }

  return (
    <div className="w-80 bg-white border-l border-slate-200 p-6 overflow-y-auto shadow-lg">
      <h2 className="text-xl font-bold text-slate-900 mb-6">Event Details</h2>

      {/* Conversation Stage */}
      <div className="mb-4">
        <span className={`text-xs font-semibold px-2 py-1 rounded ${
          eventData.conversation_stage === 'complete'
            ? 'text-green-700 bg-green-100'
            : 'text-indigo-600 bg-indigo-50'
        }`}>
          {stageLabels[eventData.conversation_stage] || eventData.conversation_stage}
        </span>
      </div>

      {/* Completion Progress */}
      {eventData.conversation_stage === 'complete' ? (
        <div className="mb-6 p-3 bg-green-50 border border-green-200 rounded-lg">
          <p className="text-sm font-semibold text-green-700">Event planning complete!</p>
          <p className="text-xs text-green-600 mt-1">Your shopping list has been delivered.</p>
        </div>
      ) : (
        <div className="mb-6">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm font-medium text-slate-700">Information Complete</span>
            <span className="text-sm font-bold text-slate-900">
              {Math.round(completionScore * 100)}%
            </span>
          </div>
          <div className="w-full bg-slate-200 rounded-full h-2">
            <div
              className={`h-2 rounded-full transition-all ${getCompletionColor()}`}
              style={{ width: `${completionScore * 100}%` }}
            ></div>
          </div>
          {completionScore >= 1.0 && (
            <p className="text-sm text-green-600 font-medium mt-2">Ready to plan!</p>
          )}
        </div>
      )}

      {/* Event Details */}
      <div className="space-y-4">
        {eventData.event_type && (
          <DetailItem label="Event Type" value={eventData.event_type} />
        )}

        {eventData.event_date && (
          <DetailItem label="Date" value={eventData.event_date} />
        )}

        {eventData.total_guests && (
          <DetailItem
            label="Total Guests"
            value={`${eventData.total_guests} (${eventData.adult_count ?? 0} adults, ${eventData.child_count ?? 0} children)`}
          />
        )}

        {eventData.formality_level && (
          <DetailItem label="Formality" value={eventData.formality_level} />
        )}

        {eventData.meal_type && (
          <DetailItem label="Meal Type" value={eventData.meal_type} />
        )}

        {eventData.event_duration_hours && (
          <DetailItem label="Duration" value={`${eventData.event_duration_hours} hours`} />
        )}

        {eventData.budget && (
          <DetailItem label="Budget" value={`$${eventData.budget.toFixed(2)}`} />
        )}

        {eventData.budget_per_person && (
          <DetailItem
            label="Per Person"
            value={`$${eventData.budget_per_person.toFixed(2)}`}
          />
        )}

        {eventData.dietary_restrictions && eventData.dietary_restrictions.length > 0 && (
          <div className="pt-4 border-t border-slate-200">
            <p className="text-sm font-semibold text-slate-700 mb-2">Dietary Restrictions</p>
            <ul className="space-y-1">
              {eventData.dietary_restrictions.map((restriction: DietaryRestriction, idx: number) => (
                <li key={idx} className="text-sm text-slate-600">
                  {restriction.type}: {restriction.count} person{restriction.count !== 1 ? 's' : ''}
                </li>
              ))}
            </ul>
          </div>
        )}

        {eventData.cuisine_preferences && eventData.cuisine_preferences.length > 0 && (
          <div className="pt-4 border-t border-slate-200">
            <p className="text-sm font-semibold text-slate-700 mb-2">Cuisine Preferences</p>
            <div className="flex flex-wrap gap-2">
              {eventData.cuisine_preferences.map((cuisine: string, idx: number) => (
                <span key={idx} className="text-xs bg-indigo-100 text-indigo-700 px-2 py-1 rounded">
                  {cuisine}
                </span>
              ))}
            </div>
          </div>
        )}

        {eventData.meal_plan?.recipes && eventData.meal_plan.recipes.length > 0 && (
          <div className="pt-4 border-t border-slate-200">
            <p className="text-sm font-semibold text-slate-700 mb-2">Menu</p>
            <ul className="space-y-2">
              {eventData.meal_plan.recipes.map((recipe, idx: number) => (
                <li key={idx} className="text-sm text-slate-600">
                  <div className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full flex-shrink-0"></span>
                    <span className="font-medium">{recipe.name}</span>
                  </div>
                  {recipe.status === 'placeholder' && (
                    <span className="text-xs text-amber-600 ml-4">needs name</span>
                  )}
                  {recipe.status === 'named' && recipe.awaiting_user_input && (
                    <span className="text-xs text-amber-600 ml-4">needs recipe</span>
                  )}
                  {recipe.status === 'named' && !recipe.awaiting_user_input && recipe.preparation_method !== 'store_bought' && (
                    <span className="text-xs text-amber-600 ml-4">needs ingredients</span>
                  )}
                  {(
                    (recipe.preparation_method === 'store_bought' && recipe.status !== 'placeholder') ||
                    (recipe.status === 'complete' && eventData.conversation_stage !== 'gathering')
                  ) && (
                    <span className="text-xs text-green-600 ml-4">✓ ready</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}

function DetailItem({ label, value }: DetailItemProps) {
  return (
    <div className="pb-3 border-b border-slate-100">
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">
        {label}
      </p>
      <p className="text-sm font-medium text-slate-900">{value}</p>
    </div>
  )
}
