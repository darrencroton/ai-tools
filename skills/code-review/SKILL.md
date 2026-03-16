---
name: code-review
description: Perform a professional, comprehensive code review of completed work. Use when the user asks to review a diff, commit, branch, directory, or implementation relative to a plan, spec, ticket, or stated requirements. Default for requests like "review this change", "review my implementation", or "check this code update". Focus on line-specific findings, risk-based depth, and explicit attention to correctness, safety, tests, and scientific C/C++/Python concerns.
---

# Professional Code Review

Produce a senior-level review that is evidence-based, risk-driven, and difficult to fake with a superficial skim.

## Load Order

- Always read this file.
- Read [references/review-matrix.md](references/review-matrix.md) for the required review dimensions.
- Read [references/scientific-and-language-priorities.md](references/scientific-and-language-priorities.md) whenever the change touches C, C++, Python, numerical code, simulations, data pipelines, benchmarks, or scientific tests.

## Inputs To Identify

From the user message and local context, identify:

- **Review target**: diff, commit, branch, files, directory, or completed task
- **Requirement source**: plan, spec, ticket, issue, paper, docs, or explicit user criteria
- **Project rules**: `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING`, test/build conventions
- **Evidence available**: git diff, tests, benchmarks, logs, docs, configs, generated outputs

Resolve the target from local context when possible. Ask the user only if multiple plausible review scopes remain.

## Minimum Investigation Standard

Every review must do all of the following:

1. Inspect the actual change set, not just a summary.
2. Read the most relevant surrounding code, not only the modified lines.
3. Follow changed interfaces one hop outward:
   - caller and callee
   - data producer and consumer
   - public API and the primary tests
4. If algorithms, numerics, memory ownership, concurrency, or file formats changed, inspect related tests and invariants before concluding.
5. Review all changed files. Do not stop after the first few files or after style comments.

## Workflow

### 1. Establish scope
- Prefer `git diff`, `git show`, or a user-supplied patch.
- If there is no usable git context, review the supplied files directly and say so.
- Identify high-risk files first: core algorithms, public APIs, memory management, concurrency, I/O, configs, tests, and build files.

### 2. Establish expected behaviour
- Extract acceptance criteria from the plan/spec/ticket when present.
- If no formal spec exists, infer intended behaviour from tests, docs, code structure, and the user request.
- State important assumptions when they materially affect confidence.

### 3. Run the review matrix
- Use [references/review-matrix.md](references/review-matrix.md).
- Every review must explicitly consider correctness, failure handling, tests, and maintainability.
- Add the scientific and language-specific checks from [references/scientific-and-language-priorities.md](references/scientific-and-language-priorities.md) when relevant.

### 4. Use supporting validation when it is proportionate
- When cheap and relevant, run targeted tests, builds, linters, or static analysis to validate or falsify review hypotheses.
- For C and C++, treat compiler warnings and sanitizers as supporting evidence, not as substitutes for reasoning.
- For scientific and numerical changes, prefer targeted regression tests, reference-data checks, or benchmark comparisons when available.

### 5. Separate findings from uncertainty
- Report confirmed defects and clear risks as findings.
- Put ambiguous concerns under `Open Questions / Assumptions`, not as hard findings.
- Do not report untouched pre-existing issues unless the current change interacts with them.

### 6. Validate review depth
Before finishing, ask:

- Did I inspect all changed files?
- Did I inspect the relevant tests?
- Did I follow risky interfaces beyond the diff?
- Did I assess numerical or scientific implications if present?
- Am I reporting the highest-impact issues first?

## Severity

- `P0 Critical`: definite correctness, safety, security, data-loss, or reproducibility failure; or a stated requirement is unmet.
- `P1 High`: strong evidence of a bug or major risk that should block completion until fixed.
- `P2 Medium`: important weakness in tests, validation, maintainability, portability, or performance that should be addressed soon.
- `P3 Low`: minor clarity, documentation, or style issue. Mention only if it adds signal.

Default output should focus on `P0` to `P2`. Include `P3` only when the review is otherwise clean or the note is unusually useful.

## What Good Findings Look Like

Each finding must include:

- `file:line`
- severity
- concise title
- why it matters in runtime, scientific, or maintenance terms
- evidence path: violated requirement, failing scenario, risky control flow, missing validation, or contract break
- concrete fix direction or verification step

Bad finding: vague, speculative, or style-only without impact.

Good finding: specific, reproducible, and tied to real engineering risk.

## Required Output

Start with findings, ordered by severity.

```md
## Findings

1. [P1] `path/to/file:123` Title
   Why this is a problem, what behaviour or requirement it breaks, and the fix direction.

## Open Questions / Assumptions
- Only include items that materially affect confidence.

## Coverage Summary
- Scope reviewed: ...
- Requirements checked against: ...
- Dimensions checked: correctness, edge cases, tests, ...
- Validation run / not run: ...

## Verdict
- `FAIL` if any `P0` or unresolved `P1`
- `PASS WITH RISKS` if only `P2` or `P3` issues remain, or validation gaps remain
- `PASS` only when no material issues were found
```

If there are no findings, say so explicitly and still list residual risks or unrun validation.

## Review Priorities

Prioritize in this order unless the task clearly demands otherwise:

1. Correctness and unmet requirements
2. Memory, resource, and lifetime safety
3. Numerical or scientific validity and reproducibility
4. Concurrency and shared-state hazards
5. Error handling and recovery paths
6. Test adequacy and regression protection
7. Performance and scalability when the change touches hot paths or large data
8. Maintainability, portability, and documentation

## Avoid Shallow Reviews

- Do not rely on a single score threshold to suppress medium-risk issues.
- Do not conclude "looks good" after scanning only the diff header or happy-path tests.
- Do not treat passing tests as proof of correctness.
- Do not over-index on style when deeper risks exist.
- Do not skip scientific review dimensions just because the code compiles.

## Notes

- Plan-based review is a special case, not the whole skill.
- When the user asks for a "review", default to a review mindset even if no plan file exists.
- Reviews are primarily about findings. Summaries are secondary.
