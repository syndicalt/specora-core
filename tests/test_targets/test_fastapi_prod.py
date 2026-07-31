"""Tests for the production FastAPI generator."""

import pytest

from forge.ir.model import (
    DomainIR,
    EndpointIR,
    EntityIR,
    FieldIR,
    GuardIR,
    InfraIR,
    RouteIR,
    StateIR,
    StateMachineIR,
)


@pytest.fixture
def empty_ir() -> DomainIR:
    return DomainIR(domain="test")


class TestGenConfig:
    def test_generates_config(self, empty_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_config import generate_config

        result = generate_config(empty_ir)
        assert result.path == "backend/config.py"
        assert "DATABASE_URL" in result.content
        assert "DATABASE_BACKEND" in result.content
        assert "AUTH_ENABLED" in result.content
        assert "CORS_ORIGINS" in result.content
        assert "@generated" in result.content


@pytest.fixture
def task_entity() -> EntityIR:
    return EntityIR(
        fqn="entity/test/task",
        name="task",
        domain="test",
        description="A task",
        table_name="tasks",
        fields=[
            FieldIR(name="title", type="string", required=True),
            FieldIR(
                name="priority", type="string", required=True, enum_values=["high", "medium", "low"]
            ),
            FieldIR(name="assigned_to", type="string"),
            FieldIR(name="id", type="uuid", computed="uuid"),
            FieldIR(name="created_at", type="datetime", computed="now"),
            FieldIR(name="updated_at", type="datetime", computed="now_on_update"),
        ],
    )


@pytest.fixture
def task_ir(task_entity: EntityIR) -> DomainIR:
    return DomainIR(domain="test", entities=[task_entity])


class TestGenRepositories:
    def test_generates_base_repository(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories

        files = generate_repositories(task_ir)
        base = next(f for f in files if "base.py" in f.path)
        assert "class TaskRepository" in base.content
        assert "async def list" in base.content
        assert "async def get" in base.content
        assert "async def create" in base.content
        assert "async def update" in base.content
        assert "async def delete" in base.content

    def test_generates_memory_adapter(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories

        files = generate_repositories(task_ir)
        mem = next(f for f in files if "memory.py" in f.path)
        assert "class MemoryTaskRepository" in mem.content
        assert "_store" in mem.content

    def test_generates_postgres_adapter(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories

        files = generate_repositories(task_ir)
        pg = next(f for f in files if "postgres.py" in f.path)
        assert "class PostgresTaskRepository" in pg.content
        assert "asyncpg" in pg.content or "SELECT" in pg.content

    def test_generates_provider_factory(self, task_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories

        files = generate_repositories(task_ir)
        base = next(f for f in files if "base.py" in f.path)
        assert "def get_task_repo" in base.content


@pytest.fixture
def task_route() -> RouteIR:
    return RouteIR(
        fqn="route/test/tasks",
        name="tasks",
        domain="test",
        entity_fqn="entity/test/task",
        base_path="/tasks",
        endpoints=[
            EndpointIR(
                method="POST",
                path="/",
                response_status=201,
                auto_fields={"id": "uuid", "created_at": "now"},
            ),
            EndpointIR(method="GET", path="/", response_status=200),
            EndpointIR(method="GET", path="/{id}", response_status=200),
            EndpointIR(method="PATCH", path="/{id}", response_status=200),
            EndpointIR(method="DELETE", path="/{id}", response_status=204),
        ],
    )


@pytest.fixture
def task_route_ir(task_entity: EntityIR, task_route: RouteIR) -> DomainIR:
    return DomainIR(domain="test", entities=[task_entity], routes=[task_route])


@pytest.fixture
def task_with_state_machine(task_entity: EntityIR) -> EntityIR:
    sm = StateMachineIR(
        fqn="workflow/test/task_lifecycle",
        initial="new",
        states=[
            StateIR(name="new", label="New"),
            StateIR(name="in_progress", label="In Progress"),
            StateIR(name="done", label="Done", terminal=True),
        ],
        transitions={"new": ["in_progress"], "in_progress": ["done"]},
        guards=[GuardIR(from_state="new", to_state="in_progress", require_fields=["assigned_to"])],
    )
    return task_entity.model_copy(update={"state_machine": sm})


@pytest.fixture
def auth_infra() -> InfraIR:
    return InfraIR(
        fqn="infra/test/auth",
        name="auth",
        domain="test",
        category="auth",
        config={
            "provider": "jwt",
            "roles": ["admin", "agent", "customer"],
            "protected_routes": [
                {
                    "path": "/tasks",
                    "methods": ["POST", "PATCH", "DELETE"],
                    "roles": ["admin", "agent"],
                },
            ],
        },
    )


class TestGenTests:
    def test_empty_ir_returns_empty(self, empty_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        result = generate_tests(empty_ir)
        assert result == []

    def test_generates_conftest(self, task_route_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        files = generate_tests(task_route_ir)
        conftest = next(f for f in files if "conftest.py" in f.path)
        assert conftest.path == "backend/tests/conftest.py"
        assert "TestClient" in conftest.content
        assert "def client" in conftest.content
        assert "DATABASE_BACKEND" in conftest.content
        assert "requires_pipeline" in conftest.content

    def test_generates_entity_test_file(self, task_route_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        files = generate_tests(task_route_ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert test_file.path == "backend/tests/test_task.py"
        assert "def test_create_task" in test_file.content
        assert "def test_create_task_missing_fields" in test_file.content
        assert "def test_list_tasks" in test_file.content
        assert "def test_get_task" in test_file.content
        assert "def test_get_task_not_found" in test_file.content
        assert "def test_update_task" in test_file.content
        assert "def test_update_task_not_found" in test_file.content
        assert "def test_delete_task" in test_file.content
        assert "def test_delete_task_not_found" in test_file.content

    def test_generates_valid_payload(self, task_route_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        files = generate_tests(task_route_ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert "VALID_PAYLOAD" in test_file.content
        assert '"title"' in test_file.content
        assert '"priority"' in test_file.content

    def test_generates_auth_helpers(
        self, task_entity: EntityIR, task_route: RouteIR, auth_infra: InfraIR
    ) -> None:
        """Tokens come from the application's own issuer, not from the fixture.

        A hand-assembled token cannot satisfy the provider's required
        typ/iat/iss/aud claims, so a conftest that mints its own gets a 401 on
        every request and the generated suite tests nothing at all.
        """
        from forge.targets.fastapi_prod.gen_config import MIN_SECRET_LENGTH
        from forge.targets.fastapi_prod.gen_tests import TEST_AUTH_SECRET, generate_tests

        ir = DomainIR(
            domain="test", entities=[task_entity], routes=[task_route], infra=[auth_infra]
        )
        files = generate_tests(ir)
        conftest = next(f for f in files if "conftest.py" in f.path)
        assert "from backend.auth.interface import AuthUser" in conftest.content
        assert "from backend.auth.jwt_provider import JWTAuthProvider" in conftest.content
        assert "JWTAuthProvider().issue_tokens(" in conftest.content
        assert "def make_auth_headers(role: str) -> dict:" in conftest.content
        assert "def admin_headers():" in conftest.content
        # python-jose is no longer a generated dependency, so nothing may import it.
        assert "jose" not in conftest.content
        # backend.config refuses to boot on a shorter secret than this.
        assert len(TEST_AUTH_SECRET) >= MIN_SECRET_LENGTH
        assert f'os.environ.setdefault("AUTH_SECRET", "{TEST_AUTH_SECRET}")' in conftest.content

    def test_conftest_isolates_the_memory_store(self, task_route_ir: DomainIR) -> None:
        """Without an autouse reset the generated suite is order-dependent.

        The in-memory stores are process-global, so rows accumulate across test
        functions and a count assertion passes alone and fails in the suite.
        """
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        files = generate_tests(task_route_ir)
        conftest = next(f for f in files if "conftest.py" in f.path)
        assert "from backend.repositories.memory import reset_stores" in conftest.content
        assert "@pytest.fixture(autouse=True)" in conftest.content
        assert "    reset_stores()" in conftest.content

    def test_generates_auth_tests(
        self, task_entity: EntityIR, task_route: RouteIR, auth_infra: InfraIR
    ) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        ir = DomainIR(
            domain="test", entities=[task_entity], routes=[task_route], infra=[auth_infra]
        )
        files = generate_tests(ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert "def test_create_task_unauthenticated(client):" in test_file.content
        assert "def test_create_task_wrong_role(client, auth_headers):" in test_file.content
        assert "    assert resp.status_code == 401" in test_file.content
        assert "    assert resp.status_code == 403" in test_file.content
        # The headers come from the conftest fixture, so a test module never
        # imports conftest and never loads the app a second time.
        assert 'headers=auth_headers("admin")' in test_file.content

    def test_generates_state_machine_tests(self, task_with_state_machine: EntityIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        route = RouteIR(
            fqn="route/test/tasks",
            name="tasks",
            domain="test",
            entity_fqn="entity/test/task",
            base_path="/tasks",
            endpoints=[
                EndpointIR(
                    method="POST", path="/", response_status=201, auto_fields={"id": "uuid"}
                ),
                EndpointIR(method="PUT", path="/{id}/state", response_status=200),
            ],
        )
        ir = DomainIR(domain="test", entities=[task_with_state_machine], routes=[route])
        files = generate_tests(ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert "def test_transition_task" in test_file.content
        assert "def test_transition_task_invalid" in test_file.content
        assert "def test_transition_task_missing_state" in test_file.content
        assert "def test_transition_task_new_to_in_progress_guard" in test_file.content
        assert "xfail" not in test_file.content
        # The guard is provoked by creating without the field it requires.
        assert 'omitted = ("assigned_to",)' in test_file.content
        # The three refusal causes must be asserted apart, not as "some 4xx".
        assert "def test_transition_task_not_found" in test_file.content
        assert (
            '    assert resp.json()["detail"]["error"] == "invalid_transition"' in test_file.content
        )
        assert '    assert resp.json()["detail"]["error"] == "guard_failed"' in test_file.content
        assert '    assert resp.json()["detail"]["error"] == "not_found"' in test_file.content
        assert "    assert resp.status_code == 409" in test_file.content

    def test_state_is_not_client_writable(self, task_with_state_machine: EntityIR) -> None:
        """A create or update carrying `state` must be refused, and asserted to be.

        Writing `state` through the ordinary endpoints bypassed every transition
        and guard in the workflow contract.
        """
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        route = RouteIR(
            fqn="route/test/tasks",
            name="tasks",
            domain="test",
            entity_fqn="entity/test/task",
            base_path="/tasks",
            endpoints=[
                EndpointIR(
                    method="POST", path="/", response_status=201, auto_fields={"id": "uuid"}
                ),
                EndpointIR(method="PATCH", path="/{id}", response_status=200),
                EndpointIR(method="PUT", path="/{id}/state", response_status=200),
            ],
        )
        ir = DomainIR(domain="test", entities=[task_with_state_machine], routes=[route])
        files = generate_tests(ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert '"state"' not in test_file.content.split("def _create_task")[0]
        assert "def test_create_task_cannot_set_state" in test_file.content
        assert "def test_update_task_cannot_set_state" in test_file.content

    def test_sensitive_fields_asserted_absent_from_responses(
        self, task_entity: EntityIR, task_route: RouteIR
    ) -> None:
        """A write-only value must be proven never to appear in a response body."""
        from forge.targets.fastapi_prod.gen_tests import SENSITIVE_SENTINEL, generate_tests

        secret = FieldIR(name="secret_token", type="string", required=True, sensitive=True)
        entity = task_entity.model_copy(update={"fields": [*task_entity.fields, secret]})
        ir = DomainIR(domain="test", entities=[entity], routes=[task_route])
        files = generate_tests(ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert "def test_task_write_only_fields_never_disclosed" in test_file.content
        assert f'SENSITIVE_SENTINEL = "{SENSITIVE_SENTINEL}"' in test_file.content
        assert '"secret_token": SENSITIVE_SENTINEL' in test_file.content
        assert "    assert SENSITIVE_SENTINEL not in created.text" in test_file.content
        assert "    assert SENSITIVE_SENTINEL not in page.text" in test_file.content

    def test_list_tests_assert_keyset_page_not_total(self, task_route_ir: DomainIR) -> None:
        """The list response is {items, next_cursor}; there is no `total`."""
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        files = generate_tests(task_route_ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert '"total"' not in test_file.content
        assert '    assert body["next_cursor"] is None' in test_file.content
        assert "def test_list_tasks_paginates" in test_file.content
        # `limit` is bounded, so the old unbounded-page request is now a 422.
        assert 'params={"limit": 999999999}' in test_file.content
        assert "    assert huge.status_code == 422" in test_file.content

    def test_state_machine_guards_generated_in_repositories(
        self, task_with_state_machine: EntityIR
    ) -> None:
        from forge.targets.fastapi_prod.gen_repositories import generate_repositories

        ir = DomainIR(domain="test", entities=[task_with_state_machine])
        files = generate_repositories(ir)
        memory = next(f for f in files if "memory.py" in f.path)
        postgres = next(f for f in files if "postgres.py" in f.path)

        assert "transition_guards = {('new', 'in_progress'): ['assigned_to']}" in memory.content
        assert "record.get(field) in (None, '', [], {})" in memory.content
        assert "transition_guards = {('new', 'in_progress'): ['assigned_to']}" in postgres.content
        assert "record = dict(row)" in postgres.content

    def test_generates_pipeline_marker(self, task_entity: EntityIR, task_route: RouteIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        entity_with_hooks = task_entity.model_copy(
            update={"ai_hooks": {"on_create": ["agent/test/classifier"]}}
        )
        ir = DomainIR(domain="test", entities=[entity_with_hooks], routes=[task_route])
        files = generate_tests(ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert "@pytest.mark.requires_pipeline" in test_file.content

    def test_no_pipeline_marker_without_hooks(self, task_route_ir: DomainIR) -> None:
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        files = generate_tests(task_route_ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert "@pytest.mark.requires_pipeline" not in test_file.content

    def test_generates_per_endpoint_role_auth(
        self, task_entity: EntityIR, auth_infra: InfraIR
    ) -> None:
        """Endpoints with roles use their own role for auth test headers."""
        from forge.targets.fastapi_prod.gen_tests import generate_tests

        route = RouteIR(
            fqn="route/test/tasks",
            name="tasks",
            domain="test",
            entity_fqn="entity/test/task",
            base_path="/tasks",
            endpoints=[
                EndpointIR(
                    method="POST",
                    path="/",
                    response_status=201,
                    auto_fields={"id": "uuid"},
                    roles=["admin"],
                ),
                EndpointIR(method="GET", path="/", response_status=200, roles=["admin", "agent"]),
            ],
        )
        ir = DomainIR(domain="test", entities=[task_entity], routes=[route], infra=[auth_infra])
        files = generate_tests(ir)
        test_file = next(f for f in files if "test_task.py" in f.path)
        assert 'headers=auth_headers("admin")' in test_file.content
        # POST is admin-only, so its 403 test presents 'agent'; GET permits
        # admin and agent, so its 403 test has to reach past both.
        assert (
            '    resp = client.post("/tasks/", json={}, headers=auth_headers("agent"))'
            in test_file.content
        )
        assert (
            '    resp = client.get("/tasks/", headers=auth_headers("customer"))'
            in test_file.content
        )


class TestGenRoutes:
    def test_endpoint_with_roles_generates_require_role(
        self, task_entity: EntityIR, auth_infra: InfraIR
    ) -> None:
        """Endpoints with roles should generate require_role dependency."""
        from forge.targets.fastapi_prod.gen_routes import generate_routes

        route = RouteIR(
            fqn="route/test/tasks",
            name="tasks",
            domain="test",
            entity_fqn="entity/test/task",
            base_path="/tasks",
            endpoints=[
                EndpointIR(
                    method="DELETE", path="/{id}", response_status=204, roles=["admin", "owner"]
                ),
            ],
        )
        ir = DomainIR(domain="test", entities=[task_entity], routes=[route], infra=[auth_infra])
        files = generate_routes(ir)
        content = files[0].content
        assert 'require_role("admin", "owner")' in content

    def test_endpoint_without_roles_generates_require_auth(
        self, task_entity: EntityIR, auth_infra: InfraIR
    ) -> None:
        """Endpoints without roles should fall back to require_auth."""
        from forge.targets.fastapi_prod.gen_routes import generate_routes

        route = RouteIR(
            fqn="route/test/tasks",
            name="tasks",
            domain="test",
            entity_fqn="entity/test/task",
            base_path="/tasks",
            endpoints=[
                EndpointIR(method="GET", path="/", response_status=200),
            ],
        )
        ir = DomainIR(domain="test", entities=[task_entity], routes=[route], infra=[auth_infra])
        files = generate_routes(ir)
        content = files[0].content
        assert "Depends(require_auth)" in content
        assert "Depends(require_role" not in content

    def test_endpoint_without_auth_generates_no_dependency(self, task_route_ir: DomainIR) -> None:
        """Without auth infra, no auth dependency is generated."""
        from forge.targets.fastapi_prod.gen_routes import generate_routes

        files = generate_routes(task_route_ir)
        content = files[0].content
        assert "require_auth" not in content
        assert "require_role" not in content
