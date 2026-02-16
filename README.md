# Food Event Planning Assistant

A full-stack AI-powered assistant for planning food events of any type. Built with FastAPI + Python backend and Vite + React frontend.

## Project Structure

```
bbq/
├── ARCHITECTURE.md          # Detailed architecture documentation
├── backend/                 # Python FastAPI backend
│   ├── app/
│   │   ├── main.py         # FastAPI application
│   │   ├── models/
│   │   │   ├── event.py    # Pydantic models for event data
│   │   │   └── chat.py     # Chat message models
│   │   └── services/
│   │       ├── ai_service.py              # Gemini AI integration
│   │       ├── session_manager.py         # Session management
│   │       └── conversation_analyzer.py   # NLP for extracting event data
│   ├── requirements.txt
│   └── .env.example
└── frontend/                # Vite + React frontend
    ├── src/
    │   ├── components/
    │   │   ├── ChatInterface.jsx    # Main chat component
    │   │   ├── ChatMessages.jsx     # Message display
    │   │   ├── ChatInput.jsx        # Input component
    │   │   └── EventDataPanel.jsx   # Sidebar showing extracted data
    │   ├── hooks/
    │   │   └── useChat.js           # Chat state management
    │   ├── App.jsx
    │   ├── main.jsx
    │   └── styles.css
    ├── package.json
    ├── vite.config.js
    └── index.html
```

## Quick Start

### Prerequisites
- Python 3.8+
- Node.js 18+
- Google API Key for Gemini

### Backend Setup

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create `.env` file from example and add your Google API key:
```bash
cp .env.example .env
# Edit .env and add: GOOGLE_API_KEY=your_key_here
```

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
- **Real-time event data sidebar** showing extracted information
- **Completion progress bar** indicating when we have enough info
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
Server responds: {
  "type": "response",
  "data": {
    "message": "assistant response",
    "completion_score": 0.6,
    "is_complete": false,
    "event_data": {...}
  }
}
```

## Development Notes

- Backend uses in-memory session storage for development. Conversations are lost on server restart.
- WebSocket connections work on localhost; use HTTPS/WSS for production.
- CORS is currently open (`*`) - configure properly for production.
- All major TODOs are marked with `# TODO:` comments in the code.

## License

MIT
