export interface DietaryRestriction {
  type: string
  count: number
  notes?: string
}

export type RecipeStatus = 'placeholder' | 'named' | 'complete'
export type RecipeSourceType = 'ai_default' | 'user_url' | 'user_upload' | 'user_description'
export type RecipeType = 'food' | 'drink'
export type PreparationMethod = 'store_bought' | 'homemade'

export interface Recipe {
  name: string
  status: RecipeStatus
  ingredients: any[]
  source_type: RecipeSourceType
  recipe_type: RecipeType
  preparation_method: PreparationMethod
  url?: string
  description?: string
  servings: number
  awaiting_user_input: boolean
}

export interface MealPlan {
  recipes: Recipe[]
  confirmed: boolean
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
  meal_plan: MealPlan
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
