# Review Matrix

Use this as a required checklist. Every review should explicitly consider each dimension and decide whether it is in scope.

| Dimension | What to check | High-risk signals |
| --- | --- | --- |
| Requirements and design fit | Does the change satisfy the stated task, preserve intended behaviour, and avoid unapproved scope creep? | Missing acceptance criteria, silent behaviour changes, incomplete migrations, incompatible interfaces |
| Functional correctness | Are the algorithms and control flow correct on the happy path and plausible failure paths? | Off-by-one logic, wrong branch conditions, stale state, incorrect units, missing updates to dependent code |
| Boundary conditions and invalid input | What happens with empty, null, NaN, Inf, overflow, underflow, large inputs, degenerate geometry, or malformed files? | Assumed non-empty arrays, unchecked casts, unguarded indexing, divide-by-zero, sentinel misuse |
| State, lifetime, and resources | Are ownership, cleanup, object lifetime, file handles, buffers, and external resources managed correctly? | Leaks, double free, dangling pointers/references/views, partially initialized state, missing cleanup on error paths |
| Interfaces and data contracts | Do callers and callees agree on shapes, units, dtype, ownership, mutability, ordering, and error semantics? | Contract changes without call-site updates, schema drift, implicit shape assumptions, incompatible defaults |
| Concurrency and shared state | Could threads, processes, async tasks, accelerators, or callbacks observe races or invalid ordering? | Shared mutable state, non-atomic flags, lockless caches, thread-unsafe libraries, hidden global state |
| Numerical and scientific validity | Are precision, stability, tolerances, convergence, invariants, and reference behaviour handled correctly? | Exact float comparisons, unjustified tolerances, unstable formulas, silent dtype narrowing, non-deterministic seeds |
| Performance and scalability | Is complexity acceptable for expected problem sizes and hardware? | Accidental quadratic work, repeated allocations, excessive copies, Python loops over arrays, cache-unfriendly layouts |
| Security and robustness | Could the change expose data, trust invalid input, or execute unsafe operations? | Unsafe parsing, path handling, unchecked format strings, shell injection, unsafe temp-file usage |
| Tests and validation | Do tests exercise the changed behaviour, edge cases, and regression paths with meaningful assertions? | Only happy-path tests, missing failure-path coverage, brittle snapshots, no reference-data checks, no tolerance-based checks |
| Observability and diagnosability | Are failures visible and actionable through errors, logs, metrics, or assertions? | Silent fallthrough, swallowed exceptions, ambiguous error codes, no context in failure messages |
| Portability, build, and packaging | Does the change preserve expected compiler, platform, dependency, and build behaviour? | Compiler-specific assumptions, UB exposed by optimization, missing includes, ABI breaks, undeclared dependency changes |
| Maintainability and readability | Can another engineer safely modify this later? | Hidden coupling, duplicated logic, misleading names, over-complex functions, tests that obscure intent |
| Documentation and user guidance | Were comments, docs, examples, config descriptions, and migration notes updated where behaviour changed? | New flags or outputs with no docs, stale examples, missing explanation for non-obvious numerics |

## Review Discipline

- Findings should be evidence-based. Tie each one to a scenario, requirement, or concrete risk.
- Use build, test, lint, benchmark, or sanitizer results when they strengthen confidence, but do not let tool output replace manual review of risky code paths.
- A missing test is a real finding when the change is risky enough that incorrect behaviour could ship unnoticed.
- Performance comments are only useful when connected to expected workload or a clear hot path.
- Do not waste review space on trivial style issues unless the user asked for them or they hide a deeper problem.
