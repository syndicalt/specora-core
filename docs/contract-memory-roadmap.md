# Contract Memory Roadmap

Specora's core thesis is that contracts are durable software memory. This roadmap
focuses on making that memory enforceable, traceable, and executable across the
Forge, Factory, Healer, and generated-runtime loop.

## 1. Enforce Semantic Validity Before Generation

Status: implemented.

Goal: `Compiler.compile()` should return a semantically coherent `DomainIR` or
fail with actionable contract diagnostics.

Tasks:

- Validate semantic references after IR passes: mixins, workflows, entity
  references, route entities, page entities, workflow transitions, and guard
  fields.
- Keep JSON Schema validation focused on shape, and make semantic validation the
  compiler-owned interface for cross-contract intent.
- Add focused compiler tests for semantic failure modes.

## 2. Unify Generated Provenance

Status: implemented.

Goal: every generated file should expose provenance in one parseable format that
Healer can read reliably.

Tasks:

- Replace ad hoc generated headers with a shared provenance parser and writer.
- Update Healer's runtime tracer to read the shared format.
- Add tests that prove generated FastAPI files trace back to the source contract.

## 3. Execute Workflow Guards

Status: implemented.

Goal: workflow guards should be behavioral contracts, not descriptive metadata.

Tasks:

- Compile `StateMachineIR` guards into a reusable transition policy.
- Use the policy from route handlers and repository adapters.
- Remove xfail markers from generated guard tests once enforcement exists.

## 4. Derive Dependency Edges From Contract Semantics

Status: implemented.

Goal: dependency rules should live in one compiler-owned module instead of being
manually duplicated by authoring tools.

Tasks:

- Extract dependencies from semantic references in entity, route, page, workflow,
  agent, and infra contracts.
- Report missing declared dependencies or synthesize the full dependency graph
  deterministically.
- Simplify Factory emitters once dependency extraction is authoritative.

## 5. Promote Diffs Into Change Contracts

Status: implemented.

Goal: contract evolution should preserve not only current intent, but intent over
time.

Tasks:

- Classify contract diffs by compatibility and migration impact.
- Link diffs, generated files, migrations, and verification expectations.
- Make Healer proposals produce durable change-contract metadata.
