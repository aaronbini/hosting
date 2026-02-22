# Auto-Generation Performance Optimization Options

## Current Problem

Lines 443-448 in `backend/app/main.py` are slow because they make **one AI call per recipe** during the recipe_confirmation stage. Even though calls are parallelized with `asyncio.gather()`, this still takes a long time when there are multiple recipes.

**Current Code**:
```python
results = await asyncio.gather(
    *[
        ai_service.generate_default_recipe(r.name)
        for r in recipes_needing_ingredients
    ]
)
```

**Example**: 5 recipes = 5 parallel AI calls = ~5-10 seconds total

---

## Option 1: Batch All Recipes into Single AI Call ⭐ RECOMMENDED

**Speed Gain**: 5-10x faster
**Implementation Effort**: Medium
**Quality**: Same as current

### How It Works
Instead of calling the AI separately for each dish, call it once with all dish names and get all ingredients back in a single response.

**Before**: 5 dishes = 5 AI calls in parallel
**After**: 5 dishes = 1 AI call

### Implementation

**In `main.py` (lines 443-448)**:
```python
# Single batched call instead of multiple parallel calls
all_ingredients = await ai_service.generate_default_recipes_batch(
    [r.name for r in recipes_needing_ingredients]
)

newly_generated = []
for recipe, ingredients in zip(recipes_needing_ingredients, all_ingredients):
    recipe.ingredients = [i.model_dump() for i in ingredients]
    recipe.status = RecipeStatus.COMPLETE
    newly_generated.append(
        {"dish": recipe.name, "ingredients": recipe.ingredients}
    )
```

**New method in `ai_service.py`**:
```python
async def generate_default_recipes_batch(self, dish_names: list[str]) -> list[list[RecipeIngredient]]:
    """Generate ingredient lists for multiple dishes in a single AI call."""
    if not dish_names:
        return []

    dishes_list = "\n".join(f"- {name}" for name in dish_names)

    prompt = f"""Provide complete ingredient lists for these dishes:
{dishes_list}

Rules:
- Use standard recipes for 4 adult servings each.
- Return a list where each entry corresponds to one dish (in the same order).
- List all ingredients needed to make each dish.
- Standardise names ("olive oil" not "EVOO").
{INGREDIENT_UNIT_RULES}
- Assign each ingredient the most appropriate grocery_category.
- Do NOT include water.

Return format: list of RecipeIngredientList objects, one per dish."""

    class RecipeIngredientList(BaseModel):
        ingredients: List[RecipeIngredient]

    class BatchRecipeResponse(BaseModel):
        recipes: List[RecipeIngredientList]

    result = await self._async_json_call(prompt, BatchRecipeResponse, temperature=0.2)
    return [r.ingredients for r in result.recipes]
```

### Pros
- Massive speed improvement (5-10x)
- Maintains same quality as individual calls
- Still shows ingredients during recipe_confirmation stage
- Clean, maintainable implementation

### Cons
- Requires adding new method to ai_service
- Slightly more complex than current approach

---

## Option 2: Use Faster Model Variant

**Speed Gain**: 3-5x faster
**Implementation Effort**: Low
**Quality**: Slightly lower (but probably fine for simple ingredient lists)

### How It Works
Use `gemini-1.5-flash-8b` (lighter variant) instead of `gemini-3-flash-preview` for recipe generation.

### Implementation

**Update `_async_json_call` to accept model override**:
```python
async def _async_json_call(
    self,
    contents,
    schema,
    *,
    temperature: float | None = None,
    model: str | None = None  # NEW parameter
):
    model_name = model or self.model_name
    # ... rest of method uses model_name instead of self.model_name
```

**In `generate_default_recipe`**:
```python
async def generate_default_recipe(self, dish_name: str) -> list[RecipeIngredient]:
    # ... existing prompt ...
    result = await self._async_json_call(
        prompt,
        _ExtractedRecipe,
        temperature=0.2,
        model="gemini-1.5-flash-8b"  # Faster variant
    )
    return result.ingredients
```

### Pros
- Simple to implement (just parameter change)
- Moderate speed improvement
- Can combine with Option 1 for even more speed

### Cons
- Slightly lower quality output (less detailed ingredients)
- Still making multiple parallel calls

---

## Option 3: Skip Auto-Generation Entirely ⚡ FASTEST

**Speed Gain**: 100x (instant)
**Implementation Effort**: Very low
**Quality**: Same (agent handles it anyway)

### How It Works
Remove auto-generation completely. The agent already generates ingredients during the `agent_running` stage with proper scaling for guest count.

### Implementation

**Delete lines 426-456 in `main.py`**:
```python
# Simply remove or comment out the entire auto-generation block
# The agent's get_dish_ingredients() will handle it during agent_running stage
```

### Analysis
The auto-generation step might be **redundant**:
1. Auto-gen creates ingredients for 4 servings during `recipe_confirmation`
2. Agent then creates ingredients again with proper scaling during `agent_running`
3. You're generating the same thing twice!

### Pros
- Instant (no waiting)
- Simplifies codebase
- Reduces AI costs
- Agent already does this work with better scaling

### Cons
- User doesn't see ingredients during recipe_confirmation stage
- May need to adjust UI expectations
- Need to verify agent handles all cases correctly

---

## Comparison Table

| Approach | Speed | Effort | Quality | Maintains Current UX |
|----------|-------|--------|---------|---------------------|
| **Current (parallel)** | 1x (baseline) | - | ✓ | ✓ |
| **Option 1 (batch)** | 5-10x | Medium | ✓ | ✓ |
| **Option 2 (faster model)** | 3-5x | Low | ~ | ✓ |
| **Option 3 (skip)** | 100x | Very low | ✓ | ✗ |
| **Option 1 + 2 (both)** | 15-50x | Medium | ~ | ✓ |

---

## Recommendation

**Start with Option 1 (batched calls)** because:
- Significant speed improvement without sacrificing quality
- Maintains current user experience (ingredients visible during recipe_confirmation)
- Clean implementation that's maintainable
- Can still combine with Option 2 for even more speed if needed

**Consider Option 3 (skip entirely)** if you realize:
- The auto-generation step is redundant with agent processing
- Users don't actually need to see ingredients during recipe_confirmation
- You want the simplest, fastest solution

---

## Next Steps

1. Test current performance with 5-10 recipes to establish baseline
2. Implement Option 1 (batched calls)
3. Measure improvement
4. If still too slow, add Option 2 (faster model)
5. Consider removing auto-generation entirely if it's truly redundant

---

## Files to Modify

- **Option 1**: `backend/app/main.py` (lines 443-448), `backend/app/services/ai_service.py` (add new method)
- **Option 2**: `backend/app/services/ai_service.py` (update `_async_json_call` and `generate_default_recipe`)
- **Option 3**: `backend/app/main.py` (delete lines 426-456)
