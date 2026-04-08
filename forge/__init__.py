"""Specora Forge — the contract compiler and code generation engine.

The Forge is the heart of Specora Core. It takes declarative contracts
(YAML files with the .contract.yaml extension) and compiles them through
a pipeline:

    Contracts -> Parser -> Validator -> Dependency Graph -> IR -> Generators -> Code

Key modules:
    forge.parser    — Load, validate, and resolve contract dependencies
    forge.ir        — Intermediate Representation (target-agnostic)
    forge.targets   — Pluggable code generators (FastAPI, TypeScript, PostgreSQL, etc.)
    forge.diff      — Contract diff tracking (every mutation is recorded)
    forge.cli       — The `specora` command-line interface
"""

__version__ = "0.1.0"
