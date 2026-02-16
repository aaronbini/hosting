import os
import json
from typing import Generator, Optional
from pydantic import BaseModel
from google import genai
from google.genai import types
from app.models.event import EventPlanningData, ExtractionResult
from app.models.shopping import (
    DishCategory,
    DishServingSpec,
    DishIngredients,
    AggregatedIngredient,
    ShoppingList,
)


# ---------------------------------------------------------------------------
# Internal response schemas for recipe-related Gemini calls
# ---------------------------------------------------------------------------

class _DishCategoryItem(BaseModel):
    dish_name: str
    category: DishCategory


class _DishCategoryMapping(BaseModel):
    items: list[_DishCategoryItem]


class _AggregatedItems(BaseModel):
    """Gemini returns just the items list; we build ShoppingList around it."""
    items: list[AggregatedIngredient]


class GeminiService:
    """Service for interacting with Google Gemini API"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Gemini service

        TODO: Support BYOK (Bring Your Own Key) pattern
        Allow users to pass their own API keys instead of using app key
        """
        key = api_key or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise ValueError("GOOGLE_API_KEY environment variable not set")

        self.client = genai.Client(api_key=key)
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

        self.system_prompt = """You are a conversational event planning assistant. Your ONLY job in the information gathering phase is to ask ONE clarifying question at a time.

                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            CRITICAL RULES (FOLLOW THESE STRICTLY):
                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                            CURRENT CONVERSATION STAGE: {conversation_stage}

                            IF conversation_stage == "gathering":
                            ✓ ASK ONE QUESTION ONLY
                            ✓ Make it specific and answerable
                            ✓ Show you're listening to their previous answer
                            ✗ DO NOT provide any suggestions
                            ✗ DO NOT provide food recommendations
                            ✗ DO NOT provide shopping lists
                            ✗ DO NOT offer menu ideas
                            ✗ DO NOT provide quantities or amounts
                            ✗ DO NOT ask about multiple topics in one message
                            ✗ DO NOT make assumptions about what they want

                            IF conversation_stage == "ready_for_suggestions":
                            ✓ NOW you can provide detailed recommendations
                            ✓ Format with clear sections, bullet points
                            ✓ Include specific quantities
                            ✓ Organize by category (main items, sides, beverages, desserts)
                            ✓ Be thorough and helpful

                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            INFORMATION TO GATHER (ASK IN THIS ORDER):
                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                            1. EVENT TYPE (dinner party, birthday, wedding, picnic, barbecue, etc.)
                            2. EVENT DATE
                            3. GUEST COUNT - total number
                            4. ADULT vs CHILD breakdown (and child ages if applicable)
                            5. MEAL TYPE - breakfast, lunch, dinner, brunch, appetizers only, etc.
                            6. CUISINE PREFERENCE - what type of food (or would they like suggestions?)
                            7. BEVERAGES - alcohol included? what types? non-alcoholic options?
                            8. DIETARY RESTRICTIONS - any vegetarians, vegans, allergies (and HOW MANY people per restriction?)
                            9. COOKING EQUIPMENT - what's available (oven, grill, stovetop, etc.) - helps with meal suggestions
                            10. BUDGET - total budget or per person (optional but helpful)
                            11. FORMALITY LEVEL (optional) - casual, semi-formal, formal (helps with menu suggestions)

                            The goal is to gather enough info to recommend SPECIFIC QUANTITIES of food items to purchase.

                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            CURRENT INFORMATION GATHERED:
                            {event_data_json}

                            RESPONSE PATTERN EXAMPLE:
                            "Great! So you're hosting a dinner party for 8 people. Now, are those all adults, or will there be any children attending?"

                            Remember: Stay in gathering mode. Be curious. Ask about ONE thing at a time."""

    def generate_response(self, user_message: str, event_data: EventPlanningData, conversation_history: list) -> str:
        """
        Generate conversational AI response using Gemini.

        TODO: Add streaming support for real-time response display
        """
        event_json = json.dumps(event_data.model_dump(exclude_none=True), indent=2)
        system_with_context = self.system_prompt.format(
            event_data_json=event_json,
            conversation_stage=event_data.conversation_stage
        )

        # Build conversation history in new SDK format
        contents = []
        for msg in conversation_history[-10:]:
            contents.append(
                types.Content(
                    role="user" if msg.role == "user" else "model",
                    parts=[types.Part(text=msg.content)]
                )
            )
        contents.append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_with_context,
            )
        )

        return response.text

    def generate_response_stream(self, user_message: str, event_data: EventPlanningData, conversation_history: list) -> Generator[str, None, None]:
        """Yields text chunks as Gemini streams the response."""
        event_json = json.dumps(event_data.model_dump(exclude_none=True), indent=2)
        system_with_context = self.system_prompt.format(
            event_data_json=event_json,
            conversation_stage=event_data.conversation_stage
        )

        # Build conversation history in new SDK format
        contents = []
        for msg in conversation_history[-10:]:
            contents.append(
                types.Content(
                    role="user" if msg.role == "user" else "model",
                    parts=[types.Part(text=msg.content)]
                )
            )
        contents.append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )

        for chunk in self.client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context)
        ):
            if chunk.text:
                yield chunk.text

    def extract_event_data(self, user_message: str, current_event_data: EventPlanningData) -> ExtractionResult:
        """
        Extract structured event planning fields from a single user message.

        Uses Gemini's JSON mode with ExtractionResult as the response schema,
        replacing the regex-based ConversationAnalyzer. Only fields explicitly
        mentioned in the message are populated — everything else stays None.
        """
        current_json = json.dumps(current_event_data.model_dump(exclude_none=True), indent=2)

        prompt = f"""Extract event planning information from the user message below.

                    Rules:
                    - Only extract fields that are explicitly mentioned. Leave everything else null.
                    - Do not re-extract fields already in "Current known data" unless the user is correcting them.
                    - For event_date, convert to ISO format YYYY-MM-DD. Today is {__import__('datetime').date.today().isoformat()}.
                    - For answered_questions, include the ID of every question the message addresses.

                    Valid answered_questions IDs: event_type, event_date, guest_count, guest_breakdown,
                    meal_type, cuisine, beverages, dietary, equipment, budget, formality

                    Current known data:
                    {current_json}

                    User message: "{user_message}"
                """

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExtractionResult,
            )
        )

        return response.parsed

    # -----------------------------------------------------------------------
    # Recipe / quantity methods (async — called from agent steps)
    # -----------------------------------------------------------------------

    async def categorise_dishes(self, meal_plan: list[str]) -> dict[str, DishCategory]:
        """
        Ask Gemini to categorise each dish in the meal plan.

        Returns a mapping of dish name → DishCategory, used by
        quantity_engine.calculate_all_serving_specs() to look up per-person
        serving multipliers.
        """
        dish_list = "\n".join(f"- {dish}" for dish in meal_plan)
        categories_list = ", ".join(c.value for c in DishCategory)

        prompt = f"""Categorise each dish below into one of these categories:
                    {categories_list}

                    Dishes:
                    {dish_list}

                    Rules:
                    - Each dish must be assigned exactly one category.
                    - Use the dish's primary role in the meal (e.g. if a dish is both a protein
                      and a starch, pick whichever dominates).
                    - Beverages always get a beverage category; appetisers get passed_appetizer.
                    """
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_DishCategoryMapping,
            ),
        )
        mapping: _DishCategoryMapping = response.parsed
        return {item.dish_name: item.category for item in mapping.items}

    async def get_dish_ingredients(self, spec: DishServingSpec) -> DishIngredients:
        """
        Given a DishServingSpec (dish name + serving counts), return a
        scaled ingredient list for that exact number of servings.

        One call per dish — these are fanned out in parallel by
        agent/steps.py:get_all_dish_ingredients().
        """
        prompt = f"""You are a professional chef. Provide a complete ingredient list for:

                    Dish: {spec.dish_name}
                    Adult servings: {spec.adult_servings}
                    Child servings: {spec.child_servings}
                    Total servings: {spec.total_servings}

                    Rules:
                    - Scale the recipe exactly to the above serving counts.
                    - Use consistent units: oz/lbs for proteins, fl oz/cups for liquids,
                      count for eggs/lemons/items, tsp/tbsp for spices and small amounts.
                    - Standardise ingredient names ("olive oil" not "EVOO", "spring onions" not "scallions").
                    - Include ALL components (e.g., pastry AND filling for sausage rolls,
                      dressing AND leaves for a Caesar salad).
                    - Assign each ingredient the most appropriate grocery_category.
                    """
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DishIngredients,
            ),
        )
        result: DishIngredients = response.parsed
        # Ensure the serving_spec is attached (Gemini won't include it)
        result.serving_spec = spec
        return result

    async def aggregate_ingredients(
        self, all_dish_ingredients: list[DishIngredients]
    ) -> ShoppingList:
        """
        Aggregate and deduplicate ingredients across all dishes.

        Uses Gemini for fuzzy name matching (handles synonyms like
        "spring onions" vs "scallions" or "tinned tomatoes" vs "canned tomatoes").
        Returns a ShoppingList with quantities summed per ingredient.

        Note: meal_plan, adult_count, child_count, total_guests are populated
        by the runner from AgentState — Gemini only returns the items list.
        """
        dishes_json = json.dumps(
            [d.model_dump() for d in all_dish_ingredients], indent=2
        )

        prompt = f"""You are a grocery list builder. Aggregate the ingredient lists
                    below into a single deduplicated shopping list.

                    Rules:
                    - Combine identical or synonymous ingredients (e.g. treat "scallions" and
                      "spring onions" as the same item; use the more common name).
                    - Sum quantities for the same ingredient, converting to consistent units where
                      needed (e.g. 4 tbsp + 2 tbsp = 6 tbsp; 8 oz + 8 oz = 1 lb).
                    - Preserve the most specific unit (prefer lbs over oz when > 16 oz).
                    - Set appears_in to the list of dish names that use each ingredient.
                    - Assign the most appropriate grocery_category to each item.

                    Ingredient lists by dish:
                    {dishes_json}
                    """
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_AggregatedItems,
            ),
        )
        result: _AggregatedItems = response.parsed
        # Construct ShoppingList — runner fills in guest counts from AgentState
        return ShoppingList(
            meal_plan=[d.dish_name for d in all_dish_ingredients],
            adult_count=0,   # overwritten by runner
            child_count=0,   # overwritten by runner
            total_guests=0,  # overwritten by runner
            items=result.items,
        )

    async def apply_shopping_list_corrections(
        self,
        shopping_list: ShoppingList,
        corrections: str,
    ) -> ShoppingList:
        """
        Apply free-text corrections from the user to the current shopping list.

        Returns a revised ShoppingList with the same structure.
        """
        list_json = json.dumps(shopping_list.model_dump(), indent=2)

        prompt = f"""You are a grocery list editor. Update the shopping list below
                    based on the user's corrections.

                    Current shopping list:
                    {list_json}

                    User corrections:
                    {corrections}

                    Rules:
                    - Apply only the changes the user explicitly requested.
                    - Return the full updated shopping list (all items, not just changed ones).
                    - Maintain the same structure as the input.
                    """
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_AggregatedItems,
            ),
        )
        result: _AggregatedItems = response.parsed
        return ShoppingList(
            meal_plan=shopping_list.meal_plan,
            adult_count=shopping_list.adult_count,
            child_count=shopping_list.child_count,
            total_guests=shopping_list.total_guests,
            items=result.items,
        )
