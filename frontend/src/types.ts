export interface DietaryRestriction {
  type: string
  count: number
  notes?: string
}

export interface EventData {
  event_type?: string
  event_date?: string
  adult_count?: number
  child_count?: number
  total_guests?: number
  meal_type?: string
  event_duration_hours?: number
  dietary_restrictions: DietaryRestriction[]
  cuisine_preferences: string[]
  beverages_preferences?: string
  foods_to_avoid: string[]
  available_equipment: string[]
  formality_level?: string
  meal_plan: string[]
  recipe_promises: string[]
  pending_upload_dish?: string
  budget?: number
  budget_per_person?: number
  output_formats: string[]
  conversation_stage: string
  answered_questions: Record<string, boolean>
  is_complete: boolean
  completion_score: number
  progress: Record<string, unknown>
}

export interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}
