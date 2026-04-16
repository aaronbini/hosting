<wizard-report>
# PostHog post-wizard report

The wizard has completed a deep integration of PostHog analytics into the Hosting Helper frontend. A new `src/posthog.ts` module initializes the `posthog-node` SDK using environment variables. User identification is called on authentication, and 11 events are captured across the full planning lifecycle — from sign-in and session creation through menu confirmation, recipe handling, output format selection, shopping list approval and generation, and plan management.

| Event | Description | File |
|---|---|---|
| `user signed in` | Fired when a user authenticates via Google OAuth | `src/App.tsx` |
| `session created` | Fired when a new event-planning session is created | `src/App.tsx` |
| `session restored` | Fired when an existing in-progress session is resumed | `src/App.tsx` |
| `message sent` | Fired when the user sends a chat message | `src/hooks/useChat.ts` |
| `menu confirmed` | Fired when the user confirms the suggested menu | `src/hooks/useChat.ts` |
| `recipes confirmed` | Fired when the user confirms all recipe sources | `src/hooks/useChat.ts` |
| `output format selected` | Fired when the user picks output format(s) | `src/hooks/useChat.ts` |
| `shopping list approved` | Fired when the user approves the shopping list review | `src/hooks/useChat.ts` |
| `shopping list generated` | Fired when the AI agent delivers the final shopping list | `src/hooks/useChat.ts` |
| `plan viewed` | Fired when the user expands a saved plan | `src/components/PlansView.tsx` |
| `plan deleted` | Fired when the user deletes a saved plan | `src/components/PlansView.tsx` |

## Next steps

We've built some insights and a dashboard for you to keep an eye on user behavior, based on the events we just instrumented:

- **Dashboard — Analytics basics**: https://us.posthog.com/project/374228/dashboard/1444629
  - **Planning Funnel** (sign-in → message → menu confirmed → list approved → list generated): https://us.posthog.com/project/374228/insights/16pOaHuh
  - **Daily Active Users & New Sessions**: https://us.posthog.com/project/374228/insights/5DtmGPGi
  - **Output Format Preferences** (pie chart breakdown): https://us.posthog.com/project/374228/insights/62vs130q
  - **Plan Engagement (Viewed vs Deleted)**: https://us.posthog.com/project/374228/insights/iYqjSz35
  - **Session Completion Rate** (session created → menu confirmed → list delivered): https://us.posthog.com/project/374228/insights/dKUaHJ5b

### Agent skill

We've left an agent skill folder in your project. You can use this context for further agent development when using Claude Code. This will help ensure the model provides the most up-to-date approaches for integrating PostHog.

</wizard-report>
