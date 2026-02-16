# Food Event Planning Assistant - Architecture

## Core Components

**1. Conversational Frontend**
- Web interface or mobile app with a chat UI
- Could use existing chat frameworks (React Chat UI, Chatbot UI libraries)
- Progressive disclosure of questions based on previous answers
- Adaptable to different event types and hosting scenarios

**2. Conversation Engine**
- State machine or decision tree to manage the question flow
- LLM integration (OpenAI, Anthropic, etc.) for natural language understanding and generation
- Maintain conversation context/session data
- Logic to determine when enough info is gathered
- Dynamic question routing based on event type (indoor/outdoor, formal/casual, dietary needs, etc.)

**3. Planning Logic/Calculation Engine**
- Rules-based system or ML model for food quantity calculations
- Database of per-person serving sizes (adults vs children, meal type, event formality)
- Adjustments for dietary restrictions (vegetarian options, allergies, religious/cultural requirements)
- Event-specific factors: indoor/outdoor, meal duration, formal/casual, cuisine preferences, alcohol considerations

**4. Google Sheets Integration**
- Google Sheets API for programmatic creation
- Template-based sheet generation (shopping list, timeline, budget)
- Shareable link generation with appropriate permissions

## Data Flow

1. User answers questions → Session state updated
2. Engine validates completeness → Triggers calculation
3. Calculation engine processes requirements → Generates recommendations
4. Sheet builder formats data → Creates/populates Google Sheet via API
5. Return shareable link + chat summary to user

## Key Technical Decisions

**Backend Options:**
- Node.js/Express or Python/Flask for API layer
- Firebase/Supabase for real-time data + auth
- Serverless functions (AWS Lambda, Vercel) for cost efficiency

**Conversation Strategy:**
- Hybrid approach: LLM for natural language + structured prompts for critical data points
- Validation layer to ensure numerical inputs (guest counts) are captured accurately

**Data Storage:**
- Session/conversation history (Redis, MongoDB)
- Recipe/serving size database (PostgreSQL, Firestore)
- User accounts if you want save/revisit functionality

## Features to Consider

- Menu suggestion engine based on event type, season, region, dietary mix, formality level
- Budget estimation with flexible cost ranges
- Timeline/prep schedule generation (adaptable for different event durations)
- Recipe links or instructions with difficulty ratings
- Venue-aware recommendations (indoor kitchen constraints, outdoor equipment needs)
- Serving style suggestions (plated, buffet, family-style, cocktail)
- Alcohol and beverage pairing suggestions
- Table setting and décor tips based on formality
- Guest accommodation planning (seating, accessibility, parking)
- Contingency planning (weather backup, dietary emergencies)

---

## Google Sheets Integration - Detailed Design

### Setup & Authentication

**Service Account Approach (Recommended for MVP):**
1. Create a Google Cloud Project
2. Enable Google Sheets API and Google Drive API
3. Create a Service Account with credentials (JSON key file)
4. Use service account to create sheets programmatically
5. Share sheets with user's email address via Drive API

**OAuth 2.0 Approach (Better for user-owned sheets):**
1. User authorizes your app to access their Google Drive
2. Your app creates sheets directly in their Drive
3. Better for privacy and long-term ownership
4. More complex to implement but better UX

### Sheet Generation Process

**Step 1: Data Preparation**
```
Input from conversation engine:
{
  "eventType": "dinner-party",
  "guests": {"adults": 20, "children": 5},
  "dietaryRestrictions": ["vegetarian: 3", "gluten-free: 2", "kosher: 1"],
  "menuPreferences": ["Italian", "seafood-friendly"],
  "duration": "3 hours",
  "mealType": "dinner",
  "venue": "home-dining",
  "formality": "semi-formal",
  "budget": 500,
  "servingStyle": "family-style"
}
```

**Step 2: Calculation Layer**
- Apply serving size formulas adjusted for event type and meal duration
- Factor in dietary restrictions with culturally appropriate alternatives
- Calculate course-based quantities (appetizers, entree, sides, dessert, coffee/tea)
- Include beverages with alcohol/non-alcohol balance
- Calculate equipment needs (serving dishes, utensils, glassware)
- Factor in preparation complexity and kitchen equipment constraints
- Add buffer quantities based on event type and guest behavior patterns

**Step 3: Sheet Structure**

Create a workbook with multiple tabs:

**Tab 1: Shopping List**
```
| Category | Item | Quantity | Unit | Estimated Cost | Store/Section | Priority | Dietary Note |
|----------|------|----------|------|----------------|---------------|----------|---------------|
| Proteins | Salmon Fillets | 6 | lbs | $72 | Fish Counter | Must Have | Gluten-free |
| Proteins | Vegetable Skewers | 8 | skewers | $18 | Produce | Must Have | Vegetarian |
| Sides | Risotto Rice | 3 | lbs | $12 | Dry Goods | Must Have | Kosher |
| Beverages | Wine (white) | 2 | bottles | $40 | Wine | Recommended | |
```

**Tab 2: Prep Timeline**
```
| When | Task | Duration | Category | Notes |
|------|------|----------|----------|-------|
| 1 week before | Finalize menu and guest count | 1 hour | Planning | Confirm dietary restrictions |
| 3 days before | Shop for non-perishables | 2 hours | Shopping | Pantry staples, wine, decorations |
| 1 day before | Shop for perishables | 1.5 hours | Shopping | Proteins, produce, dairy |
| 1 day before | Prep vegetables and make sauces | 3 hours | Prep | Can be made ahead and refrigerated |
| Day of (4 hrs before) | Set table and arrange flowers | 1.5 hours | Setup | Final touches to atmosphere |
| Day of (2 hrs before) | Final prep and plating setup | 1.5 hours | Cooking | Bring ingredients to room temperature |
| Day of (1 hr before) | Cook main course | 1 hour | Cooking | Heat oven/stovetop as needed |
```

**Tab 3: Serving Guide**
```
| Item | Course | Total Amount | Per Person | Notes |
|------|--------|--------------|-----------|-------|
| Salmon | Main | 6 lbs | 0.24 lbs | Allow 6oz per adult |
| Risotto | Main | 3 lbs dry | 0.1 lbs dry | Yields ~2 cups cooked |
| Salad | Starter | 4 lbs greens | 0.16 lbs | Generous side serving |
| Dessert | Dessert | 20 pieces | 1.3 per person | 30% may skip dessert |
```

**Tab 4: Budget Summary**
```
| Category | Subtotal | Per Guest | Percentage |
|----------|----------|-----------|------------|
| Proteins | $90 | $3.53 | 30% |
| Produce & Sides | $120 | $4.70 | 40% |
| Beverages | $60 | $2.35 | 20% |
| Supplies & Décor | $30 | $1.18 | 10% |
| **Total** | **$300** | **$11.76** | **100%** |
```

### API Implementation Details

**Using googleapis Node.js client:**
```javascript
// Pseudocode structure
async function generateCookoutSheet(planData) {
  // 1. Create new spreadsheet
  const sheet = await sheets.spreadsheets.create({
    properties: { title: `Cookout Plan - ${date}` }
  });
  
  // 2. Add multiple sheets/tabs
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: sheet.spreadsheetId,
    requests: [
      { addSheet: { properties: { title: 'Shopping List' }}},
      { addSheet: { properties: { title: 'Timeline' }}},
      { addSheet: { properties: { title: 'Serving Guide' }}},
      { addSheet: { properties: { title: 'Budget' }}}
    ]
  });
  
  // 3. Populate with data
  await sheets.spreadsheets.values.batchUpdate({
    spreadsheetId: sheet.spreadsheetId,
    resource: {
      data: [
        { range: 'Shopping List!A1:G100', values: shoppingListData },
        { range: 'Timeline!A1:C50', values: timelineData },
        // ... more tabs
      ]
    }
  });
  
  // 4. Apply formatting (colors, bold headers, freeze rows)
  await sheets.spreadsheets.batchUpdate({
    requests: [
      { repeatCell: { /* header formatting */ }},
      { updateSheetProperties: { /* freeze first row */ }}
    ]
  });
  
  // 5. Share with user
  await drive.permissions.create({
    fileId: sheet.spreadsheetId,
    requestBody: {
      type: 'user',
      role: 'writer',
      emailAddress: userEmail
    }
  });
  
  return sheet.spreadsheetUrl;
}
```

### Advanced Features

**Smart Templates:**
- Pre-built templates for different cookout types (BBQ, picnic, tailgate)
- Dynamic formulas in cells for user adjustments
- Conditional formatting (red for "must buy", yellow for "nice to have")

#### Template System Architecture

The template system allows for reusable, customizable cookout configurations that can be selected based on the conversation context.

**Template Data Structure:**
```javascript
// Template definition stored in database
const eventTemplates = {
  "classic-bbq": {
    name: "Classic Backyard BBQ",
    description: "Traditional grilled meats with classic sides",
    eventType: "outdoor-casual",
    idealFor: ["summer", "backyard", "casual", "families"],
    guestRange: { min: 10, max: 100 },
    formality: "casual",
    venue: "outdoor-backyard",
    servingStyle: "buffet",
    equipmentNeeded: ["grill", "coolers", "serving utensils", "tongs", "charcoal"],
    
    menuCategories: {
      proteins: {
        required: true,
        items: [
          {
            name: "Hamburgers",
            servingSize: { adult: 1.5, child: 1, unit: "patties" },
            costPerUnit: 2.50,
            prepTime: 15,
            cookTime: 10,
            alternatives: ["turkey-burgers", "veggie-burgers"]
          },
          {
            name: "Hot Dogs",
            servingSize: { adult: 2, child: 1.5, unit: "links" },
            costPerUnit: 1.25,
            prepTime: 5,
            cookTime: 8
          },
          {
            name: "BBQ Chicken",
            servingSize: { adult: 0.75, child: 0.5, unit: "lbs" },
            costPerUnit: 4.99,
            prepTime: 30,
            cookTime: 45,
            optional: true
          }
        ]
      },
      
      sides: {
        required: true,
        selectMin: 3,
        items: [
          {
            name: "Potato Salad",
            servingSize: { perPerson: 0.25, unit: "lbs" },
            costPerUnit: 3.00,
            makeAhead: true,
            prepTime: 45
          },
          {
            name: "Coleslaw",
            servingSize: { perPerson: 0.2, unit: "lbs" },
            costPerUnit: 2.50,
            makeAhead: true
          },
          {
            name: "Baked Beans",
            servingSize: { perPerson: 0.3, unit: "lbs" },
            costPerUnit: 2.00,
            prepTime: 20
          },
          {
            name: "Corn on the Cob",
            servingSize: { adult: 1.5, child: 1, unit: "ears" },
            costPerUnit: 0.75,
            seasonal: ["summer"],
            cookTime: 20
          }
        ]
      },
      
      beverages: {
        required: true,
        items: [
          {
            name: "Water Bottles",
            servingSize: { perPerson: 2, unit: "bottles" },
            costPerUnit: 0.50
          },
          {
            name: "Soda (12oz cans)",
            servingSize: { adult: 2, child: 1.5, unit: "cans" },
            costPerUnit: 0.75
          },
          {
            name: "Lemonade",
            servingSize: { perPerson: 2, unit: "cups" },
            costPerUnit: 0.30
          }
        ]
      },
      
      supplies: {
        required: true,
        items: [
          {
            name: "Paper Plates",
            servingSize: { perPerson: 2, unit: "plates" },
            costPerUnit: 0.15
          },
          {
            name: "Napkins",
            servingSize: { perPerson: 3, unit: "napkins" },
            costPerUnit: 0.05
          },
          {
            name: "Plastic Utensils",
            servingSize: { perPerson: 1, unit: "sets" },
            costPerUnit: 0.20
          }
        ]
      }
    },
    
    condiments: {
      items: ["ketchup", "mustard", "mayo", "relish", "bbq-sauce", "hot-sauce", "pickles", "onions", "lettuce", "tomatoes"],
      servingSize: { bulk: true }
    },
    
    timeline: [
      { daysBeforeEvent: 7, task: "Finalize guest count", category: "planning" },
      { daysBeforeEvent: 5, task: "Create shopping list", category: "planning" },
      { daysBeforeEvent: 3, task: "Shop for non-perishables", category: "shopping" },
      { daysBeforeEvent: 2, task: "Prepare marinades", category: "prep" },
      { daysBeforeEvent: 1, task: "Shop for perishables", category: "shopping" },
      { daysBeforeEvent: 1, task: "Prepare potato salad and coleslaw", category: "prep" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 4, task: "Set up tables and chairs", category: "setup" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 2, task: "Start grill", category: "cooking" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 1.5, task: "Begin grilling proteins", category: "cooking" }
    ],
    
    tips: [
      "Keep meats cold until ready to grill",
      "Have a meat thermometer handy (165°F for poultry, 160°F for ground beef)",
      "Set up separate serving areas for regular and vegetarian items",
      "Keep drinks on ice to avoid frequent refills",
      "Have a tent or covered area ready in case of rain",
      "Plan for bug spray and sunscreen availability"
    ]
  },
  
  "taco-bar": {
    name: "Interactive Taco Bar",
    description: "Build-your-own taco station with all the fixings",
    eventType: "interactive-casual",
    idealFor: ["casual", "interactive", "all-seasons", "offices"],
    guestRange: { min: 8, max: 80 },
    formality: "casual",
    venue: "flexible",
    servingStyle: "interactive-station",
    
    menuCategories: {
      proteins: {
        required: true,
        selectMin: 2,
        items: [
          {
            name: "Seasoned Ground Beef",
            servingSize: { adult: 0.4, child: 0.25, unit: "lbs" },
            costPerUnit: 5.99,
            prepTime: 10,
            cookTime: 15
          },
          {
            name: "Shredded Chicken",
            servingSize: { adult: 0.35, child: 0.2, unit: "lbs" },
            costPerUnit: 4.99,
            prepTime: 15,
            cookTime: 30
          },
          {
            name: "Black Beans (vegetarian)",
            servingSize: { perPerson: 0.3, unit: "lbs" },
            costPerUnit: 2.00,
            prepTime: 5,
            cookTime: 10
          }
        ]
      },
      
      toppings: {
        required: true,
        items: [
          { name: "Shredded Lettuce", servingSize: { perPerson: 0.15, unit: "lbs" }},
          { name: "Diced Tomatoes", servingSize: { perPerson: 0.15, unit: "lbs" }},
          { name: "Shredded Cheese", servingSize: { perPerson: 0.1, unit: "lbs" }},
          { name: "Sour Cream", servingSize: { perPerson: 2, unit: "tbsp" }},
          { name: "Salsa", servingSize: { perPerson: 3, unit: "tbsp" }},
          { name: "Guacamole", servingSize: { perPerson: 2, unit: "tbsp" }},
          { name: "Jalapeños", servingSize: { perPerson: 0.5, unit: "oz" }}
        ]
      },
      
      shells: {
        required: true,
        items: [
          { name: "Hard Taco Shells", servingSize: { adult: 3, child: 2, unit: "shells" }},
          { name: "Soft Flour Tortillas", servingSize: { adult: 3, child: 2, unit: "tortillas" }}
        ]
      }
    },
    
    timeline: [
      { daysBeforeEvent: 2, task: "Marinate chicken", category: "prep" },
      { daysBeforeEvent: 1, task: "Shop for ingredients", category: "shopping" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 3, task: "Prep all toppings", category: "prep" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 1.5, task: "Cook proteins", category: "cooking" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 0.5, task: "Set up taco bar station", category: "setup" }
    ]
  },
  
  "picnic-style": {
    name: "Picnic Style",
    description: "No-cook or minimal-cook items perfect for outdoor gatherings",
    eventType: "portable-outdoor",
    idealFor: ["park", "beach", "portable", "no-grill", "school-events"],
    guestRange: { min: 5, max: 40 },
    formality: "casual",
    venue: "outdoor-portable",
    servingStyle: "individual-boxes",
    equipmentNeeded: ["coolers", "baskets", "blankets"],
    
    menuCategories: {
      mains: {
        required: true,
        items: [
          { name: "Sub Sandwiches", servingSize: { adult: 1, child: 0.5, unit: "sandwiches" }},
          { name: "Fried Chicken (store-bought)", servingSize: { adult: 3, child: 2, unit: "pieces" }},
          { name: "Pasta Salad", servingSize: { perPerson: 0.5, unit: "lbs" }}
        ]
      }
    }
  },
  
  "dinner-party": {
    name: "Dinner Party",
    description: "Elegant sit-down dinner with multiple courses",
    eventType: "indoor-formal",
    idealFor: ["intimate", "elegant", "all-seasons"],
    guestRange: { min: 4, max: 20 },
    formality: "semi-formal",
    venue: "indoor-home",
    servingStyle: "plated",
    equipmentNeeded: ["fine-china", "silverware", "glassware", "tablecloth"],
    
    menuCategories: {
      appetizers: {
        required: true,
        items: [
          { name: "Cheese & Charcuterie Board", servingSize: { perPerson: 0.3, unit: "lbs" }},
          { name: "Crostini with Toppings", servingSize: { perPerson: 3, unit: "pieces" }}
        ]
      },
      mains: {
        required: true,
        selectMin: 1,
        items: [
          { name: "Filet Mignon", servingSize: { perPerson: 6, unit: "oz" }},
          { name: "Salmon", servingSize: { perPerson: 6, unit: "oz" }},
          { name: "Vegetarian Wellington", servingSize: { perPerson: 1, unit: "serving" }}
        ]
      },
      sides: {
        required: true,
        items: [
          { name: "Roasted Potatoes", servingSize: { perPerson: 4, unit: "oz" }},
          { name: "Seasonal Vegetables", servingSize: { perPerson: 3, unit: "oz" }}
        ]
      },
      dessert: {
        required: true,
        items: [
          { name: "Chocolate Mousse", servingSize: { perPerson: 1, unit: "cup" }}
        ]
      }
    },
    
    timeline: [
      { daysBeforeEvent: 7, task: "Plan menu and create shopping list", category: "planning" },
      { daysBeforeEvent: 3, task: "Shop for ingredients", category: "shopping" },
      { daysBeforeEvent: 1, task: "Set table and prepare serving dishes", category: "setup" },
      { daysBeforeEvent: 1, task: "Prep appetizers and side dishes", category: "prep" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 2, task: "Final plating setup and last-minute prep", category: "cooking" },
      { daysBeforeEvent: 0, hoursBeforeEvent: 1, task: "Cook mains and warm sides", category: "cooking" }
    ]
  },
  
  "wedding-reception": {
    name: "Wedding Reception",
    description: "Formal event with multiple courses and bar service",
    eventType: "formal-celebration",
    idealFor: ["weddings", "large-events", "formal"],
    guestRange: { min: 50, max: 500 },
    formality: "formal",
    venue: "flexible",
    servingStyle: "plated-or-buffet",
    equipmentNeeded: ["catering-equipment", "bar-setup", "decorations"],
    
    menuCategories: {
      cocktailHour: {
        required: false,
        items: [
          { name: "Passed Hors d'oeuvres", servingSize: { perPerson: 4, unit: "pieces" }},
          { name: "Cheese & Fruit Display", servingSize: { perPerson: 0.3, unit: "lbs" }}
        ]
      },
      mains: {
        required: true,
        selectMin: 2,
        items: [
          { name: "Filet & Lobster", servingSize: { perPerson: 1, unit: "serving" }},
          { name: "Pan-Seared Salmon", servingSize: { perPerson: 1, unit: "serving" }},
          { name: "Vegetarian Entrée", servingSize: { perPerson: 1, unit: "serving" }}
        ]
      },
      bar: {
        required: true,
        items: [
          { name: "Premium Wine (red & white)", servingSize: { perPerson: 2, unit: "glasses" }},
          { name: "Beer Selection", servingSize: { perPerson: 1, unit: "bottle" }},
          { name: "Cocktail Service", servingSize: { perPerson: 2, unit: "drinks" }},
          { name: "Non-Alcoholic Beverages", servingSize: { perPerson: 1, unit: "per-drink" }}
        ]
      },
      dessert: {
        required: true,
        items: [
          { name: "Wedding Cake", servingSize: { perPerson: 1, unit: "slice" }},
          { name: "Dessert Bar Items", servingSize: { perPerson: 0.5, unit: "lbs" }}
        ]
      }
    }
  },
  
  "corporate-lunch": {
    name: "Corporate Lunch",
    description: "Lunch for office meeting or team event",
    eventType: "corporate",
    idealFor: ["meetings", "team-building", "corporate"],
    guestRange: { min: 10, max: 200 },
    formality: "business-casual",
    venue: "indoor-office",
    servingStyle: "buffet",
    equipmentNeeded: ["serving-tables", "chafing-dishes"],
    
    menuCategories: {
      mains: {
        required: true,
        items: [
          { name: "Sandwich Platters", servingSize: { perPerson: 1.5, unit: "sandwiches" }},
          { name: "Salad Bar", servingSize: { perPerson: 2, unit: "cups" }}
        ]
      }
    }
  },
  
  "vegetarian-focus": {
    name: "Vegetarian Feast",
    description: "Plant-based menu with creative vegetarian and vegan options",
    eventType: "dietary-focused",
    idealFor: ["vegetarian", "health-conscious", "all-seasons"],
    guestRange: { min: 6, max: 60 },
    formality: "casual-to-formal",
    venue: "flexible",
    servingStyle: "flexible"
  }
};
```

**Template Selection Engine:**
```javascript
// Determines which template to use based on conversation data
class TemplateSelector {
  selectTemplate(conversationData) {
    const {
      eventType,
      guestCount,
      dietaryRestrictions,
      venue,
      season,
      formality,
      budget,
      mealType,
      availableEquipment
    } = conversationData;
    
    // Score each template
    const scores = Object.entries(eventTemplates).map(([key, template]) => {
      let score = 0;
      
      // Event type match
      if (template.eventType && eventType && template.eventType.includes(eventType)) {
        score += 30;
      }
      
      // Guest count compatibility
      if (guestCount >= template.guestRange.min && 
          guestCount <= template.guestRange.max) {
        score += 15;
      }
      
      // Venue/Equipment compatibility
      if (template.venue && venue && template.venue.includes(venue)) {
        score += 20;
      }
      
      // Equipment availability
      if (availableEquipment && template.equipmentNeeded) {
        const hasEquipment = template.equipmentNeeded.every(eq => 
          availableEquipment.includes(eq) || !eq.includes('grill')
        );
        if (hasEquipment) score += 15;
      }
      
      // Formality match
      if (template.formality && formality && template.formality === formality) {
        score += 10;
      }
      
      // Dietary alignment
      if (dietaryRestrictions.includes('vegetarian') && 
          (key === 'vegetarian-focus' || key.includes('vegetarian'))) {
        score += 25;
      }
      
      // Season compatibility
      if (template.idealFor && season && template.idealFor.includes(season)) {
        score += 5;
      }
      
      return { templateKey: key, template, score };
    });
    
    // Return highest scoring template
    return scores.sort((a, b) => b.score - a.score)[0];
  }
  
  // Allow user to override with specific template
  getTemplateByKey(key) {
    return eventTemplates[key];
  }
  
  // List all available templates for user to browse
  listAllTemplates() {
    return Object.entries(eventTemplates).map(([key, t]) => ({{
      id: key,
      name: t.name,
      description: t.description,
      idealFor: t.guestRange
    }));
  }
}
```

**Template Customization Engine:**
```javascript
// Takes a template and user data, generates customized plan
class TemplateCustomizer {
  customize(template, conversationData) {
    const {
      adults,
      children,
      dietaryRestrictions,
      excludedItems,
      budget
    } = conversationData;
    
    const totalGuests = adults + children;
    const customizedPlan = {
      shoppingList: [],
      timeline: [],
      budget: { items: [], total: 0 }
    };
    
    // Process each menu category
    Object.entries(template.menuCategories).forEach(([category, config]) => {
      config.items.forEach(item => {
        // Skip excluded items
        if (excludedItems.includes(item.name)) return;
        
        // Calculate quantities
        const quantity = this.calculateQuantity(
          item,
          adults,
          children,
          dietaryRestrictions
        );
        
        const cost = quantity * item.costPerUnit;
        
        // Add dietary alternatives if needed
        if (this.needsAlternative(item, dietaryRestrictions)) {
          const alt = this.findAlternative(item, template);
          if (alt) {
            customizedPlan.shoppingList.push(this.createShoppingItem(alt, quantity * 0.3));
          }
        }
        
        customizedPlan.shoppingList.push({
          category,
          item: item.name,
          quantity,
          unit: item.servingSize.unit,
          cost,
          priority: item.optional ? "Nice to Have" : "Must Have",
          prepTime: item.prepTime || 0,
          cookTime: item.cookTime || 0
        });
        
        customizedPlan.budget.total += cost;
      });
    });
    
    // Customize timeline based on event date
    customizedPlan.timeline = template.timeline.map(task => ({
      ...task,
      date: this.calculateTaskDate(conversationData.eventDate, task),
      completed: false
    }));
    
    // Add budget warnings
    if (budget && customizedPlan.budget.total > budget) {
      customizedPlan.budgetWarning = {
        message: `Plan exceeds budget by $${customizedPlan.budget.total - budget}`,
        suggestions: this.getCostSavingTips(customizedPlan, budget)
      };
    }
    
    return customizedPlan;
  }
  
  calculateQuantity(item, adults, children, dietaryRestrictions) {
    let qty = 0;
    
    if (item.servingSize.adult !== undefined) {
      qty += adults * item.servingSize.adult;
      qty += children * (item.servingSize.child || item.servingSize.adult * 0.6);
    } else if (item.servingSize.perPerson !== undefined) {
      qty += (adults + children) * item.servingSize.perPerson;
    }
    
    // Add buffer (typically 10-15% extra)
    qty *= 1.15;
    
    // Round up to reasonable purchasing units
    return Math.ceil(qty);
  }
  
  needsAlternative(item, restrictions) {
    // Check if item conflicts with dietary restrictions
    if (restrictions.includes('vegetarian') && 
        ['beef', 'chicken', 'pork'].some(meat => item.name.toLowerCase().includes(meat))) {
      return true;
    }
    return false;
  }
  
  getCostSavingTips(plan, budget) {
    return [
      "Consider buying store-brand items instead of name brands",
      "Remove optional items or reduce portions by 10%",
      "Shop at wholesale clubs for bulk discounts",
      "Make more items from scratch instead of pre-made"
    ];
  }
}
```

**Sheet Generation with Templates:**
```javascript
// Generates Google Sheet using the customized template plan
async function generateSheetFromTemplate(customizedPlan, templateName, eventDate, userEmail) {
  const sheet = await sheets.spreadsheets.create({
    properties: { 
      title: `${templateName} - Event Plan - ${eventDate}` 
    }
  });
  
  // Create tabs (customize based on event type)
  const sheetTabs = [
    'Shopping List',
    'Timeline', 
    'Serving Guide',
    'Budget'
  ];
  
  // Add event-specific tabs
  if (customizedPlan.bar) sheetTabs.push('Bar & Beverages');
  if (customizedPlan.setup) sheetTabs.push('Venue Setup');
  if (customizedPlan.desserts) sheetTabs.push('Dessert & Coffee');
  
  sheetTabs.push('Tips & Notes');
  
  await createSheetTabs(sheet.spreadsheetId, sheetTabs);
  
  // Populate shopping list with formatted data
  const shoppingData = [
    ['Category', 'Item', 'Quantity', 'Unit', 'Est. Cost', 'Priority', 'Purchased?'],
    ...customizedPlan.shoppingList.map(item => [
      item.category,
      item.item,
      item.quantity,
      item.unit,
      `$${item.cost.toFixed(2)}`,
      item.priority,
      '☐' // Checkbox
    ])
  ];
  
  await populateSheet(sheet.spreadsheetId, 'Shopping List!A1', shoppingData);
  
  // Apply conditional formatting for priorities
  await applyConditionalFormatting(sheet.spreadsheetId, 'Shopping List', {
    mustHave: { bgColor: '#ffebee', textColor: '#c62828' },
    niceToHave: { bgColor: '#fff9c4', textColor: '#f57f17' }
  });
  
  // Add formulas for totals
  await addFormulas(sheet.spreadsheetId, 'Budget', {
    totalFormula: '=SUM(E2:E100)',
    categorySubtotals: 'SUMIF'
  });
  
  return sheet.spreadsheetUrl;
}
```

**Template Storage Options:**

1. **JSON Files in Repository:**
   - Fast iteration during development
   - Version controlled
   - Requires deployment for updates

2. **Database (MongoDB/PostgreSQL):**
   - Dynamic updates without deployment
   - User-created custom templates
   - Query by filters (guest count, dietary needs)

3. **Hybrid Approach:**
   - Core templates in code/JSON
   - User customizations in database
   - Template "inheritance" - users fork base templates

**Real-time Collaboration:**
- Multiple users can edit the same planning sheet
- Comments for discussing menu changes
- Checkboxes for marking purchased items

**Data Validation:**
- Dropdown lists for stores/brands
- Data validation on quantity fields
- Protected ranges for formulas

**Integration with Other Services:**
- Link to recipe websites
- Integration with grocery delivery APIs (Instacart, Amazon Fresh)
- Weather API for day-of recommendations

### Error Handling

- Handle API rate limits (batch operations, retry logic)
- Fallback if user's email is invalid (create sheet and provide link)
- Graceful degradation if Drive API fails (still provide data as JSON/PDF)

### Performance Considerations

- Use batch operations instead of individual cell updates (50x faster)
- Cache template structures to avoid recreating layouts
- Consider generating sheet asynchronously for large events (100+ guests)
- Pre-warm service account credentials

### Privacy & Security

- Don't store copies of generated sheets on your servers
- Use short-lived access tokens
- Option to create "view only" links for guests
- Clear documentation on data retention policies
