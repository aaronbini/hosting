# Food Event Planning Assistant

A full-stack AI-powered assistant for planning food events of any type. Built with FastAPI + Python backend and Vite + React frontend.

## Project Structure

```
hosting/
├── ARCHITECTURE.md          # Detailed architecture documentation
├── backend/                 # Python FastAPI backend
│   ├── pyproject.toml       # Python dependencies (uv/PEP 621)
│   └── app/
│       ├── main.py          # FastAPI application + all routes
│       ├── agent/           # Agent pipeline (steps + runner)
│       ├── auth/            # Google OAuth login + JWT helpers
│       ├── db/              # SQLAlchemy models + async engine
│       ├── models/          # Pydantic models (event, chat, shopping)
│       └── services/        # AI, quantity engine, session managers
└── frontend/                # Vite + React frontend
  ├── index.html
  ├── package.json
  ├── vite.config.ts
  └── src/
    ├── components/      # Chat UI + panels
    ├── hooks/           # useChat WebSocket logic
    ├── App.tsx
    ├── main.tsx
    └── styles.css
```

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 18+
- Google API Key for Gemini

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
uv venv
source .venv/bin/activate
```

3. Install dependencies with uv:

```bash
uv sync
```

4. Set your env vars, for example:
```bash
export GOOGLE_API_KEY=your_key_here
```
4.a. Or set them in a .env file at the repo root

5. Run the server:
```bash
python -m uvicorn app.main:app --reload
```

The backend will be available at `http://localhost:8000`

### Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Run the development server:
```bash
npm run dev
```

The frontend will be available at `http://localhost:5173`

## Features

### Authentication
- **Google OAuth 2.0** login — users sign in with their Google account
- **JWT session cookies** for stateless auth after login
- Sessions and plans are scoped to the authenticated user

### Conversation Engine
- **Natural language processing** using Google Gemini
- **Persistent sessions** stored in PostgreSQL (survive server restarts)
- **WebSocket support** for real-time streaming chat with REST fallback
- **Automatic data extraction** from user messages
- **Agent pipeline** that builds a shopping list after menu/recipe confirmation

### Event Data Tracking
- Pydantic models for type-safe event data
- Automatic completion scoring (0-1.0)
- Computed fields (total_guests, budget_per_person)
- Support for event details:
  - Guest counts (adults/children)
  - Event type, date, and duration
  - Venue and formality level
  - Meal type and cuisine preferences
  - Dietary restrictions
  - Budget and available equipment

### Frontend UI
- **Tailwind CSS** for styling
- **Lucide React** for icons
- **WebSocket chat** with fallback to REST
- **Markdown rendering** for assistant responses
- **Real-time event data sidebar** showing extracted information
- **Completion progress indicator**
- Mobile-responsive design

## TODO Items

### Backend
- [ ] Session expiration and cleanup
- [ ] Google Sheets / Google Tasks: surface connection UI more prominently
- [ ] Async recipe file upload processing
- [ ] RAG integration for recipe quality
- [ ] Event-type-specific completion logic
- [ ] Rate limiting

### Frontend
- [ ] WebSocket reconnection logic
- [ ] Connection status indicators
- [ ] Dark mode
- [ ] Tests and E2E tests

## API Documentation

### Auth Endpoints
```
GET  /api/auth/login          # Redirect to Google OAuth
GET  /api/auth/callback       # OAuth callback, sets JWT cookie
GET  /api/auth/me             # Current user info
POST /api/auth/logout         # Clear session cookie
```

### Session Endpoints
```
POST   /api/sessions                          # Create session
GET    /api/sessions                          # List user's sessions
GET    /api/sessions/{session_id}             # Get session state
DELETE /api/sessions/{session_id}             # Delete session
POST   /api/chat                              # REST chat (non-streaming)
```

### Recipe Endpoints
```
POST /api/sessions/{session_id}/extract-recipe  # Extract recipe from URL
POST /api/sessions/{session_id}/upload-recipe   # Upload recipe file
```

### Google Integration
```
GET /api/auth/google/status    # Check Google Tasks auth status
GET /api/auth/google/start     # Start Google OAuth for Tasks/Sheets
GET /api/auth/google/callback  # Google OAuth callback
```

### Saved Plans
```
GET    /api/plans              # List user's saved plans
GET    /api/plans/{plan_id}    # Get a saved plan
DELETE /api/plans/{plan_id}    # Delete a saved plan
```

### WebSocket
```
WS /ws/chat/{session_id}

Client sends: { "type": "message", "data": "user message" }
Server responds with streaming and agent messages:
- stream_start / stream_chunk / stream_end
- agent_progress / agent_review / agent_complete / agent_error
```

## Development Notes

- Requires a running PostgreSQL instance. Set `DATABASE_URL` in `.env`.
- WebSocket connections work on localhost; use HTTPS/WSS for production.
- CORS origins are configured via `FRONTEND_URL` env var.

## License

MIT
