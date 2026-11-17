# MNIT AIDE Department RAG Chatbot

A Retrieval-Augmented Generation chatbot that answers questions about the
AIDE department (MNIT Jaipur) using only content from its website
(HTML pages, tables and PDFs). Powered by the free Gemini API.

**Stack:** Python, FastAPI, Gemini (`gemini-embedding-001` + `gemini-2.5-flash`), NumPy vector search, plain HTML/CSS/JS front end.

## Setup

1. Install Python 3.10 or newer.
2. Create and activate a virtual environment (optional but recommended):
   ```
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # macOS / Linux
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Get a free key at https://aistudio.google.com/apikey, then copy `.env.example` to `.env` and paste the key:
   ```
   GEMINI_API_KEY=your_key_here
   ```

## Build the knowledge base (run once)

```
python ingest.py
```

This crawls all 12 department pages, follows links that stay inside `/dept_aide/`,
downloads linked PDFs, converts tables into readable rows, chunks the text, and
embeds it with Gemini. Results are saved in `data/`.

- The free tier is rate limited. If it says "rate limited, waiting...", just let it run. Embeddings are cached, so if you stop it and run again, it resumes where it left off.
- To refresh after the website changes: `python ingest.py --recrawl`
- Scraped text is saved to `data/documents.json` so you can check what the bot actually knows.

## Run the web app

```
uvicorn app:app --reload
```

Open http://127.0.0.1:8000

## Project structure

```
app.py            FastAPI server (/api/chat, /api/status, serves UI)
rag.py            Embedding, retrieval (cosine + keyword boost), Gemini answer generation
ingest.py         Crawler, HTML/table/PDF parser, chunker, embedder
config.py         URLs, models, chunk size, retrieval settings
static/           Chat UI (index.html, style.css, app.js): modern, responsive, dark/light
data/             Generated knowledge base (created by ingest.py)
```

## Notes and limitations

- Scanned (image-only) PDFs have no extractable text and are skipped; the crawler prints a warning for each. Text inside images (posters, gallery photos) is not read, only alt text/captions.
- If the site uses JavaScript to load content, requests-based scraping cannot see it. Check `data/documents.json` for empty pages; if so, tell me and the crawler can be switched to Playwright.
- If the site has an SSL certificate problem, the crawler automatically retries without verification (read-only public pages).
- Tune answer quality in `config.py` (`TOP_K`, `CHUNK_SIZE`, `MIN_SCORE`).
- To deploy publicly, host on Render/Railway/any VPS, set `GEMINI_API_KEY` as an environment variable, and commit the `data/` folder (or run `ingest.py` on the server).
