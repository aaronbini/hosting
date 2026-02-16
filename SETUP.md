# Development Setup Script

This file documents how to set up the development environment.

## Prerequisites

- Python 3.9+
- Node.js 18+
- uv (https://docs.astral.sh/uv/)
- npm

## Backend Setup

```bash
cd backend

# Install dependencies (uv handles venv creation automatically)
uv sync

# Create .env file
cp .env.example .env

# Edit .env and add your Google API key
# GOOGLE_API_KEY=your_key_here
```

## Frontend Setup

```bash
cd frontend

# Install dependencies
npm install
```

## Running Locally

### Terminal 1 - Backend
```bash
cd backend
uv run python -m uvicorn app.main:app --reload
```

Backend runs at: http://localhost:8000

### Terminal 2 - Frontend
```bash
cd frontend
npm run dev
```

Frontend runs at: http://localhost:5173

## Testing

### Backend Health Check
```bash
curl http://localhost:8000/health
```

### Create a Session
```bash
curl -X POST http://localhost:8000/api/sessions
```

### Send a Chat Message
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "SESSION_ID",
    "message": "I want to plan a dinner party for 10 people"
  }'
```

## Useful Commands

### Install Python packages
```bash
cd backend
uv add package_name  # This updates pyproject.toml and installs
uv sync  # Install all dependencies
```

### Install npm packages
```bash
cd frontend
npm install package_name
npm update  # Update all packages
```

### Format code
```bash
# Python (if black is installed)
black backend/

# JavaScript (if prettier is installed)
cd frontend
npx prettier --write src/
```

## Environment Variables

### Backend (.env)
- `GOOGLE_API_KEY` - Your Google Gemini API key
- `GEMINI_MODEL` - Model name (default: gemini-pro)

### Frontend
- Uses Vite environment variables in `.env` file (optional)
- Proxies API calls to `http://localhost:8000` in dev mode

## Troubleshooting

### Backend fails to start
- Check that `GOOGLE_API_KEY` is set in `.env`
- Ensure port 8000 is not already in use
- Check Python version: `python --version` (should be 3.8+)

### Frontend fails to start
- Ensure Node.js is installed: `node --version` (should be 18+)
- Delete `node_modules` and reinstall: `npm install`
- Clear npm cache: `npm cache clean --force`

### WebSocket connection fails
- Check that both frontend and backend are running
- Ensure backend is on port 8000 and frontend is on 5173
- Check browser console for connection errors

## Next Steps

1. Set up your Google API key for Gemini
2. Start both backend and frontend
3. Open http://localhost:5173 in your browser
4. Try sending a message like: "I want to plan a dinner party for 8 people"
5. Watch the event data panel populate with extracted information
