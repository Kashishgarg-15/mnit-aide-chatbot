#!/usr/bin/env python3
"""
Evaluate the MNIT AIDE RAG chatbot on qa_dataset.json (50 questions).

Metrics reported
  1. Key-Fact Accuracy   - strict (all required facts present) and mean fact recall
  2. Token F1            - SQuAD-style word overlap with the reference answer
  3. Semantic Similarity - cosine similarity of answer vs reference embeddings
  4. Source Hit@5 / MRR  - did the cited sources include the expected page?
  5. Abstention Quality  - refuses out-of-scope questions, does not refuse answerable ones
  (+ latency, and an optional LLM-as-judge score with --judge)

Usage (run from anywhere; point --project at the chatbot folder)
  python evaluate_chatbot.py --backend local --project ./mnit-aide-chatbot
  python evaluate_chatbot.py --backend http  --url http://127.0.0.1:8000
  python evaluate_chatbot.py --backend mock            # dry run, no API calls

Answers are cached in <out>/raw_answers.jsonl, so an interrupted run resumes
(useful on the Gemini free tier). Use --fresh to ignore the cache.
"""
import argparse, json, math, re, statistics, sys, time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent

REFUSAL_PATTERNS = [
    r"could not find", r"couldn't find", r"can't find", r"cannot find",
    r"not (?:available|listed|mentioned|provided|specified|found)",
    r"no (?:information|details|data)", r"don't have (?:that|any|this)",
    r"does not (?:contain|provide|include|mention)", r"doesn't (?:contain|provide|include|mention)",
    r"isn't (?:available|listed|mentioned)", r"outside (?:the )?scope",
    r"only (?:answer|help).{0,40}aide",
]

# ------------------------------------------------------------------ text utils

def norm(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[*_`#>]", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1 \g<0>", t)   # keep link text and URL
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", t).strip()

def squash(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())

def tokens(t: str):
    return re.findall(r"[a-z0-9]+", norm(t))

def fact_present(fact: str, ans_norm: str, ans_squash: str) -> bool:
    if fact.startswith("re:"):
        return re.search(fact[3:], ans_norm) is not None
    f = norm(fact)
    if f in ans_norm:
        return True
    sq = squash(fact)
    return len(sq) >= 6 and sq in ans_squash          # phone numbers, reg. numbers, e-mails

def fact_scores(answer: str, groups):
    if not groups:
        return None, None
    a, s = norm(answer), squash(answer)
    hits = [any(fact_present(f, a, s) for f in g) for g in groups]
    return sum(hits) / len(hits), all(hits)

def token_f1(pred: str, ref: str) -> float:
    p, r = tokens(pred), tokens(ref)
    if not p or not r:
        return 0.0
    common = sum((Counter(p) & Counter(r)).values())
    if common == 0:
        return 0.0
    prec, rec = common / len(p), common / len(r)
    return 2 * prec * rec / (prec + rec)

def is_refusal(answer: str) -> bool:
    a = norm(answer)
    return any(re.search(p, a) for p in REFUSAL_PATTERNS)

def norm_url(u: str) -> str:
    u = re.sub(r"^https?://(www\.)?", "", u.strip().lower())
    u = re.sub(r"\.php$", "", u).rstrip("/")
    u = re.sub(r"/index$", "", u)
    return u

def source_rank(sources, expected):
    """1-based rank of first source matching any expected page, else None."""
    exp = [norm_url(e) for e in expected]
    for i, s in enumerate(sources, 1):
        url = norm_url(s.get("url", "") if isinstance(s, dict) else str(s))
        if any(url.endswith(e) for e in exp):
            return i
    return None

# ------------------------------------------------------------------ backends

class LocalBackend:
    def __init__(self, project: Path):
        sys.path.insert(0, str(project.resolve()))
        import importlib
        self.rag = importlib.import_module("rag")
        self.config = importlib.import_module("config")
        if not self.rag.index.load():
            sys.exit("Knowledge base not found in <project>/data. Run `python ingest.py` first.")
        self.rag.FAQ_DELAY = (0, 0)                        # no artificial delay while testing

    def ask(self, q):
        return self.rag.answer(q, [])

class HttpBackend:
    def __init__(self, url):
        import requests
        self.requests, self.url = requests, url.rstrip("/")

    def ask(self, q):
        r = self.requests.post(f"{self.url}/api/chat", json={"message": q, "history": []}, timeout=120)
        r.raise_for_status()
        return r.json()

class MockBackend:
    """Dry run: echoes the reference answer so you can check the pipeline end-to-end."""
    def __init__(self, data):
        self.ref = {d["question"]: d for d in data}

    def ask(self, q):
        d = self.ref[q]
        if d["should_abstain"]:
            return {"answer": "I could not find that on the AIDE website. Please see the Contacts page.", "sources": []}
        return {"answer": d["reference_answer"],
                "sources": [{"title": "mock", "url": "https://mnit.ac.in/" + d["expected_sources"][0]}]}

# ------------------------------------------------------------------ semantic similarity

def tfidf_cosine(pairs):
    docs = [tokens(a) + tokens(b) for a, b in pairs]
    df = Counter(w for d in docs for w in set(d))
    n = len(docs)
    idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}
    out = []
    for a, b in pairs:
        va, vb = Counter(tokens(a)), Counter(tokens(b))
        wa = {w: c * idf.get(w, 1) for w, c in va.items()}
        wb = {w: c * idf.get(w, 1) for w, c in vb.items()}
        dot = sum(wa[w] * wb.get(w, 0) for w in wa)
        na = math.sqrt(sum(v * v for v in wa.values()))
        nb = math.sqrt(sum(v * v for v in wb.values()))
        out.append(dot / (na * nb) if na and nb else 0.0)
    return out

def gemini_cosine(rag, pairs):
    import numpy as np
    sims = []
    for i in range(0, len(pairs), 8):
        batch = pairs[i:i + 8]
        texts = [norm(t)[:3000] for p in batch for t in p]
        vecs = rag.embed_texts(texts, "SEMANTIC_SIMILARITY")   # already L2-normalised
        for j in range(len(batch)):
            sims.append(float(np.dot(vecs[2 * j], vecs[2 * j + 1])))
    return sims

# ------------------------------------------------------------------ optional LLM judge

JUDGE_PROMPT = """You are grading a chatbot answer against a reference.
QUESTION: {q}
REFERENCE ANSWER: {ref}
CHATBOT ANSWER: {ans}

Reply with ONLY compact JSON: {{"correct": 0 or 1, "hallucination": 0 or 1}}
correct=1 if the chatbot answer contains the key facts of the reference and does not contradict it.
hallucination=1 if the chatbot answer states specific names, numbers, dates or links that are NOT
supported by the reference or look invented. For out-of-scope questions, correct=1 only if the bot
says it cannot find the information."""

def llm_judge(rag, config, q, ref, ans):
    from google.genai import types
    for attempt in range(4):
        try:
            r = rag.get_client().models.generate_content(
                model=config.CHAT_MODELS[0],
                contents=JUDGE_PROMPT.format(q=q, ref=ref, ans=ans[:3000]),
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=100))
            m = re.search(r"\{.*?\}", r.text or "", re.S)
            return json.loads(m.group(0))
        except Exception as e:                              # noqa: BLE001
            time.sleep(5 * (attempt + 1))
    return {"correct": None, "hallucination": None}

# ------------------------------------------------------------------ main

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")

def pct(x):
    return "n/a" if x != x else f"{100 * x:.1f}%"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["local", "http", "mock"], default="local")
    ap.add_argument("--project", default=str(HERE / "mnit-aide-chatbot"), help="chatbot folder (local backend)")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="server URL (http backend)")
    ap.add_argument("--dataset", default=str(HERE / "qa_dataset.json"))
    ap.add_argument("--out", default=str(HERE / "eval_results"))
    ap.add_argument("--sleep", type=float, default=4.0, help="seconds between questions (free-tier rate limits)")
    ap.add_argument("--semantic", choices=["gemini", "tfidf"], default=None)
    ap.add_argument("--judge", action="store_true", help="add LLM-as-judge correctness/hallucination (needs local backend)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--fresh", action="store_true", help="ignore cached answers")
    args = ap.parse_args()

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))[: args.limit]
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    cache_f = out / "raw_answers.jsonl"
    cache = {}
    if cache_f.exists() and not args.fresh and args.backend != "mock":
        for line in cache_f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line); cache[r["id"]] = r
    elif cache_f.exists():
        cache_f.unlink()

    backend = (LocalBackend(Path(args.project)) if args.backend == "local"
               else HttpBackend(args.url) if args.backend == "http" else MockBackend(data))
    rag = getattr(backend, "rag", None)
    if args.judge and rag is None:
        sys.exit("--judge needs --backend local")

    # ---- 1) collect answers
    for i, d in enumerate(data, 1):
        if d["id"] in cache:
            continue
        t0, err, resp = time.time(), None, {}
        for attempt in range(3):
            try:
                resp = backend.ask(d["question"]); err = None; break
            except Exception as e:                          # noqa: BLE001
                err = str(e); time.sleep(10 * (attempt + 1))
        rec = {"id": d["id"], "answer": resp.get("answer", ""), "sources": resp.get("sources", []),
               "latency": time.time() - t0, "error": err}
        cache[d["id"]] = rec
        with cache_f.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{i:02d}/{len(data)}] {d['id']} {rec['latency']:.1f}s {'ERROR ' + err[:60] if err else 'ok'}")
        if args.backend != "mock":
            time.sleep(args.sleep)

    # ---- 2) semantic similarity
    pairs = [(cache[d["id"]]["answer"], d["reference_answer"]) for d in data]
    mode = args.semantic or ("gemini" if rag else "tfidf")
    try:
        sims = gemini_cosine(rag, pairs) if mode == "gemini" else tfidf_cosine(pairs)
    except Exception as e:                                  # noqa: BLE001
        print(f"Gemini similarity failed ({str(e)[:80]}); falling back to TF-IDF"); mode = "tfidf"
        sims = tfidf_cosine(pairs)

    # ---- 3) per-question scoring
    rows = []
    for d, sim in zip(data, sims):
        r = cache[d["id"]]
        ans = r["answer"]
        recall, strict = fact_scores(ans, d["required_facts"])
        rank = source_rank(r["sources"], d["expected_sources"]) if d["expected_sources"] else None
        row = dict(id=d["id"], category=d["category"], question=d["question"], should_abstain=d["should_abstain"],
                   fact_recall=recall, fact_strict=strict, token_f1=token_f1(ans, d["reference_answer"]),
                   semantic_sim=sim, source_rank=rank, refused=is_refusal(ans), latency=r["latency"],
                   error=r["error"], answer=ans)
        if args.judge:
            j = llm_judge(rag, backend.config, d["question"], d["reference_answer"], ans)
            row["judge_correct"], row["judge_halluc"] = j.get("correct"), j.get("hallucination")
            time.sleep(args.sleep)
        rows.append(row)

    ans_rows = [r for r in rows if not r["should_abstain"]]
    oos_rows = [r for r in rows if r["should_abstain"]]
    src_rows = [r for r in ans_rows if r["id"] in {d["id"] for d in data if d["expected_sources"]}]

    summary = {
        "n_questions": len(rows), "n_answerable": len(ans_rows), "n_out_of_scope": len(oos_rows),
        "1_key_fact_accuracy_strict": mean([r["fact_strict"] for r in ans_rows]),
        "1_key_fact_recall_mean": mean([r["fact_recall"] for r in ans_rows]),
        "2_token_f1": mean([r["token_f1"] for r in ans_rows]),
        f"3_semantic_similarity_{mode}": mean([r["semantic_sim"] for r in ans_rows]),
        "4_source_hit_at_5": mean([r["source_rank"] is not None for r in src_rows]),
        "4_source_mrr": mean([1 / r["source_rank"] if r["source_rank"] else 0 for r in src_rows]),
        "5_correct_refusal_rate": mean([r["refused"] for r in oos_rows]),
        # refusal on an answerable question only counts if the answer is also wrong
        "5_false_refusal_rate": mean([r["refused"] and not r["fact_strict"] for r in ans_rows]),
        "latency_mean_s": mean([r["latency"] for r in rows]),
        "latency_p95_s": sorted(r["latency"] for r in rows)[max(0, int(0.95 * len(rows)) - 1)],
        "errors": sum(1 for r in rows if r["error"]),
    }
    if args.judge:
        summary["6_llm_judge_correct"] = mean([r["judge_correct"] for r in rows])
        summary["6_llm_judge_hallucination"] = mean([r["judge_halluc"] for r in rows])

    by_cat = defaultdict(list)
    for r in ans_rows:
        by_cat[r["category"]].append(r)
    cat_table = {c: {"n": len(v), "strict_acc": mean([x["fact_strict"] for x in v]),
                     "recall": mean([x["fact_recall"] for x in v]), "f1": mean([x["token_f1"] for x in v])}
                 for c, v in sorted(by_cat.items())}

    # ---- 4) write outputs
    import csv
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    (out / "report.json").write_text(json.dumps({"summary": summary, "by_category": cat_table}, indent=2), encoding="utf-8")

    lines = ["# MNIT AIDE chatbot - evaluation report", "",
             f"Questions: {summary['n_questions']} ({summary['n_answerable']} answerable, {summary['n_out_of_scope']} out-of-scope)", "",
             "| # | Metric | Score |", "|---|--------|-------|",
             f"| 1 | Key-Fact Accuracy (all facts present) | {pct(summary['1_key_fact_accuracy_strict'])} |",
             f"| 1 | Key-Fact Recall (mean) | {pct(summary['1_key_fact_recall_mean'])} |",
             f"| 2 | Token F1 vs reference | {summary['2_token_f1']:.3f} |",
             f"| 3 | Semantic similarity ({mode}) | {summary[f'3_semantic_similarity_{mode}']:.3f} |",
             f"| 4 | Source Hit@5 | {pct(summary['4_source_hit_at_5'])} |",
             f"| 4 | Source MRR | {summary['4_source_mrr']:.3f} |",
             f"| 5 | Correct refusal rate (out-of-scope) | {pct(summary['5_correct_refusal_rate'])} |",
             f"| 5 | False refusal rate (answerable) | {pct(summary['5_false_refusal_rate'])} |",
             f"| - | Latency mean / p95 | {summary['latency_mean_s']:.1f}s / {summary['latency_p95_s']:.1f}s |"]
    if args.judge:
        lines += [f"| 6 | LLM-judge correctness | {pct(summary['6_llm_judge_correct'])} |",
                  f"| 6 | LLM-judge hallucination rate | {pct(summary['6_llm_judge_hallucination'])} |"]
    lines += ["", "## By category (answerable questions)", "", "| Category | n | Strict acc | Fact recall | Token F1 |", "|---|---|---|---|---|"]
    for c, v in cat_table.items():
        lines.append(f"| {c} | {v['n']} | {pct(v['strict_acc'])} | {pct(v['recall'])} | {v['f1']:.3f} |")
    failed = [r for r in ans_rows if not r["fact_strict"]] + [r for r in oos_rows if not r["refused"]]
    lines += ["", f"## Failures ({len(failed)})", ""]
    for r in failed:
        kind = "should have refused" if r["should_abstain"] else f"fact recall {pct(r['fact_recall'])}"
        lines.append(f"- **{r['id']}** [{r['category']}] {r['question']} - {kind}")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "\n".join(lines[:22]))
    print(f"\nSaved: {out/'report.md'}, {out/'report.json'}, {out/'results.csv'}")

if __name__ == "__main__":
    main()
