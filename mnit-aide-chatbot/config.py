"""Central configuration for the MNIT AIDE chatbot."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Chat models are tried in order; if one is rate limited the next is used.
CHAT_MODELS = [m for m in [
    os.getenv("CHAT_MODEL", "gemini-2.5-flash"),
    "gemini-2.5-flash-lite",
] if m]
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = 768  # gemini-embedding-001 supports 768 / 1536 / 3072

DATA_DIR = BASE_DIR / "data"
DOCS_FILE = DATA_DIR / "documents.json"      # raw scraped text (for inspection)
CHUNKS_FILE = DATA_DIR / "chunks.json"       # chunked text + metadata
EMBEDDINGS_FILE = DATA_DIR / "embeddings.npy"
EMBED_CACHE_FILE = DATA_DIR / "embed_cache.pkl"  # lets ingestion resume

# ---- Crawl settings ----
SEED_URLS = [
    "https://www.mnit.ac.in/dept_aide/",
    "https://mnit.ac.in/dept_aide/index",
    "https://mnit.ac.in/dept_aide/academics",
    "https://mnit.ac.in/dept_aide/facilities",
    "https://mnit.ac.in/dept_aide/committee",
    "https://mnit.ac.in/dept_aide/patents",
    "https://mnit.ac.in/dept_aide/publications",
    "https://mnit.ac.in/dept_aide/projects",
    "https://mnit.ac.in/dept_aide/events",
    "https://mnit.ac.in/dept_aide/awards",
    "https://mnit.ac.in/dept_aide/gallery",
    "https://mnit.ac.in/dept_aide/contacts",
]
SITE_HOST = "mnit.ac.in"
ALLOWED_PATH_PREFIX = "/dept_aide"
MAX_PAGES = 400            # safety limit for HTML pages
MAX_PDFS = 150             # safety limit for PDF files
MAX_PDF_MB = 20
REQUEST_TIMEOUT = 40
CRAWL_DELAY = 0.5          # seconds between requests (be polite)

# ---- Chunking / retrieval ----
CHUNK_SIZE = 1200          # characters
CHUNK_OVERLAP = 200
EMBED_BATCH = 16
TOP_K = 8
MIN_SCORE = 0.30
