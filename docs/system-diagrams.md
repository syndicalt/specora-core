# System Diagrams

Visual maps of Specora Core's architecture, broken into four linked charts. Each chart has labeled entry/exit points that connect to the others.

**Reading order:** D (Contract Origins) -> A (Forge Pipeline) -> B (Running System) -> C (Healing Loop) -> back to A.

---

## Chart D: Contract Origins

How contracts enter the system. All paths produce `.contract.yaml` files that feed into Chart A.

```mermaid
flowchart TB
    subgraph origins["Contract Origins"]
        direction TB
        hand["Hand-Written YAML"]
        factory["Factory CLI"]
        extractor["Extractor"]
    end

    subgraph factory_detail["Factory (Tier 2 — LLM-Powered)"]
        direction TB
        interview["Interview Engine<br/><i>domain, entity, workflow</i>"]
        emitters["Emitters<br/><i>emit_entity(), emit_route(),<br/>emit_page(), emit_workflow()</i>"]
        interview -->|"user answers"| emitters
    end

    subgraph extractor_detail["Extractor (Tier 4 — Reverse Engineering)"]
        direction TB
        scan["Scanner<br/><i>scan_directory()</i>"]
        analyze["Analyzers<br/><i>python_models, routes,<br/>typescript_types</i>"]
        synthesize["Synthesizer<br/><i>synthesize() → AnalysisReport</i>"]
        emit["Emitter<br/><i>emit_contracts()</i>"]
        scan -->|"FileClassification[]"| analyze
        analyze -->|"ExtractedEntity[], Route[], Workflow[]"| synthesize
        synthesize -->|"AnalysisReport"| emit
    end

    hand -->|".contract.yaml"| contracts
    factory --> factory_detail
    factory_detail -->|"YAML strings"| contracts
    extractor --> extractor_detail
    extractor_detail -->|".contract.yaml files"| contracts

    contracts[("domains/<br/>*.contract.yaml")]

    contracts -.->|"Exit → Chart A"| chart_a_entry["⬇ Forge Pipeline"]

    style chart_a_entry fill:#2d6a4f,color:#fff,stroke:none
    style contracts fill:#264653,color:#fff
```

---

## Chart A: The Forge Pipeline (Tier 1)

Deterministic compilation from contracts to generated code. Zero LLM tokens.

```mermaid
flowchart LR
    subgraph input["Input"]
        contracts[("domains/<br/>*.contract.yaml")]
    end

    subgraph forge["Forge Pipeline"]
        direction LR
        loader["Loader<br/><i>load_all_contracts()</i>"]
        validator["Validator<br/><i>validate_all()</i>"]
        dep_graph["Graph Builder<br/><i>dependency resolution</i>"]
        compiler["Compiler<br/><i>compile() → DomainIR</i>"]
        
        loader -->|"dict[FQN, contract]"| validator
        validator -->|"contracts + errors[]"| dep_graph
        dep_graph -->|"resolved deps"| compiler
    end

    subgraph ir_hub["Intermediate Representation"]
        ir["DomainIR<br/><i>entities, routes, workflows,<br/>pages, agents, mixins, infra</i>"]
    end

    subgraph generators["Generators"]
        direction TB
        fastapi["FastAPI Prod<br/><i>app, routes, models,<br/>repos, auth, tests</i>"]
        postgres["PostgreSQL<br/><i>DDL schema</i>"]
        typescript["TypeScript<br/><i>interfaces</i>"]
        docker["Docker<br/><i>Dockerfile, compose,<br/>requirements</i>"]
        nextjs["Next.js<br/><i>pages, API client</i>"]
    end

    subgraph output["Output"]
        runtime[("runtime/<br/>generated code")]
    end

    contracts -->|"Entry ← Chart D"| loader
    compiler --> ir
    ir --> fastapi & postgres & typescript & docker & nextjs
    fastapi & postgres & typescript & docker & nextjs -->|"GeneratedFile[]"| runtime

    runtime -.->|"Exit → Chart B"| chart_b_entry["⬇ Running System"]

    style chart_b_entry fill:#2d6a4f,color:#fff,stroke:none
    style ir fill:#e76f51,color:#fff
    style contracts fill:#264653,color:#fff
    style runtime fill:#264653,color:#fff
```

---

## Chart B: Running System (Docker Topology)

The generated app running in production. Shows service connections and where errors exit to the healer.

```mermaid
flowchart TB
    subgraph docker["Docker Compose"]
        direction TB
        
        subgraph app_svc["App Service (FastAPI)"]
            direction TB
            app["backend/app.py<br/><i>CORS, error handler</i>"]
            routes["Route Handlers<br/><i>routes_*.py</i>"]
            repos["Repository Layer<br/><i>memory or postgres adapter</i>"]
            auth["Auth Middleware<br/><i>JWT provider (optional)</i>"]
            
            app --> auth
            auth --> routes
            routes --> repos
        end

        subgraph db_svc["Database Service"]
            db[("PostgreSQL 16<br/><i>schema.sql</i>")]
        end

        subgraph healer_svc["Healer Service (Sidecar)"]
            healer_api["Healer API<br/><i>:8083</i>"]
        end

        subgraph frontend_svc["Frontend Service"]
            frontend["Next.js App<br/><i>generated pages +<br/>API client</i>"]
        end
    end

    users["Users / API Clients"]
    
    users -->|"HTTP :3000"| frontend
    users -->|"HTTP :8000"| app
    frontend -->|"API calls"| app
    repos -->|"SQL queries"| db
    app -->|"POST /healer/ingest<br/><i>unhandled exceptions</i>"| healer_api

    healer_api -.->|"Exit → Chart C"| chart_c_entry["⬇ Healing Loop"]

    style chart_c_entry fill:#2d6a4f,color:#fff,stroke:none
    style db fill:#264653,color:#fff
    style healer_api fill:#e9c46a,color:#000
```

---

## Chart C: The Healing Loop (Tier 3)

Self-healing pipeline. Errors flow in, classified fixes flow out, regeneration loops back to Chart A.

```mermaid
flowchart TB
    subgraph ingest["Error Ingestion"]
        error_in["POST /healer/ingest<br/><i>Entry ← Chart B</i>"]
        queue["Priority Queue<br/><i>SQLite-backed</i>"]
        error_in -->|"HealerTicket"| queue
    end

    subgraph analysis["Classification"]
        classifier["Error Classifier<br/><i>classify_raw_error()<br/>classify_validation_error()</i>"]
        tracer["Runtime Tracer<br/><i>stacktrace → contract FQN</i>"]
        classifier --- tracer
    end

    subgraph proposal["Fix Proposal"]
        direction TB
        det["Tier 1: Deterministic<br/><i>naming, FQN, graph edge fixes</i>"]
        llm["Tier 2-3: LLM-Assisted<br/><i>structural + semantic fixes</i>"]
        det --> merge["Proposed Diff"]
        llm --> merge
    end

    subgraph approval["Approval Gate"]
        direction TB
        auto["Auto-Apply<br/><i>high-confidence fixes</i>"]
        human["Human Review<br/><i>POST /healer/approve/{id}<br/>POST /healer/reject/{id}</i>"]
    end

    subgraph apply["Fix Application"]
        applier["Applier<br/><i>apply_fix() with rollback</i>"]
        notify["Notifier<br/><i>console, webhook, file</i>"]
    end

    subgraph regen["Regeneration"]
        recompile["Compiler.compile()<br/><i>re-read modified contracts</i>"]
        regenerate["Generators<br/><i>overwrite runtime/ code</i>"]
        recompile -->|"DomainIR"| regenerate
    end

    queue --> classifier
    classifier -->|"classified ticket"| proposal
    merge --> approval
    auto --> applier
    human --> applier
    applier --> notify
    applier --> regen

    regenerate -.->|"Loop → Chart A<br/><i>contracts modified,<br/>code regenerated</i>"| chart_a_return["⬆ Forge Pipeline"]

    style chart_a_return fill:#2d6a4f,color:#fff,stroke:none
    style error_in fill:#e9c46a,color:#000
    style merge fill:#e76f51,color:#fff
```

---

## How the Charts Connect

```mermaid
flowchart LR
    D["Chart D<br/>Contract Origins"]
    A["Chart A<br/>Forge Pipeline"]
    B["Chart B<br/>Running System"]
    C["Chart C<br/>Healing Loop"]

    D -->|".contract.yaml"| A
    A -->|"runtime/ code"| B
    B -->|"errors"| C
    C -->|"fix + regenerate"| A

    style D fill:#2a9d8f,color:#fff
    style A fill:#264653,color:#fff
    style B fill:#e76f51,color:#fff
    style C fill:#e9c46a,color:#000
```
