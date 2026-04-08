"""Pipeline orchestrator — analyze, propose, apply, notify."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from forge.parser.validator import ContractValidationError
from healer.analyzer.classifier import classify_validation_error, classify_raw_error, Classification
from healer.applier import apply_fix
from healer.models import HealerTicket, HealerProposal, TicketSource, TicketStatus
from healer.notifier import Notifier
from healer.proposer.deterministic import propose_deterministic_fix
from healer.queue import HealerQueue

logger = logging.getLogger(__name__)


class HealerPipeline:

    def __init__(
        self,
        queue: HealerQueue,
        domains_root: Path = Path("domains"),
        diff_root: Path = Path(".forge/diffs"),
        log_path: Path = Path(".forge/healer/notifications.jsonl"),
    ) -> None:
        self.queue = queue
        self.domains_root = domains_root
        self.diff_root = diff_root
        self.notifier = Notifier(log_path=log_path)

    def process_next(self) -> bool:
        """Process the next queued ticket. Returns True if a ticket was processed."""
        ticket = self.queue.next_queued()
        if ticket is None:
            return False
        self.queue.update_status(ticket.id, TicketStatus.ANALYZING)
        self._process_ticket(ticket)
        return True

    def approve_ticket(self, ticket_id: str) -> bool:
        ticket = self.queue.get_ticket(ticket_id)
        if ticket is None or ticket.status != TicketStatus.PROPOSED:
            return False
        self.queue.update_status(ticket_id, TicketStatus.APPROVED)
        self._apply_and_notify(ticket)
        return True

    def reject_ticket(self, ticket_id: str, reason: str = "") -> bool:
        ticket = self.queue.get_ticket(ticket_id)
        if ticket is None or ticket.status != TicketStatus.PROPOSED:
            return False
        self.queue.update_status(ticket_id, TicketStatus.REJECTED, resolution_note=reason)
        self.notifier.notify(ticket, event="rejected", message=reason)
        return True

    def _process_ticket(self, ticket: HealerTicket) -> None:
        # Stage 2: Classify
        classification = self._classify(ticket)
        ticket.error_type = classification.error_type
        ticket.tier = classification.tier
        ticket.priority = classification.priority

        # Check if this is fixable by contract modification
        fixable_by = getattr(classification, "fixable_by", "contract")
        if fixable_by == "generator":
            self.queue.update_status(
                ticket.id, TicketStatus.FAILED,
                resolution_note=f"Generator bug — not fixable by contract. Fix the generator and regenerate.",
            )
            self.notifier.notify(ticket, event="failed",
                message=f"⚙️ Generator bug (not a contract issue): {ticket.raw_error[:100]}")
            return

        if fixable_by == "data":
            self.queue.update_status(
                ticket.id, TicketStatus.FAILED,
                resolution_note=f"Data issue — not fixable by contract. Check the data or database constraints.",
            )
            self.notifier.notify(ticket, event="failed",
                message=f"💾 Data issue (not a contract issue): {ticket.raw_error[:100]}")
            return

        # Stage 3: Propose (only for contract-fixable errors)
        proposal = self._propose(ticket)
        if proposal is None:
            self.queue.update_status(
                ticket.id, TicketStatus.FAILED,
                resolution_note="No contract fix could be proposed",
            )
            self.notifier.notify(ticket, event="failed", message="No contract fix proposed")
            return

        self.queue.set_proposal(ticket.id, proposal)
        ticket.proposal = proposal

        # Stage 4: Apply (Tier 1 auto-applies, Tier 2-3 queue for approval)
        if ticket.tier == 1:
            self._apply_and_notify(ticket)
        else:
            self.queue.update_status(ticket.id, TicketStatus.PROPOSED)
            self.notifier.notify(ticket, event="proposed", message=proposal.explanation)

    def _classify(self, ticket: HealerTicket) -> Classification:
        if ticket.source == TicketSource.VALIDATION:
            err = ContractValidationError(
                contract_fqn=ticket.contract_fqn or "",
                message=ticket.raw_error,
                path=ticket.context.get("path", ""),
            )
            return classify_validation_error(err)
        return classify_raw_error(
            source=ticket.source.value,
            error=ticket.raw_error,
            context=ticket.context,
        )

    def _propose(self, ticket: HealerTicket) -> Optional[HealerProposal]:
        if ticket.contract_fqn:
            contract = self._load_contract(ticket.contract_fqn)
            if contract:
                if ticket.tier == 1:
                    return propose_deterministic_fix(ticket.contract_fqn, contract)
                else:
                    from healer.proposer.llm_proposer import propose_llm_fix
                    return propose_llm_fix(ticket, contract, diff_root=self.diff_root)
        return None

    def _apply_and_notify(self, ticket: HealerTicket) -> None:
        if ticket.proposal is None:
            self.queue.update_status(ticket.id, TicketStatus.FAILED, resolution_note="No proposal")
            return

        contract_path = self._find_contract_path(ticket.contract_fqn or "")
        if contract_path is None:
            self.queue.update_status(
                ticket.id, TicketStatus.FAILED,
                resolution_note=f"Contract file not found for {ticket.contract_fqn}",
            )
            self.notifier.notify(ticket, event="failed", message="Contract file not found")
            return

        result = apply_fix(
            ticket.proposal, contract_path,
            diff_root=self.diff_root, ticket_id=ticket.id,
        )
        if result.success:
            self.queue.update_status(ticket.id, TicketStatus.APPLIED, resolution_note="Fix applied")
            self.notifier.notify(ticket, event="applied", message=ticket.proposal.explanation)

            # Auto-regenerate code from updated contracts
            regen_result = self._auto_regenerate()
            if regen_result:
                self.notifier.notify(ticket, event="applied",
                    message=f"🔄 Auto-regenerated: {regen_result}")
        else:
            self.queue.update_status(ticket.id, TicketStatus.FAILED, resolution_note=result.error)
            self.notifier.notify(ticket, event="failed", message=result.error)

    def _auto_regenerate(self) -> str:
        """Regenerate code from contracts after a fix is applied.

        Returns a summary string on success, empty string on failure.
        """
        try:
            from forge.ir.compiler import Compiler
            from forge.targets.fastapi_prod.generator import FastAPIProductionGenerator
            from forge.targets.postgres.gen_ddl import PostgresGenerator
            from forge.targets.migrations.generator import MigrationGenerator
            from forge.targets.nextjs.generator import NextJSGenerator

            # Find the domain directory (parent of entities/workflows/etc.)
            domain_dirs = [d for d in self.domains_root.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if not domain_dirs:
                logger.warning("No domain directories found for regeneration")
                return ""

            # Compile
            compiler = Compiler(contract_root=self.domains_root)
            ir = compiler.compile()

            # Determine output directory (sibling of domains/)
            output_root = self.domains_root.parent

            # Generate
            generators = [
                FastAPIProductionGenerator(),
                PostgresGenerator(),
                MigrationGenerator(
                    ir_cache_path=output_root / ".forge" / "ir_cache",
                    migrations_dir=output_root / "database" / "migrations",
                ),
                NextJSGenerator(),
            ]

            total_files = 0
            for gen in generators:
                for f in gen.generate(ir):
                    path = output_root / f.path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f.content, encoding="utf-8")
                    total_files += 1

            logger.info("Auto-regenerated %d files from contracts", total_files)
            return f"{total_files} files regenerated"

        except Exception as e:
            logger.error("Auto-regeneration failed: %s", e)
            return ""

    def _load_contract(self, fqn: str) -> Optional[dict]:
        path = self._find_contract_path(fqn)
        if path and path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        return None

    def _find_contract_path(self, fqn: str) -> Optional[Path]:
        parts = fqn.split("/")
        if len(parts) != 3:
            return None
        kind, domain, name = parts
        kind_dirs = {
            "entity": "entities", "workflow": "workflows",
            "page": "pages", "route": "routes",
            "agent": "agents", "mixin": "mixins", "infra": "infra",
        }
        subdir = kind_dirs.get(kind, kind + "s")
        path = self.domains_root / domain / subdir / f"{name}.contract.yaml"
        return path if path.exists() else None
