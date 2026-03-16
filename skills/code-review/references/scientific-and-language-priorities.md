# Scientific And Language-Specific Priorities

Load this file when reviewing C, C++, Python, or scientific/numerical code.

## Scientific Computing

Prioritize these checks whenever the change affects models, solvers, statistics, array kernels, simulations, geometry, or data pipelines:

- Units, coordinate systems, indexing conventions, and shape assumptions stay consistent end to end.
- Boundary conditions, degeneracies, NaN/Inf handling, and invalid physical states are explicit.
- Precision choices are justified: `float` vs `double`, dtype conversions, mixed precision, accumulation error.
- Tolerances are explained and appropriate for the scale of the problem.
- Randomness is controlled where reproducibility matters: fixed seeds, documented stochastic behaviour, deterministic test mode.
- Algorithms preserve key invariants or are checked against analytical solutions, trusted baselines, or regression data.
- Performance comments consider realistic data sizes, memory footprint, and copy behaviour, not only microbenchmarks.
- Outputs remain reproducible enough for the project: same inputs, versioned dependencies, stable serialization, and traceable parameters.

## C Review Priorities

- Buffer sizes, indexing, pointer arithmetic, and string handling cannot overflow or read past bounds.
- Ownership and cleanup are correct across all paths, especially early returns and error branches.
- Integer promotions, signedness, narrowing, and overflow are safe and intentional.
- `const`, `restrict`, aliasing assumptions, and alignment-sensitive code are coherent.
- Format strings, `errno`, return codes, and system-call failures are checked correctly.
- Shared mutable state is synchronized; `volatile` is not used as a threading primitive.
- Portability assumptions across compilers, platforms, and word sizes are justified.

## C++ Review Priorities

- Ownership is explicit and lifetime-safe: RAII, smart pointers, references, `span`, and `string_view` do not outlive backing storage.
- Copy, move, and destruction semantics are correct; no accidental expensive copies or moved-from misuse.
- Containers and iterators remain valid across mutation; no invalidation bugs.
- Exceptions and `noexcept` behaviour are consistent with resource safety and API expectations.
- Template or generic code preserves type safety and does not silently change precision or performance characteristics.
- Concurrency code uses correct synchronization and avoids data races, deadlocks, and unsafe publication.

## Python Review Priorities

- Mutable defaults, hidden global state, monkey-patching, and import-time side effects are avoided or intentional.
- Exceptions are neither swallowed nor converted into ambiguous return values.
- Context managers are used for files, locks, and other resources.
- Array and dataframe operations respect shape, dtype, broadcasting, copy-vs-view semantics, and index alignment.
- Numerical tests use appropriate tolerances instead of exact float equality.
- Performance comments focus on algorithmic choices, vectorization, memory movement, and I/O, not cosmetic micro-optimizations.
- Public APIs, typing, and docstrings stay aligned with runtime behaviour.
