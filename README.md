# Food Event Planning Assistant

A full-stack AI-powered assistant for planning food events of any type. Built with FastAPI + Python backend and Vite + React frontend.

## Project Structure

```
hosting/
├── ARCHITECTURE.md          # Detailed architecture documentation
├── backend/                 # Python FastAPI backend
│   ├── pyproject.toml       # Python dependencies (Poetry/PEP 621)
│   └── app/
│       ├── main.py          # FastAPI application
│       ├── agent/           # Agent pipeline (steps + runner)
│       ├── models/          # Pydantic models (event, chat, shopping)
│       └── services/        # AI service + quantity engine + sessions
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

### Conversation Engine
- **Natural language processing** using Google Gemini
- **Session-based conversations** with in-memory storage (TODO: persistent datastore)
- **WebSocket support** for real-time chat with REST fallback
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
- [ ] Persistent session storage (Redis/PostgreSQL)
- [ ] Session expiration and cleanup
- [ ] BYOK (Bring Your Own Key) support for multiple AI providers
- [ ] Streaming responses from Gemini
- [ ] Better NLP for data extraction (spaCy/Transformer-based)
- [ ] Event-type-specific completion logic
- [ ] Error handling and retry logic for API failures
- [ ] Rate limiting and authentication
- [ ] Tests and integration tests

### Frontend
- [ ] WebSocket reconnection logic
- [ ] Message queuing for offline support
- [ ] Connection status indicators
- [ ] Copy/export functionality for event data
- [ ] Settings/preferences panel
- [ ] Multi-language support
- [ ] Dark mode
- [ ] Tests and E2E tests

### Architecture
- [ ] Implement template-based sheet generation for Google Sheets
- [ ] Integrate with calculation engine for food quantities
- [ ] Add event timeline and prep schedule generation
- [ ] Budget breakdown and cost analysis

## API Documentation

### REST Endpoints

#### Create Session
```
POST /api/sessions
Response: { "session_id": "uuid", "message": "..." }
```

#### Get Session
```
GET /api/sessions/{session_id}
Response: { "session_id": "...", "event_data": {...}, ... }
```

#### Chat
```
POST /api/chat
Body: {
  "session_id": "uuid",
  "message": "user message"
}
Response: {
  "message": "assistant response",
  "completion_score": 0.6,
  "is_complete": false,
  "event_data": {...}
}
```

#### Delete Session
```
DELETE /api/sessions/{session_id}
Response: { "message": "Session deleted" }
```

### WebSocket Endpoint

```
WS /ws/chat/{session_id}

Client sends: { "type": "message", "data": "user message" }
Server responds with streaming and agent messages:
- stream_start / stream_chunk / stream_end
- agent_progress / agent_review / agent_complete / agent_error
```

## Development Notes

- Backend uses in-memory session storage for development. Conversations are lost on server restart.
- WebSocket connections work on localhost; use HTTPS/WSS for production.
- CORS is currently open (`*`) - configure properly for production.
- All major TODOs are marked with `# TODO:` comments in the code.

## License

MIT
