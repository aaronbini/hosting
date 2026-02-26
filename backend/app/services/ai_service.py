import datetime
import json
import logging
import os
from typing import AsyncGenerator, Optional

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.models.event import DietaryRestriction, EventPlanningData, ExtractionResult, Recipe
from app.models.shopping import (
    AggregatedIngredient,
    DishCategory,
    DishIngredients,
    DishServingSpec,
    RecipeIngredient,
    ShoppingList,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema helper for Gemini API compatibility
# ---------------------------------------------------------------------------


def _strip_additional_properties(schema: dict) -> dict:
    """
    Recursively remove 'additionalProperties' from a JSON schema dict.
    The Gemini API doesn't support this OpenAPI 3.1 field that Pydantic v2 adds.
    """
    if isinstance(schema, dict):
        schema.pop("additionalProperties", None)
        for value in schema.values():
            if isinstance(value, dict):
                _strip_additional_properties(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _strip_additional_properties(item)
    return schema


# ---------------------------------------------------------------------------
# Shared ingredient unit rules — referenced in all ingredient-related prompts
# ---------------------------------------------------------------------------

# Per-category serving size anchors used in get_dish_ingredients.
# The quantity engine produces a dimensionless "servings" multiplier; this
# table tells the AI what 1 adult serving actually means in tangible terms so
# that ingredient weights are consistent across runs.
CATEGORY_SERVING_HINTS: dict[str, str] = {
    DishCategory.MAIN_PROTEIN: "1 adult serving ≈ 6 oz of the primary protein (raw weight)",
    DishCategory.SECONDARY_PROTEIN: "1 adult serving ≈ 3-4 oz of the protein",
    DishCategory.STARCH_SIDE: "1 adult serving ≈ 2-3 oz dry pasta/rice/grains, or 5-6 oz potato",
    DishCategory.VEGETABLE_SIDE: "1 adult serving ≈ 4 oz vegetables",
    DishCategory.SALAD: (
        "1 adult serving ≈ 2-3 oz leafy greens for a green salad; "
        "for hearty vegetable salads (fennel, beet, carrot, etc.) use count units "
        "(e.g., 1 fennel bulb serves 3-4 people, 1 medium beet per 2 people)."
    ),
    DishCategory.BREAD: "1 serving ≈ 1 roll or 2 slices",
    DishCategory.DESSERT: "1 adult serving ≈ 1 standard slice or portion",
    DishCategory.PASSED_APPETIZER: "1 serving ≈ 1-2 bite-sized pieces",
    DishCategory.BEVERAGE_ALCOHOLIC: "1 serving ≈ 12 fl oz beer, 5 fl oz wine, or 1.5 fl oz spirit",
    DishCategory.BEVERAGE_NONALCOHOLIC: "1 serving ≈ 10 fl oz",
}

_MAX_CONTENT_CHARS = 30_000

INGREDIENT_UNIT_RULES = """
- Use shopping-friendly units that match how items are actually sold:
    * Don't present anything in teaspoons, tablespoons, or cups — these are not standard for grocery shopping and lead to confusion.
    * Proteins (meat, fish): oz or lbs
    * Dry goods (pasta, rice, flour, sugar, breadcrumbs, oats, lentils): oz or lbs — NEVER cups
    * Fresh produce (vegetables, fruit): oz, lbs, count, or bunch as appropriate
    * Liquids (broth, wine, cream, milk): fl oz, pints, quarts, or liters
    * Small liquid amounts (oil, sauces, condiments): fl oz
    * Spices and seasonings: oz
    * Eggs, lemons, onions, whole items: count
    * Garlic: bulbs or heads. if cloves are specified in the recipe, convert to bulbs (1 bulb ≈ 10 cloves).
    * Canned goods: cans
    * Packaged items: packages
- Do NOT use cups for any solid or dry ingredient.
- Do NOT include water — it is never a grocery item.
"""

# Quantity calibration targets used in generate_default_recipes_batch.
# These anchor AI-generated base recipes (4 servings) to realistic shopping
# amounts so that Python scaling produces correct totals at the guest count.
BASE_RECIPE_QUANTITY_GUIDE = """
Quantity calibration for 4 adult servings — use these as firm targets:
- Proteins (meat, fish, seafood): 20-24 oz TOTAL across ALL protein types in the dish.
  If the dish has multiple proteins (e.g., shrimp + clams + mussels), this is the
  COMBINED total — divide it across each type, do NOT give each type 20-24 oz.
- Dry pasta, rice, or grains: 8-12 oz total (2-3 oz per person)
- Cooking oils (olive oil, vegetable oil, butter): 1-2 fl oz TOTAL for the whole dish.
  Use the minimum needed for cooking — not recipe-blog generous amounts.
- Fresh vegetables (per type): 8-16 oz (½ to 1 lb)
- Garlic: ½ to 1 bulb
- Onions or shallots: 1-2 count
- Fresh herbs (per type): 1 bunch
- Canned goods (tomatoes, beans, etc.): 1-2 cans
- Broth or stock: 8-16 fl oz
- Dairy (cream, milk): 4-8 fl oz
These are shopping quantities, not restaurant portions. Err conservative.
"""


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


class _ExtractedRecipe(BaseModel):
    """Response schema for recipe extraction from URL/file/description."""

    dish_name: Optional[str] = None  # Actual dish name extracted from recipe (e.g., "Spaghetti Carbonara")
    ingredients: list[RecipeIngredient]


class _BatchExtractedRecipes(BaseModel):
    """Response schema for batched default recipe generation."""

    dishes: list[_ExtractedRecipe]


class _RecipeDetails(BaseModel):
    """Step-by-step instructions for one dish (ingredients provided separately)."""

    dish_name: str
    instructions: list[str]


class _RecipeDetailsBatch(BaseModel):
    """Response schema for batched recipe instruction generation."""

    recipes: list[_RecipeDetails]


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
        self.fast_model_name = os.getenv("GEMINI_FAST_MODEL", "gemini-2.5-flash-lite")

        self.system_prompt = """You are a conversational event planning assistant helping someone plan a menu, as well as how much food to buy for their event.

                            CRITICAL: Never output thinking, reasoning, or internal dialogue in your responses. Only output the final conversational text meant for the user to read. Do not use <thinking>, <thought>, or similar tags. Keep your responses focused and user-facing only.

                            CURRENT STAGE: {conversation_stage}
                            CURRENT EVENT DATA: {event_data_json}

                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            STAGE-SPECIFIC INSTRUCTIONS:
                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                            IF conversation_stage == "gathering":

                              Your goal: collect event details AND arrive at a specific list of dishes (the meal plan).

                              IMPORTANT — Recipe promises take priority:
                                Check CURRENT EVENT DATA → meal_plan → recipes for any with "awaiting_user_input": true.
                                If ANY exist, you MUST:
                                1. Focus EXCLUSIVELY on collecting those recipe sources. Do NOT suggest additional menu items.
                                2. Do NOT present a full provisional menu for other dishes until all awaiting_user_input recipes are resolved.
                                3. Do NOT move on to other topics (cuisines, sides, beverages, desserts, etc.).
                                4. For each dish awaiting user input, ask how the user wants to share the recipe:
                                   - URL: They can paste a recipe URL in chat
                                   - File upload: They can use the recipe upload panel below the chat (supports PDF, TXT, images)
                                   - Description: They can describe the ingredients in chat
                                5. Handle recipes one at a time. Once ALL recipes have awaiting_user_input: false, you can proceed normally.
                                6. When directing the user to upload a file, remind them that the upload panel
                                   lists ALL pending recipe dishes — they must confirm the correct dish name
                                   is selected in the dropdown before hitting Upload.

                                Example check:
                                  "meal_plan": {{"recipes": [
                                    {{"name": "Focaccia", "awaiting_user_input": true}},  ← MUST COLLECT THIS FIRST
                                    {{"name": "Grilled Chicken", "awaiting_user_input": false}}  ← This is fine
                                  ]}}

                              Phase 1 — Event basics (ask one question at a time):
                                1. Event/Meal type  2. Guest count (adults/children)
                                3. Dietary restrictions   4. Cuisine preference

                              Phase 2 — Menu building:
                                Once you have enough context (at minimum: event type, guest count, meal type),
                                start working toward specific dishes.

                                - ALWAYS ask first: "Do you have specific dishes in mind, or would you like me
                                  to suggest a menu?"
                                - If user provides dishes: acknowledge them, ask if they want to add more
                                  categories (appetizer, sides, dessert, beverages). Treat beverages as
                                  part of the meal plan (e.g., "Wine", "Beer", "Sparkling Water").
                                  CRITICAL for beverages: list EACH specific beverage as its own separate
                                  numbered menu item (e.g., "Vermentino wine", "Sparkling water",
                                  "Negroni Sbagliato"). NEVER group them under a theme/collection name
                                  (e.g., "Italian Coastal Selection") — such labels cannot be put on a
                                  shopping list and will confuse the system.
                                - If user wants suggestions (for a full menu or specific categories): be
                                  creative and specific — avoid the most predictable or generic dishes for
                                  this cuisine. Consider regional variations (e.g., Sicilian vs Milanese vs
                                  Roman for Italian), dishes with interesting textures or cooking methods,
                                  and less-obvious but crowd-pleasing options. Think of what an inspired
                                  home cook would serve, not a generic restaurant menu. Present suggestions
                                  as a numbered list they can modify.
                                - If user provides SOME dishes and wants help with others: suggest dishes
                                  that complement what they already chose, with the same creative spirit.
                                - If user mentions having their OWN recipe for any dish at any point,
                                  immediately ask for it (URL, file, or description) before continuing.
                                  Do NOT ignore recipe mentions or defer them to later.

                              Menu confirmation:
                                Once dishes are collected AND all recipe promises are resolved, present the
                                full menu and ask for explicit confirmation:
                                "Here's your menu: [list]. Does this look complete, or would you like to change anything?"
                                Do NOT move on until the user confirms.

                              Rules:
                                ✓ Ask ONE question at a time
                                ✓ Be conversational and warm
                                ✗ Do NOT provide quantities, shopping lists, or full provisional recipe lists yet
                                ✗ Do NOT assume the menu is final until the user confirms it
                                ✗ Do NOT ignore or defer recipe mentions — collect them immediately

                            IF conversation_stage == "recipe_confirmation":

                              The menu is locked. Now confirm the actual ingredients for each dish.

                              PRESENTING NEWLY GENERATED RECIPES:
                              If "last_generated_recipes" appears in CURRENT EVENT DATA, the system just generated
                              default ingredient lists for those dishes. You MUST present them to the user now.
                              Format each dish like this (use a bullet list for ingredients).
                              List ingredient NAMES ONLY — no quantities or amounts:

                              "Here's the ingredient list I'm planning to use for each dish:

                              **[Dish Name]**
                              • ingredient name
                              • ingredient name
                              • …

                              **[Next Dish]**
                              • …

                              Does this look right, or would you like to swap in your own recipe for any of these?
                              You can paste a URL, upload a file, or describe the ingredients.

                              Full recipes (with complete step-by-step instructions) will be provided at the end — right now we're just confirming ingredient lists.

                              ON SUBSEQUENT TURNS (no last_generated_recipes):
                              The user is reviewing or correcting dishes. Handle their feedback:
                              - If they confirm everything: acknowledge and move on.
                              - If they want to change a dish: acknowledge the change and confirm the new approach.
                              - If they want to provide their own recipe: guide them (URL, upload, description).
                              - If they ask for a full recipe for any dish: acknowledge warmly and let them
                                know that full recipes (with step-by-step instructions, scaled for their
                                guest count) will be included in the final output alongside the shopping list.
                                There's no need to generate them in chat — they'll be nicely formatted and
                                easy to reference while cooking. Then continue confirming the ingredient lists.

                              For dishes where the user has their own recipe:
                              - They can paste a URL to an online recipe (ask them to paste the URL directly in chat)
                              - They can upload a file (PDF, photo of a recipe card) using the upload panel below the chat
                              - They can describe the key ingredients conversationally
                              - Or they can let you assume a standard recipe

                              Handling recipe source scenarios:

                              SCENARIO: User says they have a recipe but doesn't specify the format
                              → Ask: "How would you like to share it? You can paste a URL, upload a file
                                (PDF or photo of the recipe), or just describe the key ingredients."

                              SCENARIO: User says they have a FILE (PDF, photo, screenshot, recipe card)
                              → Direct them immediately to the upload panel visible below the chat window.
                                Specify exactly which dish to select in the dropdown. Do NOT ask them to
                                send you a follow-up message after uploading — the upload itself signals
                                completion and you'll receive the ingredients automatically.
                                Example: "Go ahead and upload that PDF using the panel below — just make sure
                                '[dish name]' is selected in the dish dropdown."

                              SCENARIO: User mentions a WEBSITE URL they want to use
                              → Ask them to paste the URL directly in the chat so you can extract the
                                ingredients from it.

                              SCENARIO: User mentions a COOKING APP (e.g., NYTimes Cooking app, Paprika,
                              Yummly, AllRecipes app, or any mobile app)
                              → Explain that you can't access in-app content directly, and offer these
                                alternatives:
                                1. If the recipe is also on the website, find the web URL and paste it here
                                2. Take a screenshot of the recipe and upload it using the panel below
                                3. Or just describe the key ingredients and I'll work from there

                              Rules:
                                ✓ Be specific about what recipe you're assuming
                                ✓ Accept user corrections gracefully
                                ✓ Always be explicit about which dish you're referring to when asking about uploads
                                ✗ Do NOT re-open the menu discussion
                                ✗ Do NOT ask about output format or how the user wants their shopping list
                                  delivered — the system handles this automatically once all ingredients
                                  are confirmed. Never mention Google Sheet, Google Tasks, or in-chat list here.

                            IF conversation_stage == "selecting_output":

                              All recipes are confirmed. Ask how they want their shopping list delivered:

                              1. **Google Sheet** — formula-driven spreadsheet, quantities auto-adjust
                              2. **Google Tasks** — checklist format, great for shopping on your phone
                              3. **In-chat list** — formatted list right here in the conversation
                              4. **Any combination** of the above

                              Ask once, accept their choice, then confirm their selection briefly.
                              CRITICAL: Do NOT generate the shopping list yourself. The system will
                              produce it automatically once the output format is selected.

                            IF conversation_stage == "agent_running":
                              The agent is calculating. Do not generate conversational responses.

                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            RECIPE URL EXTRACTION RESULT (check every turn):
                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                            If CURRENT EVENT DATA contains "last_url_extraction_result":

                            If success=true: briefly confirm you received the recipe ingredients
                            (e.g., "Got it — I extracted the ingredients from your [dish] recipe.").

                            If success=false: your response MUST start with a clear, prominent failure
                            notice BEFORE anything else. Explain WHY it failed using the error field:
                            - If "403" or "Forbidden" or "paywall" → the site is paywalled/blocked
                            - If "404" or "not found" → the URL is broken
                            - If "No ingredient list found" → the page exists but has no recipe
                            - Otherwise → generic access error
                            Then immediately offer concrete alternatives:
                            1. Find the recipe on the website and paste the URL here
                            2. Take a screenshot and upload it using the panel below
                            3. Describe the key ingredients in the chat
                            Do NOT proceed as if the recipe was collected. The promise is still open."""

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _event_data_for_prompt(event_data: EventPlanningData) -> dict:
        """
        Serialize event_data for inclusion in a prompt, stripping fields that add
        bulk without helping the model (extracted ingredient lists, transient signals
        that are already handled by the prompt template).
        """
        d = event_data.model_dump(exclude_none=True)
        return d

    def _build_chat_context(
        self,
        user_message: str,
        event_data: EventPlanningData,
        conversation_history: list,
    ) -> tuple[str, list]:
        """Return (system_prompt_with_context, contents_list) for a chat call."""
        event_json = json.dumps(self._event_data_for_prompt(event_data), indent=2)
        system_with_context = self.system_prompt.format(
            event_data_json=event_json, conversation_stage=event_data.conversation_stage
        )

        # Add explicit pending recipe context to make them IMPOSSIBLE to miss
        pending_recipes = [r.name for r in event_data.meal_plan.pending_user_recipes]
        if pending_recipes:
            pending_context = (
                f"\n\n⚠️  URGENT: User has promised recipes for: {', '.join(pending_recipes)}\n"
                f"You MUST collect these recipes before suggesting additional menu items.\n"
                f"Do NOT propose new dishes until all awaiting_user_input recipes are resolved."
            )
            system_with_context += pending_context
        contents = [
            types.Content(
                role="user" if msg.role == "user" else "model",
                parts=[types.Part(text=msg.content)],
            )
            for msg in conversation_history
        ]
        contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))
        return system_with_context, contents

    async def _async_json_call(
        self,
        contents,
        schema,
        *,
        temperature: float | None = None,
        model: str | None = None,
    ):
        """Call Gemini async in JSON mode and return the parsed response object."""
        # If schema is a Pydantic model, convert to dict and strip additionalProperties
        schema_class = None
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            schema_class = schema
            schema = _strip_additional_properties(schema.model_json_schema())

        config_kwargs: dict = {"response_mime_type": "application/json", "response_schema": schema}
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        response = await self.client.aio.models.generate_content(
            model=model or self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        # If we stripped the schema, parse response back into the Pydantic model
        if schema_class:
            return schema_class.model_validate(response.parsed)
        return response.parsed

    # -----------------------------------------------------------------------
    # Chat response methods
    # -----------------------------------------------------------------------

    async def generate_response(
        self, user_message: str, event_data: EventPlanningData, conversation_history: list
    ) -> str:
        """Generate conversational AI response using Gemini."""
        system_with_context, contents = self._build_chat_context(
            user_message, event_data, conversation_history
        )
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context, temperature=1.2),
        )
        return response.text

    async def generate_response_stream(
        self, user_message: str, event_data: EventPlanningData, conversation_history: list
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as Gemini streams the response."""
        system_with_context, contents = self._build_chat_context(
            user_message, event_data, conversation_history
        )
        logger.info(
            "🤖 AI CALL: generate_response_stream (stage=%s, history_len=%d)",
            event_data.conversation_stage,
            len(conversation_history),
        )
        stream = await self.client.aio.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context, temperature=1.2),
        )
        chunk_count = 0
        async for chunk in stream:
            if chunk.text:
                chunk_count += 1
                yield chunk.text
        logger.info("✅ AI RESPONSE: generate_response_stream → %d chunks", chunk_count)

    async def extract_event_data(
        self,
        user_message: str,
        current_event_data: EventPlanningData,
        last_assistant_message: str | None = None,
    ) -> ExtractionResult:
        """
        Extract structured event planning fields from a single user message.

        Uses Gemini's JSON mode with ExtractionResult as the response schema.
        Stage-aware: different fields are relevant in different conversation stages.

        last_assistant_message: the previous AI turn, used so the extractor can
        infer which dishes the user is confirming when they say "yes, looks good."
        """
        current_json = json.dumps(self._event_data_for_prompt(current_event_data), indent=2)
        stage = current_event_data.conversation_stage

        assistant_context = (
            f"\n                    Previous assistant message (for context):\n"
            f'                    """{last_assistant_message}"""\n'
            if last_assistant_message
            else ""
        )

        prompt = f"""Extract event planning information from the user message below.
                    Current conversation stage: {stage}

                    General rules:
                    - Only extract fields that are explicitly mentioned or clearly confirmed. Leave everything else null.
                    - Do not re-extract fields already in "Current known data" unless the user is correcting them.
                    - For event_date, convert to ISO format YYYY-MM-DD. Today is {datetime.date.today().isoformat()}.
                    - For answered_questions, include the ID of every question the message addresses.

                    Valid answered_questions IDs: event_type, event_date, guest_count, guest_breakdown,
                    meal_type, cuisine, beverages, dietary, equipment, budget, formality, meal_plan

                    Stage-specific extraction rules:

                    IF stage == "gathering":
                    - Extract standard event fields (event_type, guest counts, cuisine, etc.) as before.
                    - cuisine_preferences must only contain broad cuisine STYLES (e.g. "Italian",
                      "Asian", "Mediterranean"). Never put dish types ("pasta"), ingredients
                      ("seafood"), or dietary terms ("vegetarian") here — those belong in
                      recipe_updates or dietary_restrictions respectively.
                    - NEVER overwrite an already-set cuisine_preferences unless the user explicitly
                      changes their cuisine choice. Dish-level requests (e.g. "I want a pasta main")
                      should produce a recipe_update, not a cuisine update.
                    - For recipe_updates: list RecipeUpdate objects for any meal plan changes:

                      ⚠️  IMPORTANT: If there are existing recipes with awaiting_user_input=true, do NOT
                      extract new recipe additions from the user's message UNLESS the user explicitly
                      requests additional dishes. Focus on collecting the promised recipes first.

                      ACTION "add": When user names a new dish or confirms a suggested dish.
                      - recipe_name: The dish name (can be placeholder like "main" or specific like "Spaghetti")
                      - status: "placeholder" if generic (main/side/dessert), "named" if specific
                      - awaiting_user_input: true if user said they have their own recipe, false otherwise
                      Example: User says "I have a recipe for a main" →
                        {{"recipe_name": "main", "action": "add", "status": "placeholder", "awaiting_user_input": true}}
                      Example: User says "Let's do focaccia" →
                        {{"recipe_name": "focaccia", "action": "add", "status": "named", "awaiting_user_input": false}}

                      BEVERAGES: When user mentions beverages, add them as recipes with recipe_type="drink":
                      - Add each beverage type as a separate recipe (e.g., "Wine", "Beer", "Sparkling Water")
                      - Set recipe_type to "drink"
                      - Set preparation_method to "store_bought" (user can override later if making cocktails/infused drinks from scratch)
                      - Set status to "named" (the agent will generate quantities later)
                      - Set awaiting_user_input to false
                      - Also populate beverages_preferences field for backward compatibility
                      Example: User says "We'll have wine and beer" →
                        {{"recipe_name": "Wine", "action": "add", "status": "named", "recipe_type": "drink", "preparation_method": "store_bought", "awaiting_user_input": false}}
                        {{"recipe_name": "Beer", "action": "add", "status": "named", "recipe_type": "drink", "preparation_method": "store_bought", "awaiting_user_input": false}}

                      CONFIRMING ASSISTANT-SUGGESTED BEVERAGES: When the previous assistant message
                      contained beverage suggestions and the user confirms them (e.g., "looks good",
                      "yes", "perfect") AND those beverages are not yet in the current meal plan:
                      - Apply the same rules above — extract each specific, purchasable beverage as
                        a separate recipe with recipe_type="drink" and preparation_method="store_bought".
                      - If the assistant grouped beverages under a theme/collection label (e.g.,
                        "Italian Coastal Selection – Vermentino or Falanghina, Sparkling Water,
                        Negroni Sbagliato"), the label itself is NOT a beverage. NEVER add it as a
                        recipe. Extract ONLY the specific drinks listed after the dash/colon.
                      - If the assistant offered "X or Y" alternatives, pick the first one listed.
                      Example: assistant suggested "Italian Coastal Selection – Vermentino or
                      Falanghina, Sparkling Water, Negroni Sbagliato", user says "looks good" →
                        {{"recipe_name": "Vermentino", "action": "add", "status": "named", "recipe_type": "drink", "preparation_method": "store_bought", "awaiting_user_input": false}}
                        {{"recipe_name": "Sparkling Water", "action": "add", "status": "named", "recipe_type": "drink", "preparation_method": "store_bought", "awaiting_user_input": false}}
                        {{"recipe_name": "Negroni Sbagliato", "action": "add", "status": "named", "recipe_type": "drink", "preparation_method": "store_bought", "awaiting_user_input": false}}

                      FOOD ITEMS: For food dishes:
                      - Set recipe_type to "food" (this is the default, so you can omit it)
                      - Set preparation_method to "homemade" (this is the default, so you can omit it)
                      - User can explicitly request store-bought items (e.g., "let's just buy pre-made guacamole")
                      Example: User says "let's get store-bought hummus" →
                        {{"recipe_name": "Hummus", "action": "add", "status": "named", "recipe_type": "food", "preparation_method": "store_bought", "awaiting_user_input": false}}

                      ACTION "remove": When user explicitly removes a dish.
                      - recipe_name: The dish to remove
                      Example: {{"recipe_name": "salad", "action": "remove"}}

                      ACTION "update": When user provides recipe details or refines a placeholder.
                      - recipe_name: Current dish name
                      - new_name: New name if renaming (e.g., "main" → "Spaghetti Carbonara")
                      - status: New status if changing
                      - awaiting_user_input: Set false when user provides recipe
                      - url: If user provides a URL for this recipe
                      - description: If user describes ingredients/changes
                      Example: User uploaded file for "main" (handled by endpoint, not extraction)
                      Example: User says "the main is Spaghetti Carbonara" →
                        {{"recipe_name": "main", "action": "update", "new_name": "Spaghetti Carbonara", "status": "named"}}
                      Example: User provides URL →
                        {{"recipe_name": "focaccia", "action": "update", "url": "https://...", "source_type": "user_url"}}

                      CRITICAL — Confirming a suggested dish for a placeholder:
                      When the user says "yes", "looks good", or otherwise confirms a menu
                      that the assistant just suggested, check the Previous assistant message
                      for specific dish names. For each PLACEHOLDER recipe in Current known
                      data (status="placeholder"), find the matching suggested dish by role
                      (e.g., "main" placeholder → the main course suggestion) and rename it
                      using action "update" — do NOT add it as a new recipe.
                      Example: "main" placeholder exists, previous assistant suggested
                      "Slow-braised Beef Short Rib Ragu" for the main, user says "yes" →
                        {{"recipe_name": "main", "action": "update", "new_name": "Slow-braised Beef Short Rib Ragu", "status": "named"}}

                      CAPTURING INGREDIENTS FROM CONFIRMED SUGGESTIONS:
                      If the Previous assistant message listed specific ingredients for a dish
                      (e.g., "I'd suggest a cheesecake — it uses cream cheese, eggs, sugar,
                      graham crackers, and butter") AND the user POSITIVELY confirms that dish
                      (e.g., "yes", "sounds great", "perfect"), capture the ingredient names
                      as a natural-language description field on the RecipeUpdate. This
                      preserves the agreed-upon recipe so it is not regenerated from scratch later.
                      Example: assistant proposed "cheesecake with cream cheese, eggs, sugar,
                      graham crackers, butter", user says "yes, that sounds great" →
                        {{"recipe_name": "cheesecake", "action": "add", "status": "named",
                          "description": "cream cheese, eggs, sugar, graham crackers, butter"}}
                      Only set description when the assistant actually listed ingredients
                      AND the user is confirming (not rejecting) that dish.
                      Do NOT set description when the user says "no", "different", "something
                      else", "let's try another", or any other rejection phrasing.
                      Do NOT invent ingredients that were not in the previous assistant message.

                      REJECTION HANDLING: Distinguish carefully between rejecting a suggestion
                      vs. removing a dish from the menu entirely.

                      REJECT AND REPLACE — user wants the slot but a different dish:
                      Signs: "I don't like that", "different suggestion", "something else for
                      [slot]", "can you try another [type]?", "no, let's go with a different
                      [type]"
                      → Keep the slot. If the recipe was already renamed from a placeholder
                        to a specific dish name, revert it back to the original placeholder:
                        {{"recipe_name": "[current specific name]", "action": "update",
                          "new_name": "[original placeholder, e.g. 'side 2']",
                          "status": "placeholder"}}
                        If the recipe is still a placeholder name (e.g. "side 2", "main"),
                        do NOT remove it and do NOT change it — just leave it alone so the
                        bot can suggest something new.
                      Do NOT capture any description from the assistant's suggestion
                      when the user is rejecting it.

                      REJECT AND REMOVE — user wants to eliminate the dish entirely:
                      Signs: "we don't need [dish]", "remove [dish]", "take that off the
                      menu", "let's skip [type] altogether"
                      → {{"recipe_name": "[current name]", "action": "remove"}}

                      MULTIPLE PLACEHOLDERS OF THE SAME TYPE: When the user requests N more
                      items of the same type (e.g., "2 more sides", "add 3 appetizers"), create
                      N separate RecipeUpdate entries with UNIQUE names. Count existing recipes
                      of that type in Current known data and number accordingly:
                      - 0 existing sides + "2 more sides" → names: "side", "side 2"
                      - 1 existing side + "2 more sides" → names: "side 2", "side 3"
                      - 2 existing sides + "3 more sides" → names: "side 3", "side 4", "side 5"
                      Never use the same recipe_name twice in one extraction — every entry
                      must have a unique name.

                      STATUS IN GATHERING STAGE: Only use status "placeholder" or "named".
                      Never set status="complete" — "complete" means the recipe has confirmed
                      ingredients, which is only set by the system after ingredient extraction.

                    - For meal_plan_confirmed: set to true ONLY if the user explicitly confirms the full
                      menu is complete ("looks good", "yes let's go with that menu", "that's everything").
                    - Include "meal_plan" in answered_questions ONLY when meal_plan_confirmed is true,
                      NOT when individual dishes are first mentioned.

                    IF stage == "recipe_confirmation":
                    - Use recipe_updates to handle recipe changes:
                      - ACTION "update" when user provides URL, description, or confirms AI recipe
                      - Set url/description/source_type as appropriate
                      - Set awaiting_user_input to false when user provides the recipe
                      Example: {{"recipe_name": "Caesar Salad", "action": "update", "awaiting_user_input": false}}
                    - Set meal_plan_confirmed to true ONLY if user explicitly says ALL recipes look
                      good and they are ready to proceed (e.g. "yes that all looks great", "let's go").
                    - NEVER set meal_plan_confirmed to true in the same turn that a recipe is being
                      added, removed, or swapped — a change request is not confirmation.
                    - Ignore event-level fields unless the user is explicitly correcting them.

                    IF stage == "selecting_output":
                    - Focus on output_formats: extract the user's chosen format(s) as a list.
                      Valid values: "google_sheet", "google_tasks", "in_chat".
                    - Ignore event-level and recipe fields.

                    Current known data:
                    {current_json}
                    {assistant_context}
                    User message: "{user_message}"
                """

        logger.info("🤖 AI CALL: extract_event_data (stage=%s, user_msg_len=%d)", stage, len(user_message))

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_strip_additional_properties(ExtractionResult.model_json_schema()),
            ),
        )

        if response.parsed is None:
            logger.warning("extract_event_data: Gemini returned None (empty/invalid JSON); using empty ExtractionResult")
            return ExtractionResult()

        result = ExtractionResult.model_validate(response.parsed)
        logger.info(
            "✅ AI RESPONSE: extract_event_data → recipe_updates=%s, meal_plan_confirmed=%s, answered_questions=%s",
            len(result.recipe_updates) if result.recipe_updates else 0,
            result.meal_plan_confirmed,
            result.answered_questions,
        )
        return result

    # -----------------------------------------------------------------------
    # Recipe extraction methods (called from API endpoints)
    # -----------------------------------------------------------------------

    async def extract_recipe_from_url(self, url: str) -> list[RecipeIngredient]:
        """Fetch a recipe URL and extract a structured ingredient list."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            page_content = resp.text[:_MAX_CONTENT_CHARS]

        prompt = f"""Extract the ingredient list from this recipe page.

                    Rules:
                    - Extract ONLY ingredients, not instructions.
                    - Standardise names ("olive oil" not "EVOO", "spring onions" not "scallions").
                    {INGREDIENT_UNIT_RULES}
                    - Assign each ingredient the most appropriate grocery_category.
                    - If the page doesn't contain a recipe, return an empty ingredients list.

                    Page content:
                    {page_content}
                    """
        result = await self._async_json_call(prompt, _ExtractedRecipe)
        return result.ingredients

    async def extract_recipe_from_file(
        self, content: bytes, mime_type: str
    ) -> tuple[Optional[str], list[RecipeIngredient]]:
        """Extract dish name and ingredients from an uploaded file.

        Returns: (dish_name, ingredients) where dish_name may be None if not found.
        """
        if mime_type.startswith("image/"):
            parts = [
                types.Part.from_bytes(data=content, mime_type=mime_type),
                types.Part(
                    text=(
                        "Extract the dish name and ingredient list from this recipe image.\n\n"
                        "Rules:\n"
                        "- For dish_name: extract the recipe title/name (e.g., 'Spaghetti Carbonara', 'Chocolate Chip Cookies'). "
                        "If no clear title is visible, leave it null.\n"
                        "- Extract ONLY ingredients, not instructions.\n"
                        "- Standardise names ('olive oil' not 'EVOO').\n"
                        + INGREDIENT_UNIT_RULES
                        + "- Assign each ingredient the most appropriate grocery_category.\n"
                        "- If the image doesn't contain a recipe, return null dish_name and empty ingredients list."
                    )
                ),
            ]
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_ExtractedRecipe,
                ),
            )
            return (response.parsed.dish_name, response.parsed.ingredients)
        else:
            text_content = content.decode("utf-8", errors="replace")[:_MAX_CONTENT_CHARS]
            contents = f"""Extract the dish name and ingredient list from this recipe text.

                        Rules:
                        - For dish_name: extract the recipe title/name (e.g., "Spaghetti Carbonara", "Chocolate Chip Cookies").
                          If no clear title is present, leave it null.
                        - Extract ONLY ingredients, not instructions.
                        - Standardise names ("olive oil" not "EVOO").
                        {INGREDIENT_UNIT_RULES}
                        - Assign each ingredient the most appropriate grocery_category.
                        - If the text doesn't contain a recipe, return null dish_name and empty ingredients list.

                        Recipe text:
                        {text_content}
                        """
        result = await self._async_json_call(contents, _ExtractedRecipe)
        return (result.dish_name, result.ingredients)

    async def generate_default_recipe(self, dish_name: str) -> list[RecipeIngredient]:
        """
        Generate a standard ingredient list for a dish using AI defaults.

        Produces a base-quantity recipe (for ~4 adult servings). The agent
        scales these to the actual guest count later via get_dish_ingredients.
        """
        results = await self.generate_default_recipes_batch([dish_name])
        return results[0]

    async def generate_default_recipes_batch(
        self, dish_names: list[str]
    ) -> list[list[RecipeIngredient]]:
        """
        Generate ingredient lists for multiple dishes in a single AI call.

        Uses a lighter/faster model since this is a simple structured task.
        Returns a list of ingredient lists in the same order as dish_names.
        """
        if not dish_names:
            return []

        dishes_list = "\n".join(f"- {name}" for name in dish_names)
        prompt = f"""Provide complete ingredient lists for each of these dishes:
                    {dishes_list}

                    Rules:
                    - Use a standard recipe for 4 adult servings per dish.
                    - Return one entry in 'dishes' per dish, in the same order listed above.
                    - List all ingredients needed to make each dish.
                    - Standardise names ("olive oil" not "EVOO", "spring onions" not "scallions").
                    {INGREDIENT_UNIT_RULES}
                    {BASE_RECIPE_QUANTITY_GUIDE}
                    - Assign each ingredient the most appropriate grocery_category.
                    - Do NOT include water.
                    """
        logger.info("🤖 AI CALL: generate_default_recipes_batch (dishes=%d, model=%s)", len(dish_names), self.fast_model_name)
        result = await self._async_json_call(
            prompt,
            _BatchExtractedRecipes,
            temperature=0.2,
            model=self.fast_model_name,
        )
        logger.info("✅ AI RESPONSE: generate_default_recipes_batch → %d dishes", len(result.dishes))
        return [dish.ingredients for dish in result.dishes]

    async def extract_recipe_from_description(self, description: str) -> list[RecipeIngredient]:
        """
        Extract a structured ingredient list from a conversational recipe description.

        Example input: "it's a mayo-based potato salad with hard boiled eggs, dill pickles,
        celery, and yellow mustard"
        """
        prompt = f"""A user described their recipe like this:
                    "{description}"

                    Extract a complete ingredient list from this description.

                    Rules:
                    - Include ALL ingredients the user mentioned.
                    - Add obvious base ingredients they may have omitted (e.g., salt, pepper,
                      the base starch/protein if implied).
                    - Estimate reasonable quantities for a standard recipe (we'll scale later).
                    - Standardise names ("olive oil" not "EVOO").
                    {INGREDIENT_UNIT_RULES}
                    - Assign each ingredient the most appropriate grocery_category.
                    """
        result = await self._async_json_call(prompt, _ExtractedRecipe, model=self.fast_model_name)
        return result.ingredients

    async def generate_recipe_instructions_batch(
        self,
        dishes: list[tuple[str, list[dict], int]],
    ) -> dict[str, list[str]]:
        """
        Generate step-by-step cooking instructions for multiple dishes.

        Each tuple is (dish_name, scaled_ingredients_as_dicts, total_servings).
        The ingredient lists are already scaled to the final guest count so the
        instructions can reference exact quantities.

        Returns a dict mapping dish_name → list of instruction strings.
        Uses the fast model since this is a structured but creative task.
        """
        if not dishes:
            return {}

        dishes_text = ""
        for dish_name, ingredients, total_servings in dishes:
            ingredient_lines = "\n".join(
                f"  - {ing.get('quantity', '')} {ing.get('unit', '')} {ing.get('name', '')}".strip()
                for ing in ingredients
            )
            dishes_text += (
                f"\nDish: {dish_name}\n"
                f"Servings: {total_servings}\n"
                f"Ingredients:\n{ingredient_lines}\n"
            )

        prompt = f"""Write step-by-step cooking instructions for each dish below.
                    The ingredient quantities are already scaled to the number of servings listed.

                    Rules:
                    - Write clear, practical instructions a home cook can follow.
                    - Reference the specific ingredients and quantities provided.
                    - Each instruction step should be a single complete action.
                    - Include timing guidance where helpful (e.g., "cook for 10 minutes").
                    - Do NOT add ingredients not in the list.
                    - Return one entry in 'recipes' per dish, in the same order listed.

                    Dishes:
                    {dishes_text}
                    """

        logger.info(
            "🤖 AI CALL: generate_recipe_instructions_batch (dishes=%d, model=%s)",
            len(dishes),
            self.fast_model_name,
        )
        result: _RecipeDetailsBatch = await self._async_json_call(
            prompt,
            _RecipeDetailsBatch,
            temperature=0.4,
            model=self.fast_model_name,
        )
        logger.info(
            "✅ AI RESPONSE: generate_recipe_instructions_batch → %d dishes",
            len(result.recipes),
        )
        return {r.dish_name: r.instructions for r in result.recipes}

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
        logger.info("🤖 AI CALL: categorise_dishes (dishes=%d)", len(meal_plan))
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
        mapping: _DishCategoryMapping = await self._async_json_call(
            prompt, _DishCategoryMapping, temperature=0.0, model=self.fast_model_name
        )
        result = {item.dish_name: item.category for item in mapping.items}
        logger.info("✅ AI RESPONSE: categorise_dishes → %s", result)
        return result

    async def get_dish_ingredients(
        self,
        spec: DishServingSpec,
        recipe: Optional["Recipe"] = None,
        dietary_restrictions: list[DietaryRestriction] = [],
    ) -> DishIngredients:
        """
        Given a DishServingSpec (dish name + serving counts), return a
        scaled ingredient list for that exact number of servings.

        If recipe has ingredients (from URL/file/description/AI default),
        those are included in the prompt so Gemini scales the user's actual recipe
        rather than inventing one.

        One call per dish — these are fanned out in parallel by
        agent/steps.py:get_all_dish_ingredients().
        """
        # Build recipe context with explicit scale factor.
        # Using scale_factor = total_servings / base_servings means Gemini multiplies
        # each ingredient by a single number rather than re-interpreting what "1 serving"
        # means — which was causing over-purchasing for multi-ingredient dishes.
        recipe_context = ""
        scale_factor: float | None = None
        base_servings: int | None = None
        if recipe and recipe.ingredients:
            base_servings = recipe.servings or 4
            scale_factor = round(spec.total_servings / base_servings, 4) if base_servings > 0 else 1.0
            recipe_context = (
                f"\n Base recipe ({base_servings} servings) — multiply every quantity by"
                f" {scale_factor:.2f}x to reach {spec.total_servings} servings:\n"
                f" {json.dumps(recipe.ingredients, indent=2)}\n"
            )

        serving_hint = CATEGORY_SERVING_HINTS.get(spec.dish_category, "")
        serving_hint_line = f"- Serving size reference: {serving_hint}" if serving_hint else ""

        dietary_note = ""
        if dietary_restrictions:
            lines = [f"  - {r.count} guest(s): {r.type}" for r in dietary_restrictions]
            dietary_note = (
                "DIETARY RESTRICTIONS (strict — do NOT include any violating ingredients):\n"
                + "\n".join(lines)
                + "\n"
            )

        # Special handling for beverages - they should just list the beverage, not a recipe
        is_beverage = spec.dish_category in (
            DishCategory.BEVERAGE_ALCOHOLIC,
            DishCategory.BEVERAGE_NONALCOHOLIC,
        )

        if is_beverage:
            prompt = f"""You are a professional chef. Provide the ingredient list for this BEVERAGE:

                    Beverage: {spec.dish_name}
                    Dish category: {spec.dish_category}
                    Adult servings: {spec.adult_servings}
                    Child servings: {spec.child_servings}
                    Total servings: {spec.total_servings}

                    CRITICAL: This is a BEVERAGE, not a food dish. Return ONLY the beverage itself as the ingredient.
                    Do NOT create a recipe or list ingredients for a sauce/dish that uses this beverage.

                    {dietary_note}Rules for beverages:
                    - For wine: list "wine" (red/white/rosé as appropriate) in bottles
                    - For beer: list "beer" in cans or bottles
                    - For cocktails: list the spirits and mixers needed
                    - For non-alcoholic: list the beverage (water, juice, soda, etc.)
                    {serving_hint_line}
                    {INGREDIENT_UNIT_RULES}
                    - Use appropriate units: bottles for wine, cans/bottles for beer, liters for bulk drinks
                    """
        elif scale_factor is not None:
            prompt = f"""You are a professional chef. Scale this recipe to the target serving count.

                    Dish: {spec.dish_name}
                    Dish category: {spec.dish_category}
                    Adult servings: {spec.adult_servings}
                    Child servings: {spec.child_servings}
                    Total servings: {spec.total_servings}
                    {recipe_context}
                    {dietary_note}Rules:
                    - Multiply EVERY ingredient quantity by exactly {scale_factor:.2f}x ({base_servings} → {spec.total_servings} servings).
                    - Preserve ALL ingredients from the base recipe — do not add or remove any.
                    - Do NOT apply any per-serving protein or carb targets; the scale factor is the only quantity guide.
                    {INGREDIENT_UNIT_RULES}
                    - Standardise ingredient names ("olive oil" not "EVOO", "spring onions" not "scallions").
                    - Assign each ingredient the most appropriate grocery_category.
                    """
        else:
            # Fallback: no base recipe available — generate quantities from scratch.
            prompt = f"""You are a professional chef. Provide a complete ingredient list for:

                    Dish: {spec.dish_name}
                    Dish category: {spec.dish_category}
                    Adult servings: {spec.adult_servings}
                    Child servings: {spec.child_servings}
                    Total servings: {spec.total_servings}

                    {dietary_note}Rules:
                    - Generate appropriate quantities for {spec.total_servings} total servings.
                    {serving_hint_line}
                    - Child servings are ~60% of an adult serving for food items.
                    - For dishes with multiple proteins (e.g., shrimp, clams, mussels), the serving hint
                      is the TOTAL across all proteins combined — divide it across each protein type.
                    {INGREDIENT_UNIT_RULES}
                    - Standardise ingredient names ("olive oil" not "EVOO", "spring onions" not "scallions").
                    - Include ALL components (e.g., dressing AND leaves for a Caesar salad).
                    - Assign each ingredient the most appropriate grocery_category.
                    """
        logger.info(
            "🤖 AI CALL: get_dish_ingredients (dish=%s, category=%s, servings=%d)",
            spec.dish_name,
            spec.dish_category.value,
            spec.total_servings,
        )
        logger.info(f"Getting ingredients for recipe: {recipe.model_dump() if recipe else 'No user-provided recipe'}")
        result: DishIngredients = await self._async_json_call(
            prompt, DishIngredients, temperature=0.0, model=self.fast_model_name
        )
        # Ensure the serving_spec is attached (Gemini won't include it)
        result.serving_spec = spec
        logger.info(
            "✅ AI RESPONSE: get_dish_ingredients (%s) → %d ingredients",
            spec.dish_name,
            len(result.ingredients),
        )
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
        logger.info("🤖 AI CALL: aggregate_ingredients (dishes=%d)", len(all_dish_ingredients))
        dishes_json = json.dumps([d.model_dump(mode="json") for d in all_dish_ingredients], indent=2)

        prompt = f"""You are a grocery list builder. Aggregate the ingredient lists
                    below into a single deduplicated shopping list.

                    Rules:
                    - Combine identical or synonymous ingredients (e.g. treat "scallions" and
                      "spring onions" as the same item; use the more common name).
                    - Treat ingredient variants that differ only in specificity as the same item.
                      Use the most specific name that covers all uses. Examples:
                        "olive oil" + "extra virgin olive oil" → "extra virgin olive oil"
                        "red wine" + "dry red wine" → "dry red wine"
                        "white wine" + "dry white wine" → "dry white wine"
                        "butter" + "unsalted butter" → "unsalted butter"
                      Never list both a generic and a specific variant of the same ingredient.
                    - Sum quantities for the same ingredient, converting to consistent units where
                      needed (e.g. 4 tbsp + 2 tbsp = 6 tbsp; 8 oz + 8 oz = 1 lb).
                    {INGREDIENT_UNIT_RULES}
                    - Prefer lbs over oz when total is ≥ 16 oz.
                    - Set appears_in to the list of dish names that use each ingredient.
                    - Assign the most appropriate grocery_category to each item.

                    Ingredient lists by dish:
                    {dishes_json}
                    """
        result: _AggregatedItems = await self._async_json_call(
            prompt, _AggregatedItems, temperature=0.0, model=self.fast_model_name
        )
        logger.info("✅ AI RESPONSE: aggregate_ingredients → %d unique items", len(result.items))
        # Construct ShoppingList — runner fills in guest counts from AgentState
        return ShoppingList(
            meal_plan=[d.dish_name for d in all_dish_ingredients],
            adult_count=0,  # overwritten by runner
            child_count=0,  # overwritten by runner
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
        list_json = json.dumps(shopping_list.model_dump(mode="json"), indent=2)

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
        result: _AggregatedItems = await self._async_json_call(
            prompt, _AggregatedItems, temperature=0.0
        )
        return ShoppingList(
            meal_plan=shopping_list.meal_plan,
            adult_count=shopping_list.adult_count,
            child_count=shopping_list.child_count,
            total_guests=shopping_list.total_guests,
            items=result.items,
        )
