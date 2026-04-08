# Contributing to Specora Core

Thanks for your interest in Specora Core. This document explains how to report bugs, suggest features, and submit code.

## Reporting Bugs

Open a [GitHub issue](https://github.com/specora/specora-core/issues/new?template=bug_report.md) with:

- What you expected to happen
- What actually happened
- Steps to reproduce
- Your Python version and OS
- The contract YAML that triggered the bug (if applicable)

## Suggesting Features

Open a [feature request](https://github.com/specora/specora-core/issues/new?template=feature_request.md). Describe the problem you're trying to solve, not just the solution you want. If the feature involves a new contract kind or generator target, sketch out what the contract YAML would look like.

## Development Setup

```bash
git clone https://github.com/specora/specora-core.git
cd specora-core
pip install -e ".[all]"
pytest
```

This installs all dependencies (dev, LLM, healer) and runs the test suite.

## Code Style

- **Formatter/linter**: [ruff](https://docs.astral.sh/ruff/). Run `ruff check .` and `ruff format .` before committing.
- **Line length**: 100 characters.
- **Target**: Python 3.10+.
- **Never hand-edit generated code.** The `runtime/` directory is disposable output. Change the contract, regenerate.

## The Contract-First Rule

This is the most important rule in the project:

**No code without a contract.**

- Adding a new entity? Write the `.contract.yaml` first.
- Adding a new generator target? Define the output spec first.
- Fixing a bug? Update the contract that should have prevented it.

If your PR includes implementation code but no contract change, explain why in the PR description.

## Pull Request Process

1. Fork the repository.
2. Create a feature branch from `master`:
   ```bash
   git checkout -b feat/your-feature master
   ```
3. Make your changes. Write tests. Run the suite:
   ```bash
   pytest
   ruff check .
   ```
4. Commit with a clear message. We use conventional commits:
   - `feat:` new feature
   - `fix:` bug fix
   - `docs:` documentation
   - `refactor:` code restructuring
   - `test:` test additions or fixes
5. Push to your fork and open a PR against `master`.
6. Describe what the PR does and link any related issues.

## Understanding the Architecture

Read [CLAUDE.md](CLAUDE.md) for the full architecture reference, contract language specification, project structure, and build rules. It is the operating manual for the entire system.

## Questions?

Open a discussion or issue. We are happy to help.
