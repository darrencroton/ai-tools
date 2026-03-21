---
name: summarise-paper
description: Summarise a science paper from a local PDF path or URL. Usage: /summarise-paper <path-or-url>
---

## Task

Summarise the science paper at: $ARGUMENTS

## Steps

1. If `$ARGUMENTS` is a local file path, use the Read tool to read the PDF.
2. If `$ARGUMENTS` is a URL, use WebFetch to retrieve the paper content.
3. Generate a structured markdown summary following the Output Format below.
4. Output only the markdown summary — no preamble, commentary, or surrounding text. 
5. If the user specifies a file path, write to that file; otherwise print in the conversation.
6. When writing to file, derive the filename as **`Author(s) - Year - Title.md`** (1 author: surname only; 2 authors: `A and B`; 3+: `A et al`; title truncated to 80 chars, unsafe characters removed).

## Output Format

Produce a single markdown document with these sections in order.

### Header

```
# <Paper Title>

Authors: <Author list>

Published: <Month Year> ([Link](<URL>))
```

Only include the link if an arXiv ID or DOI is present in the paper; otherwise omit it entirely.

### Sections

Each section gets up to 3 bullet points. Every bullet **must** end with an inline citation identifying exactly where in the paper it comes from — e.g. *(Abstract)*, *(Section 2.3)*, *(Table 1)*, *(Figure 4, p.7)*. No bullet without a citation.

- **Key Ideas** — the central contributions or claims of the paper
- **Introduction** — context, motivation, and problem statement
- **Data** — datasets, observations, or inputs used
- **Method** — approach, model, algorithm, or technique
- **Results** — key quantitative or qualitative findings
- **Discussion** — interpretation, implications, and comparisons with prior work
- **Weaknesses** — limitations acknowledged by the authors or otherwise apparent
- **Conclusions** — what the authors conclude
- **Future Work** — directions proposed or implied

Omit any section that genuinely does not apply to the paper (e.g. no distinct Data section in a purely theoretical paper). Do not add a placeholder.

**Quality gate — do not return the summary until:**
- Every bullet accurately reflects the paper content (no paraphrase that changes meaning, no invented detail)
- Every bullet carries a valid inline citation traceable to a specific location in the paper
- No bullet is left uncited under any circumstance

### Glossary

A 2-column markdown table with headers **Term** and **Definition**, covering specialised terms, acronyms, and domain-specific concepts introduced or relied upon in the paper. No row limit.

## Style Rules

- UK English throughout
- Render equations in LaTeX: inline `$...$` or display `$$...$$`
- No tags section
- No footnotes or reference list at the end — all citations are inline within each bullet
- Citations are non-negotiable: every bullet must have one; omit a bullet entirely rather than write it without a traceable source
