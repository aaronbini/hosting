import json
import os
from typing import Generator, Optional

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.models.event import EventPlanningData, ExtractionResult, RecipeSource
from app.models.shopping import (
    AggregatedIngredient,
    DishCategory,
    DishIngredients,
    DishServingSpec,
    RecipeIngredient,
    ShoppingList,
)

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
    DishCategory.SALAD: "1 adult serving ≈ 2 oz leafy greens (plus dressing ingredients)",
    DishCategory.BREAD: "1 serving ≈ 1 roll or 2 slices",
    DishCategory.DESSERT: "1 adult serving ≈ 1 standard slice or portion",
    DishCategory.PASSED_APPETIZER: "1 serving ≈ 1-2 bite-sized pieces",
    DishCategory.BEVERAGE_ALCOHOLIC: "1 serving ≈ 12 fl oz beer, 5 fl oz wine, or 1.5 fl oz spirit",
    DishCategory.BEVERAGE_NONALCOHOLIC: "1 serving ≈ 10 fl oz",
}

INGREDIENT_UNIT_RULES = """
- Use shopping-friendly units that match how items are actually sold:
    * Proteins (meat, fish): oz or lbs
    * Dry goods (pasta, rice, flour, sugar, breadcrumbs, oats, lentils): oz or lbs — NEVER cups
    * Fresh produce (vegetables, fruit): oz, lbs, count, or bunch as appropriate
    * Liquids (broth, wine, cream, milk): fl oz, pints, quarts, or liters
    * Small liquid amounts (oil, sauces, condiments): tbsp or fl oz
    * Spices and seasonings: tsp or tbsp
    * Eggs, lemons, onions, whole items: count
    * Garlic: bulbs or heads. if cloves are specified in the recipe, convert to bulbs (1 bulb ≈ 10 cloves).
    * Canned goods: cans
    * Packaged items: packages
- Do NOT use cups for any solid or dry ingredient.
- Do NOT include water — it is never a grocery item.
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

    ingredients: list[RecipeIngredient]


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

        self.system_prompt = """You are a conversational event planning assistant helping someone plan a menu, as well as how much food to buy for their event.

                            CURRENT STAGE: {conversation_stage}
                            CURRENT EVENT DATA: {event_data_json}

                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                            STAGE-SPECIFIC INSTRUCTIONS:
                            ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                            IF conversation_stage == "gathering":

                              Your goal: collect event details AND arrive at a specific list of dishes (the meal plan).

                              IMPORTANT — Recipe promises take priority:
                                Check CURRENT EVENT DATA for "recipe_promises". If it is non-empty, you MUST:
                                1. Focus EXCLUSIVELY on collecting those recipe sources.
                                2. Do NOT present a full provisional menu for all dishes.
                                3. Do NOT move on to other topics.
                                4. For each promised dish, ask how the user wants to share the recipe:
                                   URL (paste in chat), file (upload panel below), or describe it.
                                5. Handle one at a time. Once all recipe_promises are empty, you can proceed normally.

                              Phase 1 — Event basics (ask one question at a time):
                                1. Event/Meal type  2. Guest count (adults/children)
                                3. Dietary restrictions   4. Cuisine preference

                              Phase 2 — Menu building:
                                Once you have enough context (at minimum: event type, guest count, meal type),
                                start working toward specific dishes.

                                - ALWAYS ask first: "Do you have specific dishes in mind, or would you like me
                                  to suggest a menu?"
                                - If user provides dishes: acknowledge them, ask if they want to add more
                                  categories (appetizer, sides, dessert, beverages).
                                - If user wants suggestions: propose a COMPLETE menu (main, sides, dessert)
                                  based on cuisine preference, guest count, and event type. Present it as a
                                  numbered list they can modify.
                                - If user provides SOME dishes and wants help with others: suggest dishes
                                  that complement what they already chose.
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

                              The menu is locked. Now confirm the recipe/preparation for each dish.

                              For each dish in the meal plan, present ALL dishes at once in a numbered list with
                              your assumed recipe approach. Example:
                              "Let's confirm recipes for each dish:
                              1. Ribs — I'll assume classic dry-rubbed smoked BBQ ribs with vinegar sauce
                              2. Potato salad — classic mayo-based with eggs and celery
                              3. Cornbread — traditional Southern-style"

                              Ask the user to confirm or correct any dish. For dishes where the user has their own recipe:
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

                            IF conversation_stage == "selecting_output":

                              All recipes are confirmed. Ask how they want their shopping list delivered:

                              1. **Google Sheet** — formula-driven spreadsheet, quantities auto-adjust
                              2. **Google Keep** — checklist format, great for shopping on your phone
                              3. **In-chat list** — formatted list right here in the conversation
                              4. **Any combination** of the above

                              Ask once, accept their choice, then confirm.

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

    def generate_response(
        self, user_message: str, event_data: EventPlanningData, conversation_history: list
    ) -> str:
        """
        Generate conversational AI response using Gemini.
        """
        event_json = json.dumps(event_data.model_dump(exclude_none=True), indent=2)
        system_with_context = self.system_prompt.format(
            event_data_json=event_json, conversation_stage=event_data.conversation_stage
        )

        # Build conversation history in new SDK format
        contents = []
        for msg in conversation_history[-10:]:
            contents.append(
                types.Content(
                    role="user" if msg.role == "user" else "model",
                    parts=[types.Part(text=msg.content)],
                )
            )
        contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_with_context,
            ),
        )

        return response.text

    def generate_response_stream(
        self, user_message: str, event_data: EventPlanningData, conversation_history: list
    ) -> Generator[str, None, None]:
        """Yields text chunks as Gemini streams the response."""
        event_json = json.dumps(event_data.model_dump(exclude_none=True), indent=2)
        system_with_context = self.system_prompt.format(
            event_data_json=event_json, conversation_stage=event_data.conversation_stage
        )

        # Build conversation history in new SDK format
        contents = []
        for msg in conversation_history[-10:]:
            contents.append(
                types.Content(
                    role="user" if msg.role == "user" else "model",
                    parts=[types.Part(text=msg.content)],
                )
            )
        contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))

        for chunk in self.client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system_with_context),
        ):
            if chunk.text:
                yield chunk.text

    def extract_event_data(
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
        current_json = json.dumps(current_event_data.model_dump(exclude_none=True), indent=2)
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
                    - For event_date, convert to ISO format YYYY-MM-DD. Today is {__import__("datetime").date.today().isoformat()}.
                    - For answered_questions, include the ID of every question the message addresses.

                    Valid answered_questions IDs: event_type, event_date, guest_count, guest_breakdown,
                    meal_type, cuisine, beverages, dietary, equipment, budget, formality, meal_plan

                    Stage-specific extraction rules:

                    IF stage == "gathering":
                    - Extract standard event fields (event_type, guest counts, cuisine, etc.) as before.
                    - For meal_plan_additions: list any SPECIFIC dish names that are now part of the agreed
                      menu. This includes:
                      (a) dishes the user explicitly names in their message, AND
                      (b) dishes proposed by the assistant (in the previous message) that the user is now
                          confirming (e.g., user says "yes", "looks good", "that works", "perfect").
                      Only include concrete dishes ("pasta carbonara", "ribs"), NOT vague categories
                      ("something Italian", "a side dish").
                    - For meal_plan_removals: list dishes the user explicitly says to remove or replace.
                    - For meal_plan_confirmed: set to true ONLY if the user explicitly confirms the full
                      menu is complete ("looks good", "yes let's go with that menu", "that's everything").
                    - Include "meal_plan" in answered_questions ONLY when meal_plan_confirmed is true,
                      NOT when individual dishes are first mentioned.
                    - For recipe_promise_additions: list any dishes where the user explicitly says they
                      have their OWN recipe (e.g., "I have a recipe for X", "I found X on NYTimes",
                      "I have a PDF for X"). Do NOT include dishes the assistant is assuming defaults for.
                    - For recipe_promise_resolutions: list dishes whose recipe promise is now resolved
                      in this message — i.e., the user described the recipe, said to use your best guess,
                      or explicitly gave up on providing a specific recipe for that dish.
                    - For pending_upload_dish: set to the dish name if the user explicitly says they have
                      a FILE (PDF, photo, screenshot, recipe card) to upload for a specific dish. Set to
                      null in all other cases (URL, description, general message, unrelated messages).

                    IF stage == "recipe_confirmation":
                    - Focus on recipe_confirmations: for each dish the user addresses, include an entry
                      with dish_name, confirmed (true/false), and optionally source_type, description, url.
                    - If the user provides a URL for a dish's recipe, set url to that URL and
                      source_type to "user_url". The system will auto-fetch and extract it.
                    - Set recipes_confirmed to true ONLY if the user confirms ALL recipes are good.
                    - Set pending_upload_dish to the dish name if the user indicates they have a FILE
                      (PDF, photo, screenshot, recipe card) to upload for a specific dish. Set to null
                      in all other cases (URL, description, general confirmation, unrelated messages).
                    - Ignore event-level fields (event_type, guest_count, etc.) unless the user is
                      explicitly correcting them.

                    IF stage == "selecting_output":
                    - Focus on output_formats: extract the user's chosen format(s) as a list.
                      Valid values: "google_sheet", "google_keep", "in_chat".
                    - Ignore event-level and recipe fields.

                    Current known data:
                    {current_json}
                    {assistant_context}
                    User message: "{user_message}"
                """

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ExtractionResult,
            ),
        )

        return response.parsed

    # -----------------------------------------------------------------------
    # Recipe extraction methods (called from API endpoints)
    # -----------------------------------------------------------------------

    async def extract_recipe_from_url(self, url: str) -> list[RecipeIngredient]:
        """
        Fetch a recipe URL and extract a structured ingredient list.

        Uses httpx to fetch the page, then Gemini to parse the content.
        """
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            page_content = resp.text

        # Truncate to avoid exceeding token limits on very large pages
        max_chars = 30000
        if len(page_content) > max_chars:
            page_content = page_content[:max_chars]

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
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ExtractedRecipe,
            ),
        )
        return response.parsed.ingredients

    async def extract_recipe_from_file(
        self, content: bytes, mime_type: str
    ) -> list[RecipeIngredient]:
        """
        Extract ingredients from an uploaded file.

        For images (jpg/png): uses Gemini vision.
        For text/PDF: extracts text content and passes to Gemini.
        """
        if mime_type.startswith("image/"):
            # Use Gemini vision for images (recipe cards, cookbook photos)
            parts = [
                types.Part.from_bytes(data=content, mime_type=mime_type),
                types.Part(
                    text=(
                        "Extract the ingredient list from this recipe image.\n\n"
                        "Rules:\n"
                        "- Extract ONLY ingredients, not instructions.\n"
                        "- Standardise names ('olive oil' not 'EVOO').\n"
                        + INGREDIENT_UNIT_RULES
                        + "- Assign each ingredient the most appropriate grocery_category.\n"
                        "- If the image doesn't contain a recipe, return an empty ingredients list."
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
        else:
            # Text-based files (txt, pdf text extraction)
            text_content = content.decode("utf-8", errors="replace")
            max_chars = 30000
            if len(text_content) > max_chars:
                text_content = text_content[:max_chars]

            prompt = f"""Extract the ingredient list from this recipe text.

                        Rules:
                        - Extract ONLY ingredients, not instructions.
                        - Standardise names ("olive oil" not "EVOO").
                        {INGREDIENT_UNIT_RULES}
                        - Assign each ingredient the most appropriate grocery_category.
                        - If the text doesn't contain a recipe, return an empty ingredients list.

                        Recipe text:
                        {text_content}
                        """
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_ExtractedRecipe,
                ),
            )
        return response.parsed.ingredients

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
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ExtractedRecipe,
            ),
        )
        return response.parsed.ingredients

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
                temperature=0.0,
            ),
        )
        mapping: _DishCategoryMapping = response.parsed
        return {item.dish_name: item.category for item in mapping.items}

    async def get_dish_ingredients(
        self,
        spec: DishServingSpec,
        recipe_source: Optional["RecipeSource"] = None,
    ) -> DishIngredients:
        """
        Given a DishServingSpec (dish name + serving counts), return a
        scaled ingredient list for that exact number of servings.

        If recipe_source has extracted_ingredients (from URL/file/description),
        those are included in the prompt so Gemini scales the user's actual recipe
        rather than inventing one.

        One call per dish — these are fanned out in parallel by
        agent/steps.py:get_all_dish_ingredients().
        """
        # Build recipe context from user-provided source if available
        recipe_context = ""
        if recipe_source and recipe_source.extracted_ingredients:
            recipe_context = (
                f"\n                    The user provided this recipe's ingredient list (scale these):\n"
                f"                    {json.dumps(recipe_source.extracted_ingredients, indent=2)}\n"
            )

        serving_hint = CATEGORY_SERVING_HINTS.get(spec.dish_category, "")
        serving_hint_line = f"- Serving size reference: {serving_hint}" if serving_hint else ""

        prompt = f"""You are a professional chef. Provide a complete ingredient list for:

                    Dish: {spec.dish_name}
                    Dish category: {spec.dish_category}
                    Adult servings: {spec.adult_servings}
                    Child servings: {spec.child_servings}
                    Total servings: {spec.total_servings}
                    {recipe_context}
                    Rules:
                    - Scale the recipe exactly to the above serving counts.
                    {serving_hint_line}
                    - Child servings are ~60% of an adult serving for food items.
                    {INGREDIENT_UNIT_RULES}
                    - Standardise ingredient names ("olive oil" not "EVOO", "spring onions" not "scallions").
                    - Include ALL components (e.g., pastry AND filling for sausage rolls,
                      dressing AND leaves for a Caesar salad).
                    - Assign each ingredient the most appropriate grocery_category.
                    - If user-provided ingredients are included above, use those as the base
                      recipe and scale them. Do NOT substitute different ingredients.
                    """
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=DishIngredients,
                temperature=0.0,
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
        dishes_json = json.dumps([d.model_dump() for d in all_dish_ingredients], indent=2)

        prompt = f"""You are a grocery list builder. Aggregate the ingredient lists
                    below into a single deduplicated shopping list.

                    Rules:
                    - Combine identical or synonymous ingredients (e.g. treat "scallions" and
                      "spring onions" as the same item; use the more common name).
                    - Sum quantities for the same ingredient, converting to consistent units where
                      needed (e.g. 4 tbsp + 2 tbsp = 6 tbsp; 8 oz + 8 oz = 1 lb).
                    {INGREDIENT_UNIT_RULES}
                    - Prefer lbs over oz when total is ≥ 16 oz.
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
                temperature=0.0,
            ),
        )
        result: _AggregatedItems = response.parsed
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
                temperature=0.0,
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
