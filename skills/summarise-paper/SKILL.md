---
name: summarise-paper
description: Summarise a science paper from a local PDF path or URL. Use only when specifically asked.
---

## Task

Summarise the science paper at: $ARGUMENTS

## Steps

1. If `$ARGUMENTS` is a local file path, use the Read tool to read the PDF.
2. If `$ARGUMENTS` is a URL, use a WebFetch tool to retrieve the paper content.
3. Draft a structured markdown summary following the Output Format below.
4. Perform a review pass over the draft before outputting (see Review Pass below).
5. Output only the final markdown summary — no preamble, commentary, or surrounding text.
6. If the user specifies a file path, write to that file; otherwise print in the conversation.
7. When writing to file, derive the filename as **`Author(s) - Year - Title.md`** (1 author: surname only; 2 authors: `A and B`; 3+: `A et al`; title truncated to 80 chars, unsafe characters removed).

## Review Pass

Before outputting, re-read the paper and check the draft against it:

1. **Coverage** — re-read the abstract, conclusions, and all section headings; confirm no central contribution, key result, or major finding is absent from the summary; add missing content as extra bullets if needed.
2. **Accuracy** — verify each bullet against its footnote quote; reject any bullet where the paraphrase goes beyond or misrepresents what the quote actually says; rewrite or drop it.
3. **Quote fidelity** — confirm every footnote quote is verbatim (character-for-character from the paper), not a paraphrase; correct any that are not exact.
4. **Glossary completeness** — ensure all relevant glossary terms specific to the paper's context are included; add any that are missing.

Only output the summary once all four checks pass.

## Output Format

Produce a single markdown document with these sections in order.

### Header

```
# <Paper Title>

Authors: <Author list> (full list comprising full names, comma separated)

Published: <Month Year> ([Link](<URL>))
```

Only include the link if an arXiv ID or DOI is present in the paper; otherwise omit it entirely.

### Sections

Each section gets up to 3 bullet points written in your own words. Every bullet **must** carry a footnote marker (`[^1]`, `[^2]`, etc.) linking it to a verbatim supporting quote in the References section. No bullet without a footnote. Assign footnote numbers sequentially across the entire document.

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
- Every bullet has a `[^N]` footnote marker
- Every footnote contains a verbatim quote copied exactly from the paper, with a page or section reference
- Every bullet accurately follows from its supporting quote — no paraphrase that goes beyond or contradicts what the quote says, no invented detail
- No bullet is left without a footnote under any circumstance; omit a bullet entirely rather than write it without a verifiable verbatim source

### Glossary

A 2-column markdown table with headers **Term** and **Definition**, covering specialised terms, acronyms, and domain-specific concepts introduced or relied upon in the paper. No row limit.

### References

Footnote definitions in order, each containing a verbatim quote and its location:

```
[^1]: "exact words copied from the paper" (Abstract, p.1)
[^2]: "another exact quote" (Section 2.3, p.4)
```

- Quotes must be **verbatim** — copied character-for-character from the paper, enclosed in double quotation marks
- Every footnote must include a location: section name, table/figure number, or page number (or combination)
- Every `[^N]` marker used in the body must have a corresponding definition here

## Style Rules

- UK English throughout
- Render equations in LaTeX: inline `$...$` or display `$$...$$`
- No tags section
- Footnotes are mandatory — every bullet must have one; omit a bullet entirely rather than write it without a verbatim source quote
