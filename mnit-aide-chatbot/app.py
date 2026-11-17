"""FastAPI server: serves the chat UI and the /api/chat endpoint."""
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import rag

app = FastAPI(title="MNIT AIDE Chatbot")
STATIC = Path(__file__).parent / "static"


class Turn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[Turn] = []


@app.on_event("startup")
def _startup():
    ok = rag.index.load()
    print(f"Knowledge base loaded: {len(rag.index.chunks)} chunks" if ok
          else "!! Knowledge base not found. Run: python ingest.py")


@app.get("/api/status")
def status():
    pages = len({c["url"] for c in rag.index.chunks})
    return {"ready": rag.index.ready, "chunks": len(rag.index.chunks), "documents": pages}


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        history = [t.model_dump() for t in req.history][-8:]
        return rag.answer(req.message.strip(), history)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Something went wrong: {e}")


@app.get("/")
def root():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
