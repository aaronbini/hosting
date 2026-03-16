# RAG for Recipe Quality: Design Notes

The app sometimes generates strange recipes: weird ingredient lists, odd quantities, or dishes no one would want to make. This doc outlines how **Retrieval Augmented Generation (RAG)** can help and what data to use.

---

## Can RAG Help? Yes.

Right now, recipe content is generated in three places:

| Place | What it does | Why it goes wrong |
|-------|----------------|-------------------|
| **`generate_default_recipes_batch`** | Creates ingredient lists for 4 servings from scratch using only prompt rules (`BASE_RECIPE_QUANTITY_GUIDE`, `INGREDIENT_UNIT_RULES`). | No real recipe grounding — the model invents from general knowledge, which can be inconsistent or odd. |
| **`get_dish_ingredients`** (fallback) | When there’s no base recipe, generates quantities from scratch using `CATEGORY_SERVING_HINTS`. | Same issue: no concrete recipe to anchor to. |
| **`generate_recipe_instructions_batch`** | Writes step-by-step instructions from scaled ingredients. | Can produce non-standard or confusing steps without a reference. |

RAG helps by **retrieving real recipes (or reference data)** and **injecting them into the prompt** so the model *adapts* rather than *invents*. That yields:

- **Plausible ingredient lists** — e.g. “Spaghetti Carbonara” is grounded in real carbonara recipes.
- **Reasonable quantities** — retrieved recipes give 4-serving baselines; your existing scaling logic still applies.
- **Familiar dishes** — retrieval can prefer well-known, well-liked recipes.

---

## What Data to Use

### 1. Curated recipe corpus (main RAG source)

**What:** Structured recipes with at least:

- **Dish name** (and optional variants, e.g. “Spaghetti Carbonara”, “Carbonara”)
- **Ingredient list** with names and quantities for a fixed base serving (e.g. 4)
- **Category / cuisine** (optional but useful for retrieval)

**Why:** This is the primary signal. When the user asks for “Focaccia”, you retrieve 1–3 real focaccia recipes and say: “Produce an ingredient list for **Focaccia** in the same style and completeness as these examples. Use 4 servings. Follow our unit rules.”

**Possible sources:**

- **Public datasets:** Recipe1M, Epicurious, or other open recipe datasets (check licenses). You’d normalize to your schema (dish name, ingredients with quantity+unit, category).
- **Curated list:** Manually add 100–500 “canonical” recipes for the dishes you see most (pastas, roasts, salads, common cocktails, etc.). High quality, full control.
- **Recipe APIs:** Spoonacular, Edamam, etc. Use their search by dish name and cache results (or use as live retrieval). Be mindful of rate limits and ToS.

**Indexing:** Embed (or keyword) by **dish name** and optionally **cuisine/category**. Store per-recipe or per-dish. Chunk = one recipe (title + ingredients + optional category). At generation time: query by dish name (and optionally event cuisine) → top-k recipes → pass into prompt.

### 2. Quantity / serving rules (you already have most of this)

**What:** Rules like “1 adult main ≈ 6 oz protein”, “pasta 2–3 oz dry per person”, “garlic ½–1 bulb for 4”. You already have `CATEGORY_SERVING_HINTS` and `BASE_RECIPE_QUANTITY_GUIDE`.

**Why:** They constrain quantities so the model doesn’t over- or under-buy. They’re not “retrieved” in the classic RAG sense but can be:

- Kept as static prompt text (current approach), or
- Stored as small “rule” documents and retrieved by dish category when you don’t have a good recipe match (e.g. “unknown dish” → retrieve “main protein rules”).

So: **quantity rules** can stay as-is or become a small retrievable layer; the big win is **recipe** RAG.

### 3. Optional: user-accepted recipes

**What:** When a user keeps an AI-generated recipe (or provides one via URL/upload and doesn’t change it), you could store a fingerprint (dish name + normalized ingredients) and treat it as a “good” example.

**Why:** Over time you could bias retrieval toward “recipes users actually kept” (e.g. in a separate index or as a boost in the main index). This is a later-phase improvement; not required for the first RAG version.

---

## Where to Plug RAG In

### Priority 1: `generate_default_recipes_batch` (default ingredient lists)

**Flow today:**  
Dish names → one prompt with rules → model invents ingredient lists for 4 servings.

**Flow with RAG:**

1. For each dish name (or in batch), **query the recipe index** (by dish name ± cuisine).
2. For each dish, get **top-k** recipes (e.g. 1–2). Format them as “Reference recipe(s) for [Dish]: …” with ingredients (and optionally quantities).
3. **Augment the prompt:** “Using these reference recipes as a guide for ingredients and proportions, produce a single consolidated ingredient list for [Dish] for 4 adult servings. Follow our unit and category rules. If no reference is found, generate a standard recipe.”
4. Call the same `_BatchExtractedRecipes`-style API; the model now *adapts* instead of inventing.

**Impact:** Directly attacks “weird ingredient lists” and “recipes no one would make” by anchoring to real recipes. Quantity norms (`BASE_RECIPE_QUANTITY_GUIDE`) still apply.

### Priority 2: `get_dish_ingredients` fallback (no base recipe)

**Flow today:**  
Dish + category + serving hint → model generates quantities from scratch.

**Flow with RAG:**  
Same idea: retrieve 1–2 recipes for that dish name (and maybe category). Add “Reference recipe(s): …” to the prompt so the fallback path is also grounded. Keeps behavior consistent with Priority 1.

### Priority 3 (optional): `generate_recipe_instructions_batch`

**Flow today:**  
Scaled ingredients → model writes step-by-step instructions.

**Flow with RAG:**  
Retrieve full recipes (title + ingredients + **instructions**). Pass 1 reference recipe’s method as context: “Produce step-by-step instructions for [Dish] for [N] servings, in the same style as this example. Use these exact ingredients and quantities: …”. Reduces odd or non-standard methods.

---

## Implementation Sketch

### Data model (minimal)

- **Recipe document:** `{ "dish_name": str, "ingredients": [{ "name", "quantity", "unit" }], "base_servings": int, "category"?: str, "cuisine"?: str }`. Optionally add `instructions` for Priority 3.
- **Index:** Vector store (e.g. Chroma, pgvector, or in-memory FAISS for MVP) with embeddings of `dish_name` (and optionally `category` + `cuisine`). One vector per recipe; metadata holds the full document.

### Embedding and retrieval

- **Embed:** Concatenate `dish_name` + optional category/cuisine (e.g. `"Spaghetti Carbonara Italian main"`) and embed with the same model you use elsewhere (e.g. Gemini embedding or a small local model). Store in the vector store.
- **Retrieve:** Given current dish name (and optionally event cuisine), run a similarity search; take top-k (e.g. 2). If similarity is below a threshold, skip RAG for that dish and fall back to current behavior.

### Code touch points

- **New:** `services/recipe_retriever.py` (or similar): `async def retrieve_recipes(dish_name: str, cuisine: Optional[str], k: int) -> list[RecipeDoc]`.
- **Change:** `ai_service.generate_default_recipes_batch`: (1) call retriever per dish (or batch-query), (2) format retrieved recipes into the prompt, (3) same structured output.
- **Change:** `get_dish_ingredients` fallback branch: same — retrieve by dish name, add to prompt.

### MVP path

1. **No vector DB yet:** Build a small **curated JSON list** of 50–100 recipes (dish name, ingredients, base_servings). At call time, do **exact or fuzzy match by dish name** (e.g. normalize to lowercase, strip punctuation, or use simple keyword overlap). Pass the best 1–2 matches into the prompt. That already gives you “RAG-like” behavior with minimal infra.
2. **Add embeddings:** When the list grows or you want semantic match (“Focaccia” → “Rosemary Focaccia”), add an embedding step and a small vector index (e.g. Chroma). Same retrieval interface, better recall.
3. **Optional:** Ingest from a public dataset or API into the same schema and index.

---

## Summary

| Question | Answer |
|----------|--------|
| **Can RAG help?** | Yes. It grounds generation in real recipes instead of pure model invention. |
| **What data?** | (1) Curated recipe corpus (main); (2) quantity/serving rules (you have these; optional to make them retrievable); (3) optional user-accepted recipes later. |
| **Where first?** | `generate_default_recipes_batch` — biggest impact on “weird ingredients / weird amounts / recipes no one would make.” Then `get_dish_ingredients` fallback, then optionally recipe instructions. |
| **MVP?** | Small curated list + name-based (or fuzzy) match in the prompt; add vector search when you need scale or semantic match. |

If you want to go deeper on one part (e.g. schema for the recipe corpus, or exact prompt changes for `generate_default_recipes_batch`), we can do that next.
