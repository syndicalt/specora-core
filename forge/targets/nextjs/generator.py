"""Next.js frontend generator — orchestrates all sub-generators."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile
from forge.targets.nextjs.gen_api_client import generate_api_client
from forge.targets.nextjs.gen_components import generate_components
from forge.targets.nextjs.gen_layout import generate_layout
from forge.targets.nextjs.gen_pages import generate_pages
from forge.targets.nextjs.gen_scaffold import generate_scaffold


class NextJSGenerator(BaseGenerator):
    """Generates a complete Next.js 15 frontend from domain contracts."""

    def name(self) -> str:
        return "nextjs"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        if not ir.pages:
            return []

        files: list[GeneratedFile] = []

        # Project scaffold
        files.extend(generate_scaffold(ir))

        # API client
        if ir.routes:
            files.append(generate_api_client(ir))

        # Reusable components
        files.extend(generate_components(ir))

        # App router pages
        files.extend(generate_pages(ir))

        # Layout + dashboard + Docker
        files.extend(generate_layout(ir))

        # Copy types.ts from existing TypeScript generator
        from forge.targets.typescript.gen_types import TypeScriptGenerator
        ts_gen = TypeScriptGenerator()
        ts_files = ts_gen.generate(ir)
        for f in ts_files:
            files.append(GeneratedFile(
                path=f"frontend/src/lib/{f.path}",
                content=f.content,
                provenance=f.provenance,
            ))

        return files
