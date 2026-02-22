# Async File Upload Processing Architecture

## Overview

This document describes the architectural changes needed to convert the synchronous recipe file upload system to an asynchronous, non-blocking implementation using background task processing and WebSocket notifications.

**Status**: Not implemented (documentation only)
**Estimated Effort**: 3-6 hours
**Prerequisites**: Understanding of asyncio, task queues, WebSocket communication

---

## Problem Statement

### Current Synchronous Flow

```
User clicks Upload
    ↓
Frontend: POST /api/sessions/{id}/upload-recipe
    ↓
Backend: await file.read()
    ↓
Backend: await ai_service.extract_recipe_from_file()  ← BLOCKS for 3-10 seconds
    ↓
Backend: Update session state
    ↓
Backend: Return 200 + ingredients JSON
    ↓
Frontend: Show success message
```

**Issues**:
- HTTP request held open for entire extraction duration (3-10 seconds)
- User cannot interact with the app during extraction
- Timeouts on slow networks or large files
- Poor UX for multi-file uploads (if added later)

---

## Proposed Async Flow

```
User clicks Upload
    ↓
Frontend: POST /api/sessions/{id}/upload-recipe
    ↓
Backend: await file.read()
    ↓
Backend: Create extraction task in queue
    ↓
Backend: Return 202 Accepted + task_id immediately
    ↓
Frontend: Shows "Extracting..." (non-blocking)
    │
    │ (User can continue chatting while extraction happens)
    │
    ├─ Background Worker: Pulls task from queue
    │       ↓
    │  Worker: await extract_recipe_from_file()
    │       ↓
    │  Worker: Update session state
    │       ↓
    │  Worker: Send WebSocket message to frontend
    │       ↓
    ↓  Frontend: Receives WS notification
    ↓       ↓
    └─ Frontend: Shows success toast / updates UI
```

**Benefits**:
- Immediate HTTP response (< 100ms)
- User can continue using the app during extraction
- More resilient to network issues
- Better scalability for concurrent uploads
- Foundation for batch processing

---

## Components Required

### 1. Task Queue (Backend)

Store pending extraction tasks. Two implementation options:

#### Option A: In-Memory Queue (Simple MVP)
- Uses Python `asyncio.Queue`
- No external dependencies
- Tasks lost on server restart
- Good for single-server deployments

```python
# In main.py or new task_queue.py
from asyncio import Queue
from dataclasses import dataclass
from datetime import datetime

@dataclass
class ExtractionTask:
    task_id: str           # UUID
    session_id: str
    dish_name: str
    file_content: bytes
    file_mime_type: str
    created_at: datetime
    status: str            # "pending" | "processing" | "completed" | "failed"

# Global task queue
extraction_queue: Queue[ExtractionTask] = Queue()
```

#### Option B: Redis-Backed Queue (Production)
- Uses `arq` (async task queue for FastAPI)
- Persistent across restarts
- Supports retries and dead letter queues
- Requires Redis server

```python
# requirements: arq, redis
from arq import create_pool
from arq.connections import RedisSettings

# In main.py
redis_pool = await create_pool(RedisSettings())

# Enqueue task
await redis_pool.enqueue_job('extract_recipe', task_id, session_id, ...)
```

**Recommendation**: Start with Option A for MVP, migrate to Option B if needed.

---

### 2. Background Worker Pool (Backend)

Process tasks from the queue concurrently.

**New file**: `backend/app/worker.py`

```python
import asyncio
from app.services.ai_service import ai_service
from app.services.session_manager import session_manager
from app.models.event import RecipeSourceType
from app.main import _find_or_create_recipe_source, websocket_manager

async def extraction_worker(worker_id: int, queue: asyncio.Queue):
    """Background worker that processes extraction tasks."""
    print(f"Worker {worker_id} started")

    while True:
        task = await queue.get()
        print(f"Worker {worker_id} processing task {task.task_id}")

        try:
            # Extract ingredients using AI
            ingredients = await ai_service.extract_recipe_from_file(
                task.file_content,
                task.file_mime_type
            )

            # Update session state
            session = session_manager.get_session(task.session_id)
            if session:
                rs = _find_or_create_recipe_source(session.event_data, task.dish_name)
                rs.source_type = RecipeSourceType.USER_UPLOAD
                rs.extracted_ingredients = [i.model_dump() for i in ingredients]
                rs.confirmed = True

                # Remove from recipe promises
                session.event_data.recipe_promises = [
                    p for p in session.event_data.recipe_promises
                    if p.lower() != task.dish_name.lower()
                ]

            # Notify frontend via WebSocket
            await websocket_manager.send_extraction_result(
                session_id=task.session_id,
                task_id=task.task_id,
                success=True,
                dish_name=task.dish_name,
                ingredient_count=len(ingredients),
                ingredients=[i.model_dump() for i in ingredients]
            )

            print(f"Worker {worker_id} completed task {task.task_id}")

        except Exception as e:
            print(f"Worker {worker_id} failed task {task.task_id}: {e}")

            # Notify failure via WebSocket
            await websocket_manager.send_extraction_result(
                session_id=task.session_id,
                task_id=task.task_id,
                success=False,
                error=str(e)
            )

        finally:
            queue.task_done()


# Start workers at app startup
async def start_worker_pool(queue: asyncio.Queue, num_workers: int = 3):
    """Spawn multiple workers to process tasks concurrently."""
    workers = [
        asyncio.create_task(extraction_worker(i, queue))
        for i in range(num_workers)
    ]
    return workers
```

**In `main.py`**, start workers on app startup:

```python
from app.worker import start_worker_pool, extraction_queue

@app.on_event("startup")
async def startup_event():
    # Start 3 concurrent extraction workers
    await start_worker_pool(extraction_queue, num_workers=3)
    print("Background workers started")
```

---

### 3. WebSocket Manager (Backend)

Track active WebSocket connections and send notifications.

**New file**: `backend/app/services/websocket_manager.py`

```python
from fastapi import WebSocket
from typing import Dict

class WebSocketManager:
    """Manages WebSocket connections and message routing."""

    def __init__(self):
        # Map session_id → WebSocket connection
        self.connections: Dict[str, WebSocket] = {}

    def register(self, session_id: str, ws: WebSocket):
        """Register a new WebSocket connection for a session."""
        self.connections[session_id] = ws
        print(f"WebSocket registered for session {session_id}")

    def unregister(self, session_id: str):
        """Unregister a WebSocket connection."""
        self.connections.pop(session_id, None)
        print(f"WebSocket unregistered for session {session_id}")

    async def send_extraction_result(
        self,
        session_id: str,
        task_id: str,
        success: bool,
        **kwargs
    ):
        """Send extraction completion notification to frontend."""
        ws = self.connections.get(session_id)
        if not ws:
            print(f"No WebSocket found for session {session_id}")
            return

        try:
            await ws.send_json({
                "type": "extraction_complete",
                "task_id": task_id,
                "success": success,
                **kwargs
            })
            print(f"Sent extraction_complete to session {session_id}")
        except Exception as e:
            print(f"Failed to send WebSocket message: {e}")
            self.unregister(session_id)

# Global instance
websocket_manager = WebSocketManager()
```

**In `main.py`**, register/unregister WebSocket connections:

```python
from app.services.websocket_manager import websocket_manager

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Register WebSocket connection
    websocket_manager.register(session_id, websocket)

    try:
        # Existing WebSocket message handling loop...
        while True:
            data = await websocket.receive_json()
            # ... handle messages ...
    except WebSocketDisconnect:
        websocket_manager.unregister(session_id)
```

---

### 4. Updated Upload Endpoint (Backend)

Return `202 Accepted` immediately instead of blocking.

**In `main.py`**, replace the existing `/upload-recipe` endpoint:

```python
import uuid
from fastapi.responses import JSONResponse
from app.worker import extraction_queue, ExtractionTask
from datetime import datetime

@app.post("/api/sessions/{session_id}/upload-recipe")
async def upload_recipe(
    session_id: str,
    dish_name: str,
    file: UploadFile = File(...),
):
    """
    Enqueue recipe extraction task and return immediately.
    Returns 202 Accepted with task_id for tracking.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Validate file type
    allowed_types = {
        "application/pdf",
        "text/plain",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}"
        )

    # Read file content
    content = await file.read()

    # Validate file size (5MB limit)
    MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(content)} bytes). Maximum: {MAX_FILE_SIZE} bytes (5MB)"
        )

    # Create extraction task
    task_id = str(uuid.uuid4())
    task = ExtractionTask(
        task_id=task_id,
        session_id=session_id,
        dish_name=dish_name,
        file_content=content,
        file_mime_type=file.content_type,
        created_at=datetime.now(),
        status="pending"
    )

    # Enqueue for background processing
    await extraction_queue.put(task)

    # Return immediately with 202 Accepted
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "message": "Extraction started",
            "estimated_seconds": 5
        }
    )
```

---

### 5. Frontend WebSocket Handler (Frontend)

Listen for extraction completion messages.

**In `frontend/src/hooks/useChat.ts`**, add handler:

```typescript
useEffect(() => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return

  const handleMessage = (event: MessageEvent) => {
    const msg = JSON.parse(event.data)

    // Existing handlers for stream_start, stream_chunk, etc.
    if (msg.type === 'stream_start') {
      // ... existing code ...
    }

    // NEW: Handle extraction completion
    if (msg.type === 'extraction_complete') {
      if (msg.success) {
        // Show success notification
        console.log(`✓ Extracted ${msg.ingredient_count} ingredients for ${msg.dish_name}`)

        // Optional: Update local state to trigger UI refresh
        // or just rely on the next stream_start to refresh eventData

        // Show toast notification (if using a toast library)
        // toast.success(`Recipe for ${msg.dish_name} extracted!`)
      } else {
        // Show error notification
        console.error(`✗ Extraction failed: ${msg.error}`)
        // toast.error(`Extraction failed: ${msg.error}`)
      }
    }
  }

  ws.addEventListener('message', handleMessage)
  return () => ws.removeEventListener('message', handleMessage)
}, [ws])
```

---

### 6. Frontend Upload Handler (Frontend)

Handle `202 Accepted` response.

**In `frontend/src/components/RecipeUploadPanel.tsx`**, update `handleSubmit`:

```typescript
const handleSubmit = async (e: React.FormEvent) => {
  e.preventDefault()
  if (!file || !selectedDish) return

  setUploading(true)
  setResult(null)

  const formData = new FormData()
  formData.append('file', file)

  try {
    const res = await fetch(
      `/api/sessions/${sessionId}/upload-recipe?dish_name=${encodeURIComponent(selectedDish)}`,
      { method: 'POST', body: formData }
    )

    if (res.status === 202) {
      // Async processing started
      const { task_id, estimated_seconds } = await res.json()

      setResult({
        success: true,
        message: `Extracting ingredients in background (task ${task_id.substring(0, 8)}…)`
      })

      // Clear form for next upload
      setFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ''

      // NOTE: Actual success/failure will arrive via WebSocket
      // The WebSocket handler in useChat will show toast notification

    } else if (res.ok) {
      // Fallback: synchronous response (shouldn't happen in async mode)
      const data = await res.json()
      const count = data.ingredients?.length ?? 0
      setResult({
        success: true,
        message: `Extracted ${count} ingredient${count !== 1 ? 's' : ''}`
      })
      onUploadComplete?.(selectedDish)

    } else {
      // Error response
      const err = await res.json().catch(() => ({}))
      setResult({
        success: false,
        message: err.detail ?? `Upload failed (HTTP ${res.status})`
      })
    }
  } catch (err) {
    setResult({
      success: false,
      message: 'Network error — check your connection and try again.'
    })
  } finally {
    setUploading(false)
  }
}
```

---

## State Management

### Session State Changes

Add task tracking to `EventPlanningData` in `backend/app/models/event.py`:

```python
class EventPlanningData(BaseModel):
    # ... existing fields ...

    # Track pending extraction tasks
    pending_extraction_tasks: List[str] = Field(
        default_factory=list,
        description="Task IDs for in-flight recipe extractions"
    )
```

Update when tasks start/complete:

```python
# When task is created:
session.event_data.pending_extraction_tasks.append(task_id)

# When task completes:
session.event_data.pending_extraction_tasks = [
    tid for tid in session.event_data.pending_extraction_tasks
    if tid != task_id
]
```

### Task Cleanup

Periodically clean up old completed/failed tasks to prevent memory leaks:

```python
from datetime import datetime, timedelta

async def cleanup_old_tasks():
    """Remove task records older than 1 hour."""
    while True:
        await asyncio.sleep(3600)  # Run every hour
        cutoff = datetime.now() - timedelta(hours=1)
        # Remove old tasks from storage (if persisting tasks)
```

---

## Error Handling

### 1. Task Timeout
If extraction takes longer than expected:

```python
async def extraction_worker_with_timeout(worker_id: int, queue: asyncio.Queue):
    while True:
        task = await queue.get()
        try:
            # Set timeout for extraction
            ingredients = await asyncio.wait_for(
                ai_service.extract_recipe_from_file(...),
                timeout=30.0  # 30 second timeout
            )
            # ... success handling ...
        except asyncio.TimeoutError:
            # Notify timeout via WebSocket
            await websocket_manager.send_extraction_result(
                session_id=task.session_id,
                task_id=task.task_id,
                success=False,
                error="Extraction timed out after 30 seconds"
            )
        finally:
            queue.task_done()
```

### 2. Worker Crash
If a worker crashes, the task remains in the queue and will be picked up by another worker (for in-memory queue). For Redis queue, use `arq`'s built-in retry mechanism.

### 3. WebSocket Disconnection
If WebSocket is disconnected when extraction completes:

```python
async def send_extraction_result(self, session_id, task_id, **result):
    ws = self.connections.get(session_id)
    if not ws:
        # Store result in session for retrieval on reconnect
        session = session_manager.get_session(session_id)
        if session:
            if not hasattr(session, 'pending_notifications'):
                session.pending_notifications = []
            session.pending_notifications.append({
                "type": "extraction_complete",
                "task_id": task_id,
                **result
            })
        return

    await ws.send_json({"type": "extraction_complete", "task_id": task_id, **result})
```

On reconnect, send pending notifications:

```python
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    websocket_manager.register(session_id, websocket)

    # Send any pending notifications
    session = session_manager.get_session(session_id)
    if session and hasattr(session, 'pending_notifications'):
        for notif in session.pending_notifications:
            await websocket.send_json(notif)
        session.pending_notifications = []

    # ... continue with message loop ...
```

### 4. Session Not Found
If session is deleted while task is processing:

```python
session = session_manager.get_session(task.session_id)
if not session:
    print(f"Session {task.session_id} not found, skipping state update")
    # Still send WebSocket notification (if connected)
    await websocket_manager.send_extraction_result(...)
    return
```

---

## Testing Strategy

### Unit Tests

```python
# test_worker.py
import pytest
from app.worker import ExtractionTask, extraction_worker

@pytest.mark.asyncio
async def test_extraction_worker_success():
    queue = asyncio.Queue()
    task = ExtractionTask(
        task_id="test-123",
        session_id="session-456",
        dish_name="Test Dish",
        file_content=b"...",
        file_mime_type="text/plain",
        created_at=datetime.now(),
        status="pending"
    )
    await queue.put(task)

    # Run worker for one task
    worker_task = asyncio.create_task(extraction_worker(0, queue))
    await queue.join()
    worker_task.cancel()

    # Assert WebSocket was called, session updated, etc.
```

### Integration Tests

```python
# test_async_upload.py
import pytest
from fastapi.testclient import TestClient

def test_upload_returns_202():
    client = TestClient(app)
    response = client.post(
        "/api/sessions/test-session/upload-recipe?dish_name=Pasta",
        files={"file": ("recipe.txt", b"ingredients...", "text/plain")}
    )
    assert response.status_code == 202
    assert "task_id" in response.json()
```

---

## Migration Path

### Phase 1: Add Infrastructure (No Breaking Changes)
1. Add `WebSocketManager` class
2. Add `worker.py` with worker pool
3. Add `extraction_queue` to main.py
4. Start workers on app startup

### Phase 2: Dual-Mode Support
1. Keep existing synchronous endpoint as `/upload-recipe-sync`
2. Add new async endpoint as `/upload-recipe-async`
3. Frontend conditionally uses async if available

### Phase 3: Full Migration
1. Replace `/upload-recipe` with async version
2. Remove synchronous endpoint
3. Update frontend to expect 202 responses

### Phase 4: Production Hardening (Optional)
1. Replace in-memory queue with Redis + `arq`
2. Add task persistence
3. Add monitoring/metrics (task queue length, worker utilization, etc.)

---

## Trade-offs

| Aspect | Synchronous (Current) | Async (This Doc) |
|--------|----------------------|------------------|
| **Complexity** | Low (50 LOC) | Medium-High (200+ LOC) |
| **User Experience** | Blocks UI for 3-10s | Non-blocking, can chat during extraction |
| **Infrastructure** | None | Task queue (in-mem or Redis) |
| **Resilience** | Fails on timeout/disconnect | Survives disconnects, retryable |
| **Scalability** | Limited by HTTP timeouts | Handles concurrent uploads easily |
| **Implementation Time** | Done ✓ | 3-6 hours |
| **Latency Perception** | Feels slow (blocking) | Feels fast (immediate feedback) |
| **Debugging** | Simple stack traces | Harder (async tasks, workers) |

---

## Minimal MVP Approach (No Queue)

If you want async processing without full infrastructure, use `asyncio.create_task()`:

```python
# Simplest async implementation
@app.post("/api/sessions/{session_id}/upload-recipe")
async def upload_recipe(session_id: str, dish_name: str, file: UploadFile):
    content = await file.read()
    task_id = str(uuid.uuid4())

    # Spawn background task (fire-and-forget)
    asyncio.create_task(
        process_upload_in_background(session_id, dish_name, content, file.content_type, task_id)
    )

    return JSONResponse(202, {"task_id": task_id, "message": "Processing started"})

async def process_upload_in_background(session_id, dish_name, content, mime_type, task_id):
    try:
        ingredients = await ai_service.extract_recipe_from_file(content, mime_type)
        # Update session state
        # Send WebSocket notification
        await websocket_manager.send_extraction_result(
            session_id, task_id, success=True, ingredients=ingredients
        )
    except Exception as e:
        await websocket_manager.send_extraction_result(
            session_id, task_id, success=False, error=str(e)
        )
```

**Pros**: Very simple, no queue infrastructure
**Cons**: Tasks lost on restart, no retry mechanism, harder to monitor

---

## Conclusion

The async architecture provides a significantly better user experience at the cost of increased backend complexity. The in-memory queue approach is a good starting point for MVP, with the option to migrate to Redis-backed persistence later if needed.

**Next Steps**:
1. Review this document
2. Decide on MVP vs. full implementation
3. Implement Phase 1 (infrastructure)
4. Test with sample uploads
5. Roll out to production incrementally

**Questions?** See main codebase or contact the team.
