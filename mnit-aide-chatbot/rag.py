"""Retrieval + generation using Gemini (embeddings + chat)."""
import json
import random
import re
import time
from typing import List, Optional

import numpy as np
from google import genai
from google.genai import types

import config

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing. Add it to the .env file.")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _is_rate_limit(err: Exception) -> bool:
    s = str(err)
    return "429" in s or "RESOURCE_EXHAUSTED" in s or "quota" in s.lower()


def _is_transient(err: Exception) -> bool:
    s = str(err)
    return any(c in s for c in ("500", "502", "503", "504", "UNAVAILABLE", "DEADLINE"))


def embed_texts(texts: List[str], task_type: str) -> np.ndarray:
    """Embed texts in one request (caller keeps batches small). Returns L2-normalised vectors."""
    client = get_client()
    for attempt in range(1, 9):
        try:
            res = client.models.embed_content(
                model=config.EMBED_MODEL,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=config.EMBED_DIM,
                ),
            )
            vecs = np.array([e.values for e in res.embeddings], dtype=np.float32)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1
            return vecs / norms
        except Exception as e:  # noqa: BLE001
            if _is_rate_limit(e):
                wait = min(20 * attempt, 90)
                print(f"   rate limited, waiting {wait}s (attempt {attempt}/8)...")
                time.sleep(wait)
            elif _is_transient(e):
                time.sleep(3 * attempt)
            else:
                raise
    raise RuntimeError("Embedding failed after several retries (rate limit?). Try again later.")


# ------------------------------------------------------------------ index

class Index:
    def __init__(self):
        self.chunks: List[dict] = []
        self.vectors: Optional[np.ndarray] = None

    @property
    def ready(self) -> bool:
        return self.vectors is not None and len(self.chunks) > 0

    def load(self) -> bool:
        if not (config.CHUNKS_FILE.exists() and config.EMBEDDINGS_FILE.exists()):
            return False
        self.chunks = json.loads(config.CHUNKS_FILE.read_text(encoding="utf-8"))
        self.vectors = np.load(config.EMBEDDINGS_FILE)
        return len(self.chunks) == len(self.vectors)

    def search(self, query: str, k: int = config.TOP_K) -> List[dict]:
        qv = embed_texts([query], "RETRIEVAL_QUERY")[0]
        sims = self.vectors @ qv

        # light keyword boost so names / numbers / codes (e.g. "AI-301", "Dr. Sharma") rank well
        terms = {t for t in re.findall(r"[a-z0-9]{3,}", query.lower())}
        if terms:
            boost = np.array([
                sum(1 for t in terms if t in c["text"].lower()) / len(terms)
                for c in self.chunks
            ], dtype=np.float32)
            sims = sims + 0.12 * boost

        order = np.argsort(-sims)[: k * 2]
        out, per_url = [], {}
        for i in order:
            c = self.chunks[int(i)]
            if per_url.get(c["url"], 0) >= 4:   # avoid one page flooding the context
                continue
            per_url[c["url"]] = per_url.get(c["url"], 0) + 1
            out.append({**c, "score": float(sims[i])})
            if len(out) >= k:
                break
        return out


index = Index()

# ------------------------------------------------------------------ generation

SYSTEM_PROMPT = """You are the official-style virtual assistant for the Department of Artificial \
Intelligence and Data Engineering (AIDE), Malaviya National Institute of Technology (MNIT) Jaipur.

Rules:
1. Answer ONLY using the CONTEXT excerpts taken from the AIDE department website. Never invent \
names, dates, numbers, emails, phone numbers or links.
2. If the context does not contain the answer, say you could not find it on the AIDE website and \
suggest the Contacts page (https://mnit.ac.in/dept_aide/contacts). If the context has partial \
information, share it and say what is missing.
3. Be clear and well organised. Use Markdown: short paragraphs, bullet lists, and tables when \
listing many items (faculty, publications, courses, events, etc.).
4. When the context includes a document or page link that is useful (PDF, form, notice), include it \
as a Markdown link.
5. Do NOT start answers with greetings like "Hello!" and do not end with "Is there anything else" \
unless the user greeted you first. Go straight to the answer.
6. Do not mention "context" or "excerpts" in your answer; just answer naturally."""


def _build_retrieval_query(message: str, history: List[dict]) -> str:
    """Short follow-ups ('and his email?') are expanded with the previous user question."""
    if len(message.split()) < 6 and history:
        prev = [h["content"] for h in history if h["role"] == "user"]
        if prev:
            return f"{prev[-1]} {message}"
    return message


FAQ_DELAY = (2.0, 3.0)   # seconds; set to (0, 0) to answer instantly


def _match_faq(message: str):
    """Hardcoded answers from faq.json (re-read each time, so edits need no restart)."""
    path = config.BASE_DIR / "faq.json"
    if not path.exists():
        return None
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        print("faq.json is invalid JSON, ignoring it")
        return None
    q = message.lower()
    for e in entries:
        for group in e.get("match", []):
            if all(w.lower() in q for w in group):
                return e
    return None


def answer(message: str, history: List[dict]) -> dict:
    faq = _match_faq(message)
    if faq and faq.get("mode", "direct") == "direct":
        time.sleep(random.uniform(*FAQ_DELAY))
        return {"answer": faq["answer"], "sources": faq.get("sources", [])}
    if not index.ready:
        raise RuntimeError("Knowledge base is empty. Run `python ingest.py` first.")

    hits = index.search(_build_retrieval_query(message, history))
    good = [h for h in hits if h["score"] >= config.MIN_SCORE] or hits[:3]

    context = "\n\n".join(
        f"[Source {i + 1}: {h['title']} - {h['url']}]\n{h['text']}" for i, h in enumerate(good)
    )
    if faq:
        context = f"[VERIFIED INFORMATION - highest priority]\n{faq['answer']}\n\n" + context
    convo = ""
    for h in history[-6:]:
        who = "User" if h["role"] == "user" else "Assistant"
        convo += f"{who}: {h['content']}\n"

    prompt = (
        f"CONTEXT:\n{context}\n\n"
        f"CONVERSATION SO FAR:\n{convo or '(none)'}\n"
        f"USER QUESTION: {message}\n\nANSWER:"
    )

    client = get_client()
    last_err: Optional[Exception] = None
    for model in config.CHAT_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.2,
                        max_output_tokens=2048,
                    ),
                )
                text = (resp.text or "").strip()
                if not text:
                    text = "I couldn't generate an answer for that. Please try rephrasing your question."
                seen, sources = set(), []
                for h in good:
                    if h["url"] not in seen:
                        seen.add(h["url"])
                        sources.append({"title": h["title"], "url": h["url"]})
                return {"answer": text, "sources": sources[:5]}
            except Exception as e:  # noqa: BLE001
                last_err = e
                if _is_rate_limit(e):
                    break  # try next model
                if _is_transient(e):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
    raise RuntimeError(
        "The Gemini free-tier rate limit was reached. Please wait a minute and try again."
        if last_err and _is_rate_limit(last_err) else f"Gemini request failed: {last_err}"
    )
