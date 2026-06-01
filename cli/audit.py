"""
CLI tool — run audits from the terminal without any HTTP layer.

Usage:
  audit run developer.co.uk --geography uk
  audit run developer.co.uk --geography uk --pretty
  audit run developer.co.uk --geography uk --force
  audit batch domains.csv --geography uk
  audit show <audit-id>
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(name="audit", help="GTM Intelligence Engine — domain auditing CLI")
console = Console()
err_console = Console(stderr=True)


def _get_auditor():
    from config import Settings
    from core.engine import DomainAuditor
    from verticals.proptech import register

    register()
    return DomainAuditor(Settings(), store=None)


@app.command()
def run(
    domain: Annotated[str, typer.Argument(help="Domain to audit (e.g. developer.co.uk)")],
    geography: Annotated[str, typer.Option("--geography", "-g", help="'uk' or 'se'")] = "uk",
    vertical: Annotated[str, typer.Option("--vertical", "-v")] = "proptech",
    force: Annotated[bool, typer.Option("--force", help="Bypass cache")] = False,
    pretty: Annotated[bool, typer.Option("--pretty", help="Rich formatted output")] = False,
) -> None:
    """Run a full audit on a single domain."""
    from core.base.schemas import AuditRequest, Geography, Vertical

    auditor = _get_auditor()

    request = AuditRequest(
        domain=domain.removeprefix("https://").removeprefix("http://").rstrip("/").lower(),
        vertical=Vertical(vertical),
        geography=Geography(geography),
        force_refresh=force,
    )

    report = asyncio.run(auditor.audit(request))

    if pretty:
        _print_pretty(report)
    else:
        print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))


@app.command()
def batch(
    csv_file: Annotated[Path, typer.Argument(help="CSV file with a 'domain' column")],
    geography: Annotated[str, typer.Option("--geography", "-g")] = "uk",
    vertical: Annotated[str, typer.Option("--vertical", "-v")] = "proptech",
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output JSONL file")] = None,
) -> None:
    """Audit all domains in a CSV file. CSV must have a 'domain' column."""
    from core.base.schemas import AuditRequest, Geography, Vertical

    if not csv_file.exists():
        err_console.print(f"[red]File not found:[/red] {csv_file}")
        raise typer.Exit(1)

    with csv_file.open() as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "domain" not in reader.fieldnames:
            err_console.print("[red]CSV must have a 'domain' column[/red]")
            raise typer.Exit(1)
        domains = [row["domain"].strip() for row in reader if row.get("domain")]

    console.print(f"Auditing [bold]{len(domains)}[/bold] domains...")

    auditor = _get_auditor()
    out_file = open(output, "w") if output else sys.stdout  # noqa: SIM115

    async def _run_all() -> None:
        for i, domain in enumerate(domains, 1):
            console.print(f"  [{i}/{len(domains)}] {domain}", end="\r")
            request = AuditRequest(
                domain=domain.removeprefix("https://").removeprefix("http://").rstrip("/").lower(),
                vertical=Vertical(vertical),
                geography=Geography(geography),
            )
            report = await auditor.audit(request)
            line = json.dumps(report.model_dump(mode="json"), default=str)
            print(line, file=out_file)

    asyncio.run(_run_all())

    if output:
        out_file.close()
        console.print(f"\n[green]Done.[/green] Results written to {output}")


@app.command()
def show(
    audit_id: Annotated[str, typer.Argument(help="Audit ID to retrieve")],
) -> None:
    """Retrieve and display a stored audit by ID."""
    console.print("[yellow]Store not yet configured. Run with --pretty to see live results.[/yellow]")
    raise typer.Exit(1)


def _print_pretty(report) -> None:  # type: ignore[type-arg]
    console.print(Panel(f"[bold]{report.domain}[/bold]  |  {report.geography.value.upper()}  |  v{report.rules_version}", title="GTM Audit"))

    if report.triage:
        status_color = {"auto_approved": "green", "pending_review": "yellow", "flagged": "red"}.get(
            report.triage.review_status.value, "white"
        )
        console.print(
            f"Triage: [{status_color}]{report.triage.review_status.value}[/{status_color}]  "
            f"(confidence: {report.triage.audit_confidence:.0%})"
        )

    if report.icp_persona:
        console.print(f"ICP: [bold]{report.icp_persona.value}[/bold] ({report.icp_confidence:.0%})")

    if report.pain_signals:
        table = Table(title="Pain Signals", show_header=True)
        table.add_column("Signal", style="cyan")
        table.add_column("Severity")
        table.add_column("Confidence")
        table.add_column("Module")

        for s in report.pain_signals:
            sev_color = {"CRITICAL": "red", "HIGH": "orange3", "MEDIUM": "yellow", "LOW": "white"}.get(
                s.severity.value, "white"
            )
            table.add_row(
                s.signal_id,
                f"[{sev_color}]{s.severity.value}[/{sev_color}]",
                f"{s.confidence:.0%}",
                s.m360_module.value,
            )
        console.print(table)

    if report.outbound:
        console.print(Panel(report.outbound.hook_text, title="Hook"))
        console.print(f"Subject: [italic]{report.outbound.subject_line}[/italic]")
