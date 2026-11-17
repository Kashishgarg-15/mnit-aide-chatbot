"""
Crawl the MNIT AIDE website (HTML pages, tables, PDFs), chunk the text,
embed it with Gemini and save the knowledge base into ./data

Usage:
    python ingest.py            # crawl + embed (resumes embeddings if interrupted)
    python ingest.py --recrawl  # force a fresh crawl even if data/documents.json exists
"""
import argparse
import collections
import hashlib
import io
import json
import pickle
import re
import sys
import time
from urllib.parse import urljoin, urldefrag, urlparse

import numpy as np
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

import config
import rag

requests.packages.urllib3.disable_warnings()  # some govt sites have incomplete cert chains
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MNIT-AIDE-RAG-Bot/1.0)"}
SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".mp4", ".mp3", ".avi",
            ".zip", ".rar", ".css", ".js", ".woff", ".woff2", ".ttf")
DOC_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")


# ------------------------------------------------------------------ helpers

def normalize(url: str, base: str = "") -> str:
    url = urldefrag(urljoin(base, url.strip()))[0]
    p = urlparse(url)
    host = p.netloc.lower().replace("www.", "")
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return f"https://{host}{path}" + (f"?{p.query}" if p.query else "")


def is_dept_page(url: str) -> bool:
    p = urlparse(url)
    return p.netloc == config.SITE_HOST and p.path.lower().startswith(config.ALLOWED_PATH_PREFIX)


def is_pdf(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".pdf")


def fetch(url: str) -> requests.Response:
    try:
        return requests.get(url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT)
    except requests.exceptions.SSLError:
        return requests.get(url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT, verify=False)


def clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ------------------------------------------------------------------ parsing

def table_to_lines(table) -> list:
    """Turn a table into self-contained lines: 'Header: value | Header: value'."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [clean_line(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append((cells, bool(tr.find("th"))))
    if not rows:
        return []
    header = None
    if rows[0][1] or (len(rows) > 1 and not rows[0][1] and all(rows[0][0])):
        header = rows[0][0]
        rows = rows[1:]
    lines = []
    for cells, _ in rows:
        if header and len(header) == len(cells):
            parts = [f"{h}: {c}" for h, c in zip(header, cells) if c]
        else:
            parts = [c for c in cells if c]
        if parts:
            lines.append(" | ".join(parts))
    return lines


def parse_html(html: str, url: str):
    soup = BeautifulSoup(html, "lxml")
    title = clean_line(soup.title.get_text()) if soup.title else url

    for t in soup(["script", "style", "noscript", "iframe", "svg"]):
        t.decompose()

    # links to documents / external resources: keep the URL in the text so the bot can cite it
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absu = normalize(href, url)
        links.add(absu)
        txt = clean_line(a.get_text(" "))
        if absu.lower().split("?")[0].endswith(DOC_EXT) or (
                urlparse(absu).netloc != config.SITE_HOST and absu.startswith("http")):
            a.append(f" (link: {absu})")
    for a in soup.select('a[href^="mailto:"]'):
        a.append(f" ({a['href'][7:]})")

    for img in soup.find_all("img"):
        alt = clean_line(img.get("alt", "") or img.get("title", ""))
        if alt:
            img.replace_with(f" [Image: {alt}] ")

    for table in soup.find_all("table"):
        lines = table_to_lines(table)
        holder = soup.new_tag("div")
        for ln in lines:
            p = soup.new_tag("p")
            p.string = ln
            holder.append(p)
        table.replace_with(holder)

    text = soup.get_text("\n")
    lines = [clean_line(l) for l in text.split("\n")]
    lines = [l for l in lines if len(l) > 1]
    return title, lines, links


def parse_pdf(content: bytes):
    reader = PdfReader(io.BytesIO(content))
    lines = []
    for n, page in enumerate(reader.pages, 1):
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            txt = ""
        page_lines = [clean_line(l) for l in txt.split("\n") if clean_line(l)]
        if page_lines:
            lines.append(f"(page {n})")
            lines.extend(page_lines)
    return lines


# ------------------------------------------------------------------ crawl

def crawl() -> list:
    queue = collections.deque(normalize(u) for u in config.SEED_URLS)
    seen, docs = set(queue), []
    n_html = n_pdf = 0

    while queue:
        url = queue.popleft()
        pdf = is_pdf(url)
        if pdf and n_pdf >= config.MAX_PDFS:
            continue
        if not pdf and n_html >= config.MAX_PAGES:
            continue
        try:
            time.sleep(config.CRAWL_DELAY)
            r = fetch(url)
            if r.status_code != 200:
                print(f"  [{r.status_code}] {url}")
                continue
            ctype = r.headers.get("content-type", "").lower()

            if pdf or "pdf" in ctype:
                if len(r.content) > config.MAX_PDF_MB * 1024 * 1024:
                    print(f"  [skip big pdf] {url}")
                    continue
                lines = parse_pdf(r.content)
                if sum(len(l) for l in lines) < 80:
                    print(f"  [pdf has no text - scanned?] {url}")
                    continue
                n_pdf += 1
                fname = urlparse(url).path.rsplit("/", 1)[-1]
                docs.append({"url": url, "title": f"PDF: {fname}", "type": "pdf", "lines": lines})
                print(f"  [pdf  {n_pdf:>3}] {url}  ({len(lines)} lines)")

            elif "html" in ctype or "text" in ctype:
                title, lines, links = parse_html(r.text, url)
                n_html += 1
                docs.append({"url": url, "title": title, "type": "html", "lines": lines})
                print(f"  [page {n_html:>3}] {url}  ({len(lines)} lines)")
                for l in links:
                    low = urlparse(l).path.lower()
                    if l in seen or low.endswith(SKIP_EXT):
                        continue
                    if is_dept_page(l) or (low.endswith(".pdf") and urlparse(l).netloc == config.SITE_HOST):
                        seen.add(l)
                        queue.append(l)
        except Exception as e:  # noqa: BLE001
            print(f"  [error] {url}: {e}")

    return docs


def strip_boilerplate(docs: list) -> None:
    """Remove lines (menus, headers, footers) repeated on most HTML pages."""
    html_docs = [d for d in docs if d["type"] == "html"]
    if len(html_docs) < 5:
        return
    counter = collections.Counter()
    for d in html_docs:
        counter.update(set(d["lines"]))
    limit = 0.6 * len(html_docs)
    common = {l for l, c in counter.items() if c > limit}
    for d in html_docs:
        d["lines"] = [l for l in d["lines"] if l not in common]
    print(f"  removed {len(common)} boilerplate lines shared across pages")


# ------------------------------------------------------------------ chunking

def chunk_doc(doc: dict) -> list:
    size, overlap = config.CHUNK_SIZE, config.CHUNK_OVERLAP
    head = f"Source: {doc['title']} ({doc['url']})\n"
    pieces = []
    for l in doc["lines"]:           # hard-split extremely long lines
        while len(l) > size:
            pieces.append(l[:size])
            l = l[size:]
        pieces.append(l)

    chunks, cur, cur_len = [], [], 0
    for l in pieces:
        if cur_len + len(l) + 1 > size and cur:
            chunks.append("\n".join(cur))
            tail, t_len = [], 0
            for prev in reversed(cur):          # carry overlap
                if t_len + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                t_len += len(prev) + 1
            cur, cur_len = tail, t_len
        cur.append(l)
        cur_len += len(l) + 1
    if cur and sum(len(x) for x in cur) > 20:
        chunks.append("\n".join(cur))
    return [{"text": head + c, "url": doc["url"], "title": doc["title"], "type": doc["type"]}
            for c in chunks]


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recrawl", action="store_true", help="ignore saved documents.json")
    args = ap.parse_args()

    config.DATA_DIR.mkdir(exist_ok=True)
    if not config.GEMINI_API_KEY:
        sys.exit("GEMINI_API_KEY missing - copy .env.example to .env and add your key.")

    if config.DOCS_FILE.exists() and not args.recrawl:
        print("Using saved crawl (data/documents.json). Use --recrawl to refresh.")
        docs = json.loads(config.DOCS_FILE.read_text(encoding="utf-8"))
    else:
        print("1/3 Crawling website...")
        docs = crawl()
        if not docs:
            sys.exit("Nothing was crawled. Check your internet connection / site availability.")
        strip_boilerplate(docs)
        docs = [d for d in docs if d["lines"]]
        config.DOCS_FILE.write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"   saved {len(docs)} documents")

    print("2/3 Chunking...")
    chunks = []
    for d in docs:
        chunks.extend(chunk_doc(d))
    print(f"   {len(chunks)} chunks")

    print("3/3 Embedding with Gemini (free tier is rate limited; this may take a few minutes)...")
    cache = {}
    if config.EMBED_CACHE_FILE.exists():
        cache = pickle.loads(config.EMBED_CACHE_FILE.read_bytes())
    key = lambda t: hashlib.sha1((config.EMBED_MODEL + t).encode()).hexdigest()  # noqa: E731
    todo = [c for c in chunks if key(c["text"]) not in cache]
    print(f"   {len(chunks) - len(todo)} cached, {len(todo)} to embed")

    for i in range(0, len(todo), config.EMBED_BATCH):
        batch = todo[i:i + config.EMBED_BATCH]
        vecs = rag.embed_texts([c["text"] for c in batch], "RETRIEVAL_DOCUMENT")
        for c, v in zip(batch, vecs):
            cache[key(c["text"])] = v
        config.EMBED_CACHE_FILE.write_bytes(pickle.dumps(cache))
        print(f"   embedded {min(i + config.EMBED_BATCH, len(todo))}/{len(todo)}")
        time.sleep(2)

    vectors = np.stack([cache[key(c["text"])] for c in chunks]).astype(np.float32)
    config.CHUNKS_FILE.write_text(json.dumps(chunks, ensure_ascii=False), encoding="utf-8")
    np.save(config.EMBEDDINGS_FILE, vectors)
    print(f"\nDone. Knowledge base ready: {len(chunks)} chunks from {len(docs)} documents.")
    print("Start the app with:  uvicorn app:app --reload")


if __name__ == "__main__":
    main()
