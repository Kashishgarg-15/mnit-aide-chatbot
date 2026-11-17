# MNIT AIDE chatbot - evaluation report

Questions: 50 (46 answerable, 4 out-of-scope)

| # | Metric | Score |
|---|--------|-------|
| 1 | Key-Fact Accuracy (all facts present) | 78.3% |
| 1 | Key-Fact Recall (mean) | 78.3% |
| 2 | Token F1 vs reference | 0.398 |
| 3 | Semantic similarity (tfidf) | 0.488 |
| 4 | Source Hit@5 | 78.3% |
| 4 | Source MRR | 0.674 |
| 5 | Correct refusal rate (out-of-scope) | 100.0% |
| 5 | False refusal rate (answerable) | 4.3% |
| - | Latency mean / p95 | 13.9s / 63.6s |

## By category (answerable questions)

| Category | n | Strict acc | Fact recall | Token F1 |
|---|---|---|---|---|
| about | 5 | 100.0% | 100.0% | 0.644 |
| academics | 4 | 50.0% | 50.0% | 0.301 |
| awards | 3 | 100.0% | 100.0% | 0.665 |
| committee | 4 | 100.0% | 100.0% | 0.441 |
| contacts | 4 | 0.0% | 0.0% | 0.000 |
| events | 3 | 100.0% | 100.0% | 0.417 |
| facilities | 4 | 75.0% | 75.0% | 0.320 |
| gallery | 2 | 50.0% | 50.0% | 0.474 |
| patents | 3 | 66.7% | 66.7% | 0.375 |
| people | 4 | 100.0% | 100.0% | 0.553 |
| projects | 3 | 100.0% | 100.0% | 0.265 |
| publications | 2 | 50.0% | 50.0% | 0.272 |
| syllabus | 5 | 100.0% | 100.0% | 0.397 |

## Failures (10)

- **Q06** [contacts] Who is the Head of the AIDE department? - fact recall 0.0%
- **Q07** [contacts] What is the official email address of the AIDE Head of Department? - fact recall 0.0%
- **Q08** [contacts] What is the postal address of the AIDE department? - fact recall 0.0%
- **Q09** [contacts] What is the phone number of the AIDE HoD office? - fact recall 0.0%
- **Q10** [academics] Where can I find the scheme and syllabus for the B.Tech AIDE programme? - fact recall 0.0%
- **Q11** [academics] Is the time-table for AIDE available on the website? - fact recall 0.0%
- **Q19** [facilities] What laboratory facility does the AIDE department have? - fact recall 0.0%
- **Q29** [patents] What is the registration number of the patent on identifying and clustering human joints for smart physiotherapy? - fact recall 0.0%
- **Q39** [gallery] When was the inaugural ceremony of the AIDE department held? - fact recall 0.0%
- **Q46** [publications] How many conference publications are listed on the AIDE website? - fact recall 0.0%