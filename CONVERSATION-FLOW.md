# Conversation Flow — Dinner Party Planner

All possible conversation paths, including happy paths, user-driven scenarios, and error cases.

---

## Main Stage Flow

```mermaid
flowchart TD
    START([User opens app]) --> WS[WebSocket connects]
    WS --> G[**STAGE: gathering**]

    G --> CHAT_LOOP{AI sends extraction + streaming chat}
    CHAT_LOOP --> IS_COMPLETE{is_complete?}
    IS_COMPLETE -- No --> CHAT_LOOP
    IS_COMPLETE -- Yes --> RC[**STAGE: recipe_confirmation**<br/>meal_plan.confirmed = false]

    RC --> AUTO_GEN[Auto-generate ingredients<br/>for homemade dishes]
    AUTO_GEN --> RC_LOOP{User confirms/<br/>provides/modifies recipes}
    RC_LOOP --> CONFIRMED{meal_plan.confirmed = true?}
    CONFIRMED -- No --> RC_LOOP
    CONFIRMED -- Yes --> SO[**STAGE: selecting_output**]

    SO --> OUT_CARD[Show output format card:<br/>google_tasks / google_sheet / in_chat]
    OUT_CARD --> OUT_CHOSEN{output format selected?}
    OUT_CHOSEN -- No --> OUT_CARD
    OUT_CHOSEN -- Yes --> AR[**STAGE: agent_running**]

    AR --> AGENT[Run agent:<br/>calculate → ingredients → aggregate]
    AGENT --> REVIEW[**REVIEW LOOP**<br/>Present shopping list]
    REVIEW --> APPROVED{User approves?}
    APPROVED -- Corrections --> APPLY_CORR[Apply corrections via Gemini]
    APPLY_CORR --> REVIEW
    APPROVED -- Yes --> DELIVER[Deliver outputs in parallel]
    DELIVER --> COMPLETE([**STAGE: complete**<br/>Auto-save plan])
```

---

## Completion Gate — when does gathering end?

```mermaid
flowchart TD
    CG1{All 6 critical<br/>questions answered?} -- No --> BLOCK1[AI continues gathering]
    CG1 -- Yes --> CG2{≥1 recipe<br/>in meal_plan?}
    CG2 -- No --> BLOCK2[AI proposes dishes]
    CG2 -- Yes --> CG3{meal_plan.confirmed<br/>== true?}
    CG3 -- No --> BLOCK3[AI presents menu for confirmation]
    CG3 -- Yes --> CG4{Any recipe with<br/>awaiting_user_input = true?}
    CG4 -- Yes --> BLOCK4[AI collects promised recipes first]
    CG4 -- No --> COMPLETE([is_complete = true<br/>→ stage: recipe_confirmation])
```

**6 critical questions:** event_type, guest_count, guest_breakdown (adults vs children), dietary restrictions, cuisine preference, meal_plan (specific dishes confirmed).

---

## Recipe Source Decision Tree

```mermaid
flowchart TD
    RS1([Dish added to meal plan]) --> RS2{Who provides recipe?}
    RS2 -- AI default --> RS3[auto_generate_recipes in recipe_confirmation<br/>source: AI_DEFAULT]
    RS2 -- User promises URL --> RS4[awaiting_user_input = true<br/>Blocking: AI waits for URL]
    RS4 --> RS5[User pastes URL → extract_recipe_from_url<br/>source: USER_URL]
    RS2 -- User uploads file --> RS6[RecipeUploadPanel → upload-recipe endpoint<br/>source: USER_UPLOAD]
    RS2 -- User types description --> RS7[Gemini extracts from text<br/>source: USER_DESCRIPTION]
    RS2 -- Store-bought --> RS8[preparation_method = store_bought<br/>status = COMPLETE, no extraction]
    RS3 & RS5 & RS6 & RS7 --> RS9[status = COMPLETE<br/>awaiting_user_input = false]
    RS8 --> RS9
    RS9 --> RS10([Recipe ready for agent])
```

---

## Scenario A: App Chooses Everything

User gives minimal input, AI drives all decisions.

```mermaid
flowchart TD
    A1([User: 'Plan a dinner for 8']) --> A2[AI asks: event type, guest count, dietary, cuisine]
    A2 --> A3[User answers each question in turn]
    A3 --> A4[AI suggests full menu with placeholder names:<br/>'main', 'side', 'dessert']
    A4 --> A5[User: 'Sounds great!']
    A5 --> A6[Extraction: UPDATE placeholders → real names<br/>meal_plan.confirmed = true]
    A6 --> A7[is_complete = true → stage: recipe_confirmation]
    A7 --> A8[Auto-generate ingredients for all homemade dishes]
    A8 --> A9[AI presents ingredient lists per dish]
    A9 --> A10[User: 'Looks good, confirm!']
    A10 --> A11[meal_plan.confirmed = true → stage: selecting_output]
    A11 --> A12[User selects In-chat list]
    A12 --> A13[Agent: calculate quantities → scale ingredients → aggregate]
    A13 --> A14[Review: user approves]
    A14 --> A15([Shopping list + recipes delivered in chat])
```

---

## Scenario B: User Provides All Recipes via URLs

```mermaid
flowchart TD
    B1([User: 'I have recipes for everything']) --> B2[AI asks for event details]
    B2 --> B3[User answers + pastes URL for each dish]
    B3 --> B4{URL extraction attempt}
    B4 -- Success --> B5[recipe: status=COMPLETE, source=USER_URL<br/>awaiting_user_input=false]
    B4 -- 403/404/network error --> B6[last_url_extraction_result stores error<br/>AI surfaces failure loudly]
    B6 --> B7[User pastes different URL or provides description]
    B7 --> B4
    B5 --> B8[All recipes complete → is_complete = true]
    B8 --> B9[stage: recipe_confirmation<br/>auto-generate skipped — recipes already complete]
    B9 --> B10[User confirms → stage: selecting_output]
    B10 --> B11[User selects Google Tasks]
    B11 --> B12{Google credentials exist?}
    B12 -- No --> B13[Show 'Connect Google' button]
    B13 --> B14[OAuth popup opens]
    B14 --> B15{User authorizes?}
    B15 -- Yes --> B16[Credentials stored in session]
    B15 -- Denied/Closed --> B17[OAuth fails — user retries or switches output]
    B16 --> B18[Agent runs with credentials]
    B18 --> B19([Google Tasks list URL returned in chat])
```

---

## Scenario C: Mixed — Some AI-Generated, Some User-Provided

```mermaid
flowchart TD
    C1([User describes event with some specific dishes]) --> C2[AI builds partial menu]
    C2 --> C3[User: 'I have my own tiramisu recipe']
    C3 --> C4[Extraction: ADD 'Tiramisu'<br/>awaiting_user_input = true]
    C4 --> C5[BLOCKING: AI focuses exclusively on<br/>collecting this promised recipe]
    C5 --> C6{How does user provide it?}
    C6 -- Pastes URL --> C7[extract_recipe_from_url]
    C6 -- Uploads file --> C8[RecipeUploadPanel → upload-recipe endpoint]
    C6 -- Types description --> C9[Gemini extracts from text]
    C7 & C8 & C9 --> C10[awaiting_user_input = false, status = COMPLETE]
    C10 --> C11[AI resumes normal gathering for remaining dishes]
    C11 --> C12[All dishes complete → is_complete = true]
    C12 --> C13[stage: recipe_confirmation<br/>User recipe already complete, AI auto-generates for the rest]
    C13 --> C14[User confirms → stage: selecting_output]
    C14 --> C15([Standard output flow])
```

---

## Scenario D: User Modifies Menu Mid-Gathering

```mermaid
flowchart TD
    D1([Menu proposed, user considering]) --> D2[User: 'Actually, swap the risotto for gnocchi']
    D2 --> D3[Extraction: REMOVE 'Risotto', ADD 'Gnocchi']
    D3 --> D4[meal_plan.confirmed = false<br/>answered_questions.meal_plan = false]
    D4 --> D5[AI presents updated menu]
    D5 --> D6[User: 'Also add a soup course']
    D6 --> D7[Extraction: ADD 'Soup' placeholder]
    D7 --> D8[AI asks: 'Which soup did you have in mind?']
    D8 --> D9[User specifies → UPDATE 'soup' → 'French Onion Soup']
    D9 --> D10[User confirms updated menu → is_complete = true]
    D10 --> D11[stage: recipe_confirmation]
    D11 --> D12([Standard recipe confirmation flow])
```

---

## Scenario E: Modifications During Recipe Confirmation

```mermaid
flowchart TD
    E1([Stage: recipe_confirmation]) --> E2[AI presents auto-generated ingredients per dish]
    E2 --> E3[User: 'I don't like that pasta recipe, use this one instead']
    E3 --> E4[User pastes URL → extract_recipe_from_url]
    E4 --> E5{Extraction success?}
    E5 -- Yes --> E6[recipe.source_type = USER_URL, ingredients replaced]
    E5 -- No --> E7[Error surfaced by AI — user retries]
    E6 --> E8[User: 'Also remove the dessert']
    E8 --> E9[Extraction: REMOVE 'Tiramisu'<br/>meal_plan.confirmed = false]
    E9 --> E10[AI confirms updated menu, user re-confirms]
    E10 --> E11[meal_plan.confirmed = true → stage: selecting_output]
    E11 --> E12([Standard output flow])
```

---

## Scenario F: User Wants Multiple Outputs

```mermaid
flowchart TD
    F1([Stage: selecting_output]) --> F2[User: 'In-chat list AND Google Tasks']
    F2 --> F3[Extraction: output_formats = IN_CHAT + GOOGLE_TASKS]
    F3 --> F4{Google credentials?}
    F4 -- No --> F5[OAuth flow → credentials stored]
    F5 --> F6[stage: agent_running]
    F4 -- Yes --> F6
    F6 --> F7[Agent delivery in parallel:<br/>- format_chat_output<br/>- create_google_tasks]
    F7 --> F8([Chat: formatted markdown list<br/>Plus: Google Tasks URL])
```

---

## Scenario G: Shopping List Corrections in Review Loop

```mermaid
flowchart TD
    G1([Agent: review loop — shopping list presented]) --> G2{User action}
    G2 -- Approve / empty message --> G3[Filter excluded items → deliver]
    G2 -- Check 'already have' boxes --> G4[excluded_items set grows]
    G4 --> G2
    G2 -- Text correction message --> G5[apply_shopping_list_corrections via Gemini]
    G5 --> G6[Revised shopping list re-presented]
    G6 --> G2
    G3 --> G7([Deliver with exclusions applied])
```

---

## Scenario H: Dietary Restrictions

```mermaid
flowchart TD
    H1([User: '2 guests are vegan']) --> H2[dietary field populated]
    H2 --> H3[AI avoids non-vegan dishes in suggestions]
    H3 --> H4[Agent get_dish_ingredients:<br/>passes dietary_restrictions to Gemini]
    H4 --> H5[Gemini: 'Do NOT include ingredients violating restrictions']
    H5 --> H6[Ingredients generated without violating items]
    H6 --> H7[User-provided recipes NOT auto-modified]
    H7 --> H8[User responsible for verifying own recipes comply]
    H8 --> H9([Shopping list delivered])
```

---

## Scenario I: Re-run Agent with Cached Shopping List

```mermaid
flowchart TD
    I1([Agent completed — in-chat output delivered]) --> I2[User: 'Also send to Google Tasks']
    I2 --> I3{existing_state.shopping_list present?}
    I3 -- Yes --> I4[Skip calculate + ingredients + aggregate steps]
    I4 --> I5[Deliver directly with cached shopping list]
    I5 --> I6([Google Tasks URL returned])
    I3 -- No --> I7[Re-run full agent from step 1]
```

---

## Scenario J: Store-Bought Items Mixed with Homemade

```mermaid
flowchart TD
    J1([Meal plan includes: homemade pasta, store-bought hummus, wine]) --> J2[Agent step: calculate quantities for all]
    J2 --> J3[get_all_dish_ingredients routing per dish]
    J3 --> J4{Dish type?}
    J4 -- Homemade/named --> J5[Python scaling of extracted ingredients]
    J4 -- Store-bought --> J6[Single COUNT entry — no AI call<br/>e.g., Hummus: 1 count]
    J4 -- Beverage --> J7[Gemini scaling — special beverage prompt]
    J5 & J6 & J7 --> J8[Aggregate all items<br/>fuzzy deduplication across dishes]
    J8 --> J9([Shopping list: scaled homemade items +<br/>beverage quantities + 1x store-bought items])
```

---

## Error Cases

```mermaid
flowchart TD
    ERR1([URL extraction fails]) --> ERR1A{HTTP status}
    ERR1A -- 403 Forbidden --> ERR1B[AI: 'That page blocked access']
    ERR1A -- 404 Not Found --> ERR1C[AI: 'URL not found']
    ERR1A -- Network/Timeout --> ERR1D[AI: 'Network error or timeout']
    ERR1A -- Page exists, no recipe --> ERR1E[AI: 'No recipe found on that page']
    ERR1B & ERR1C & ERR1D & ERR1E --> ERR1F[User provides different URL or switches to description]

    ERR2([File upload fails]) --> ERR2A{MIME type supported?}
    ERR2A -- Unsupported --> ERR2B[400: Supported formats: PDF, TXT, JPEG, PNG, WebP]
    ERR2B --> ERR2C[User uploads different format]

    ERR3([Extraction JSON invalid / empty]) --> ERR3A[Log warning<br/>Return empty ExtractionResult<br/>No state change — conversation continues normally]

    ERR4([Agent step fails]) --> ERR4A[asyncio.gather with return_exceptions=True]
    ERR4A -- Non-fatal --> ERR4B[Log error, skip failed delivery task<br/>Other outputs still delivered]
    ERR4A -- Fatal exception --> ERR4C[state.stage = ERROR<br/>Send agent_error WebSocket message to frontend]

    ERR5([Google OAuth — user denies or closes popup]) --> ERR5A[Popup closes without postMessage<br/>isGoogleConnected stays false]
    ERR5A --> ERR5B[User must retry Connect Google or switch to in-chat output]

    ERR6([AI service unavailable]) --> ERR6A[GOOGLE_API_KEY missing at startup<br/>ai_service = None]
    ERR6A --> ERR6B[All chat endpoints return 503<br/>/health: ai_service_ready = false]

    ERR7([WebSocket message processing error]) --> ERR7A[Exception caught in handler<br/>Send type:error message to frontend]
    ERR7A --> ERR7B[Connection stays open — user can retry]
```

---

## Key Mechanics

| Mechanic | Trigger | Effect |
|---|---|---|
| `awaiting_user_input = true` | User promises a recipe | AI blocks on collecting it before anything else |
| `meal_plan.confirmed = false` | ADD or REMOVE action | User must re-confirm updated menu |
| `auto_generate_recipes` | Entering recipe_confirmation | AI generates ingredient lists for homemade dishes not yet provided by user |
| Confirmation reset | Any ADD/REMOVE during recipe_confirmation | `meal_plan.confirmed = false`, user must re-confirm |
| Completion score | Each extraction | 35% non-meal questions + 65% meal plan quality |
| Output format guard | `selecting_output` stage only | AI cannot extract output formats during earlier stages |
| Cached shopping list | `existing_state.shopping_list` present | Skip recalculation, jump to delivery |
