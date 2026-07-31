"""Next.js frontend generator — orchestrates all sub-generators."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile, validate_generated_files
from forge.targets.nextjs.context import FrontendContext
from forge.targets.nextjs.gen_api_client import generate_api_client
from forge.targets.nextjs.gen_components import generate_components
from forge.targets.nextjs.gen_layout import generate_layout
from forge.targets.nextjs.gen_pages import generate_pages
from forge.targets.nextjs.gen_scaffold import generate_scaffold
from forge.targets.nextjs.gen_session import generate_session
from forge.targets.typescript.gen_types import TypeScriptGenerator


class NextJSGenerator(BaseGenerator):
    """Generates a complete Next.js 15 frontend from domain contracts."""

    def name(self) -> str:
        return "nextjs"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        if not ir.pages:
            return []

        # Every name the sub-generators share — component stems, api bindings,
        # page URLs — is resolved once, domain-aware, before anything is
        # emitted. Deriving them independently is what let two entities named
        # `account` in different domains overwrite each other's components.
        ctx = FrontendContext(ir)

        files: list[GeneratedFile] = []
        files.extend(generate_scaffold(ir))
        if ir.routes:
            files.extend(generate_api_client(ctx))
        files.extend(generate_session(ctx))
        files.extend(generate_components(ctx))
        files.extend(generate_pages(ctx))
        files.extend(generate_layout(ctx))

        # The interfaces the api client and the components are typed against.
        for f in TypeScriptGenerator().generate(ir):
            files.append(
                GeneratedFile(
                    path=f"frontend/src/lib/{f.path}",
                    content=f.content,
                    provenance=f.provenance,
                )
            )

        # Rejects two files claiming one path. A collision here means a page or
        # a component was silently dropped, which is invisible until the
        # missing screen is opened in production.
        return validate_generated_files(files)
