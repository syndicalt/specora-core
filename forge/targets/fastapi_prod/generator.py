"""Production FastAPI generator — orchestrates all sub-generators."""
from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import BaseGenerator, GeneratedFile
from forge.targets.fastapi_prod.gen_app import generate_app
from forge.targets.fastapi_prod.gen_auth import generate_auth
from forge.targets.fastapi_prod.gen_config import generate_config
from forge.targets.fastapi_prod.gen_docker import generate_docker
from forge.targets.fastapi_prod.gen_models import generate_models
from forge.targets.fastapi_prod.gen_repositories import generate_repositories
from forge.targets.fastapi_prod.gen_routes import generate_routes
from forge.targets.fastapi_prod.gen_tests import generate_tests


class FastAPIProductionGenerator(BaseGenerator):
    """Production-grade FastAPI generator with repos, auth, Docker, tests."""

    def name(self) -> str:
        return "fastapi-prod"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        files: list[GeneratedFile] = []

        # Config
        files.append(generate_config(ir))

        # Models
        models = generate_models(ir)
        if models.content:
            files.append(models)

        # Repositories
        files.extend(generate_repositories(ir))

        # Auth (only if infra/auth contract exists)
        files.extend(generate_auth(ir))

        # Routes
        files.extend(generate_routes(ir))

        # App
        files.append(generate_app(ir))

        return files


class DockerGenerator(BaseGenerator):
    """Generates Docker deployment files."""

    def name(self) -> str:
        return "docker"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        return generate_docker(ir)


class TestSuiteGenerator(BaseGenerator):
    """Generates black-box pytest tests."""

    def name(self) -> str:
        return "tests"

    def generate(self, ir: DomainIR) -> list[GeneratedFile]:
        return generate_tests(ir)
