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

export default function EventDataPanel({ eventData, completionScore, isComplete }: Props) {
  const getCompletionColor = () => {
    if (completionScore >= 0.8) return 'bg-green-500'
    if (completionScore >= 0.5) return 'bg-yellow-500'
    return 'bg-slate-400'
  }

  return (
    <div className="w-80 bg-white border-l border-slate-200 p-6 overflow-y-auto shadow-lg">
      <h2 className="text-xl font-bold text-slate-900 mb-6">Event Details</h2>

      {/* Completion Progress */}
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
        {isComplete && (
          <p className="text-sm text-green-600 font-medium mt-2">✓ Ready to plan!</p>
        )}
      </div>

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
            value={`${eventData.total_guests} (${eventData.adult_count} adults, ${eventData.child_count} children)`}
          />
        )}

        {eventData.venue_type && (
          <DetailItem label="Venue" value={eventData.venue_type} />
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
