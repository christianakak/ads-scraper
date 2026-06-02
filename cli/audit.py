"""
CLI tool — run audits from the terminal without any HTTP layer.

Usage:
  audit scan developer.co.uk --geography uk       ← cinematic live UI
  audit run developer.co.uk --geography uk        ← raw JSON (Clay/batch use)
  audit batch domains.csv --geography uk
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

app = typer.Typer(name="audit", help="GTM Intelligence Engine — domain auditing CLI")
console = Console()
err_console = Console(stderr=True)

_SEV_COLOUR = {"CRITICAL": "red", "HIGH": "orange3", "MEDIUM": "yellow", "LOW": "dim white"}
_DQ_LABEL = {"real": ("[green]✓[/green]", "real"), "dummy": ("[yellow]~[/yellow]", "dummy"), "skipped": ("[red]✗[/red]", "skipped")}

# ---------------------------------------------------------------------------
# Collector summary line — what to show per collector
# ---------------------------------------------------------------------------

def _collector_summary(collector_id: str, data: dict[str, Any], data_source: str) -> str:
    icon, label = _DQ_LABEL.get(data_source, ("?", data_source))
    parts: list[str] = []

    if collector_id == "dns_headers":
        flags = []
        if data.get("has_ssl"):       flags.append("[green]SSL ✓[/green]")
        if data.get("has_spf"):       flags.append("[green]SPF ✓[/green]")
        if data.get("has_dmarc"):     flags.append("[green]DMARC ✓[/green]")
        if data.get("cdn_provider"):  flags.append(f"CDN:{data['cdn_provider']}")
        if data.get("email_provider") and data["email_provider"] != "Unknown":
            flags.append(data["email_provider"])
        parts = flags

    elif collector_id == "ad_intelligence":
        if data_source == "skipped":
            parts = ["[dim]no Adyntel key[/dim]"]
        elif data_source == "dummy":
            age = data.get("creative_age_days")
            parts = [f"[dim]dummy[/dim] — age:{age}d  fatigue:{data.get('ad_fatigue_score')}  cta:{data.get('primary_cta_type')}"]
        else:
            age = data.get("creative_age_days")
            parts = [f"age:[bold]{age}d[/bold]  fatigue:{data.get('ad_fatigue_score')}  cta:{data.get('primary_cta_type')}  spend:{data.get('spend_tier')}"]

    elif collector_id == "site_scanner":
        has_res = data.get("has_digital_reservation")
        load = data.get("load_time_ms")
        mob = data.get("mobile_score")
        crm = data.get("tech_stack", {}).get("crm") if isinstance(data.get("tech_stack"), dict) else None
        pixel = data.get("tech_stack", {}).get("has_facebook_pixel") if isinstance(data.get("tech_stack"), dict) else None

        if load:
            colour = "red" if load > 3000 else "yellow" if load > 2000 else "green"
            parts.append(f"[{colour}]{load}ms[/{colour}]")
        if mob is not None:
            colour = "red" if mob < 25 else "yellow" if mob < 50 else "green"
            parts.append(f"mobile:[{colour}]{mob:.0f}/100[/{colour}]")
        if crm:   parts.append(f"CRM:{crm}")
        if pixel is not None:
            parts.append("[green]FB✓[/green]" if pixel else "[dim]no pixel[/dim]")
        if has_res: parts.append("[green]reservation ✓[/green]")

    elif collector_id == "portal_quality":
        listed = data.get("portal_listed")
        if listed:
            score = data.get("listing_quality_score", 0)
            dom = data.get("days_on_market")
            colour = "red" if score < 0.45 else "yellow" if score < 0.7 else "green"
            parts.append(f"Rightmove [{colour}]{score:.0%}[/{colour}]")
            if dom: parts.append(f"{dom}d on market")
        else:
            parts.append("[red]NOT listed on Rightmove[/red]")

    elif collector_id == "planning_intel":
        stage = data.get("development_stage", "unknown")
        reg = data.get("has_register_interest_page")
        colour = "yellow" if stage == "pre_launch" else "green" if stage == "active" else "dim"
        parts.append(f"[{colour}]{stage}[/{colour}]")
        if reg: parts.append("[yellow]register interest found[/yellow]")
        apps = len(data.get("recent_planning_apps", []))
        if apps: parts.append(f"{apps} planning app{'s' if apps > 1 else ''}")

    elif collector_id == "social_review":
        tp_id = data.get("trustpilot_business_id")
        rating = data.get("avg_rating")
        if rating:
            colour = "red" if rating < 3.5 else "yellow" if rating < 4.2 else "green"
            parts.append(f"Trustpilot [{colour}]{rating}/5[/{colour}]  ({data.get('review_count', 0)} reviews)")
        elif tp_id:
            parts.append("[dim]TP ID found · ratings need API key[/dim]")
        else:
            parts.append("[dim]no reviews found[/dim]")

    detail = "  ".join(parts) if parts else ""
    return f"{icon} [{label}]  {detail}"


# ---------------------------------------------------------------------------
# scan — cinematic live terminal UI
# ---------------------------------------------------------------------------

@app.command()
def scan(
    domain: Annotated[str, typer.Argument(help="Domain to scan (e.g. developer.co.uk)")],
    geography: Annotated[str, typer.Option("--geography", "-g")] = "uk",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Full cinematic scan — live output as each collector fires."""
    from api.app import _build_clay_flat
    from config import Settings
    from core.base.schemas import AuditRequest, Geography, Vertical
    from core.engine import DomainAuditor
    from core.hook_generator import HookGenerator
    from verticals.proptech import register

    register()
    settings = Settings()

    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/").lower()

    # State tracked across callback invocations
    state: dict[str, Any] = {
        "done": {},       # collector_id → (result, elapsed)
        "start": {},      # collector_id → start time (approximated)
        "t0": time.time(),
    }
    lock = threading.Lock()

    COLLECTOR_ORDER = ["dns_headers", "ad_intelligence", "planning_intel", "social_review", "site_scanner", "portal_quality"]
    COLLECTOR_LABELS = {
        "dns_headers":    "dns / infrastructure",
        "ad_intelligence":"ad intelligence     ",
        "planning_intel": "planning intel      ",
        "social_review":  "reviews             ",
        "site_scanner":   "site scanner        ",
        "portal_quality": "portal quality      ",
    }

    def on_done(result: Any) -> None:
        elapsed = time.time() - state["t0"]
        with lock:
            state["done"][result.collector_id] = (result, elapsed)

    def _build_display(phase: str, report: Any = None) -> Any:
        lines: list[Any] = []

        # Header
        elapsed = time.time() - state["t0"]
        header = Text()
        header.append("  GTM INTELLIGENCE ENGINE", style="bold green")
        header.append(f"  ·  {domain.upper()}  ·  {geography.upper()}", style="dim")
        header.append(f"  ·  {elapsed:.0f}s", style="dim")
        lines.append(Panel(header, border_style="green"))

        # Collector table
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(width=22)
        table.add_column()
        table.add_column(width=6, justify="right")

        with lock:
            done_now = dict(state["done"])

        for cid in COLLECTOR_ORDER:
            label = COLLECTOR_LABELS.get(cid, cid)
            if cid in done_now:
                result, t = done_now[cid]
                summary = _collector_summary(cid, result.data, result.data_source)
                table.add_row(f"[dim]{label}[/dim]", summary, f"[dim]{t:.1f}s[/dim]")
            elif phase == "collecting":
                table.add_row(f"[dim]{label}[/dim]", "[dim]...[/dim]", "")

        lines.append(table)

        if report is None:
            return Group(*lines)

        # Pain signals
        lines.append(Rule(style="dim green"))

        sig_table = Table(show_header=False, box=None, padding=(0, 1))
        sig_table.add_column(width=12)
        sig_table.add_column(width=34)
        sig_table.add_column()

        for sig in report.pain_signals:
            col = _SEV_COLOUR.get(sig.severity.value, "white")
            sig_table.add_row(
                f"[{col}][{sig.severity.value}][/{col}]",
                f"[bold]{sig.signal_id}[/bold]",
                f"[dim italic]{sig.emotional_trigger[:65]}[/dim italic]",
            )

        icp = report.icp_persona.value.replace("_", " ") if report.icp_persona else "unknown"
        triage_col = {"auto_approved": "green", "pending_review": "yellow", "flagged": "red"}.get(
            report.triage.review_status.value if report.triage else "", "white"
        )
        summary_line = (
            f"  [bold]{len(report.pain_signals)} signals[/bold]  ·  "
            f"[bold cyan]{icp}[/bold cyan] ({report.icp_confidence:.0%})  ·  "
            f"[{triage_col}]{report.triage.review_status.value}[/{triage_col}] "
            f"@ {report.triage.audit_confidence:.0%}"
        )
        lines.append(Text.from_markup(summary_line))
        lines.append(sig_table)

        if report.recommended_modules:
            mods = "  →  ".join(f"[bold]{m.value}[/bold]" for m in report.recommended_modules)
            lines.append(Text.from_markup(f"\n  PRIMARY: {mods}"))

        # Outbound copy
        if report.outbound:
            lines.append(Rule(style="dim green"))
            lines.append(Text.from_markup(f"  [bold]SUBJECT[/bold]   {report.outbound.subject_line}"))
            lines.append(Text(""))
            hook_panel = Panel(
                report.outbound.hook_text,
                title="[bold green]HOOK[/bold green]",
                border_style="dim green",
            )
            lines.append(hook_panel)
            followup_panel = Panel(
                report.outbound.follow_up_angle,
                title="[dim]FOLLOW-UP (day 3)[/dim]",
                border_style="dim",
            )
            lines.append(followup_panel)

        # Data quality footer
        lines.append(Rule(style="dim"))
        dq = report.cache_meta.data_quality if report.cache_meta else {}
        dummy = [k for k, v in dq.items() if v == "dummy"]
        skipped = [k for k, v in dq.items() if v == "skipped"]
        footer_parts = ["[dim]"]
        if dummy:   footer_parts.append(f"~ dummy: {', '.join(dummy)}")
        if skipped: footer_parts.append(f"  ✗ skipped: {', '.join(skipped)}")
        if not dummy and not skipped:
            footer_parts.append("all collectors returned real data")
        footer_parts.append("[/dim]")
        lines.append(Text.from_markup("".join(footer_parts)))

        return Group(*lines)

    with Live(_build_display("collecting"), console=console, refresh_per_second=4, transient=False) as live:
        def tick() -> None:
            live.update(_build_display("collecting"))

        # Run audit with live ticking
        async def run_with_ticks() -> Any:
            auditor = DomainAuditor(settings, store=None, on_collector_done=on_done)
            req = AuditRequest(
                domain=domain,
                vertical=Vertical("proptech"),
                geography=Geography(geography),
                force_refresh=force,
            )

            # Tick display every 0.5s while audit runs
            async def ticker() -> None:
                while True:
                    await asyncio.sleep(0.5)
                    live.update(_build_display("collecting"))

            ticker_task = asyncio.create_task(ticker())
            try:
                report = await auditor.audit(req)
            finally:
                ticker_task.cancel()

            return report

        report = asyncio.run(run_with_ticks())

        # Generate hook
        if settings.anthropic_api_key and report.pain_signals:
            live.update(_build_display("done", report))
            hg = HookGenerator(settings.anthropic_api_key)
            report.outbound = asyncio.run(hg.generate(report))

        report.clay_flat = _build_clay_flat(report)
        live.update(_build_display("done", report))


# ---------------------------------------------------------------------------
# run — raw JSON output (Clay / batch use)
# ---------------------------------------------------------------------------

@app.command()
def run(
    domain: Annotated[str, typer.Argument(help="Domain to audit (e.g. developer.co.uk)")],
    geography: Annotated[str, typer.Option("--geography", "-g")] = "uk",
    vertical: Annotated[str, typer.Option("--vertical", "-v")] = "proptech",
    force: Annotated[bool, typer.Option("--force")] = False,
    pretty: Annotated[bool, typer.Option("--pretty")] = False,
) -> None:
    """Raw JSON output — use this for Clay, batch scripts, and piping."""
    from config import Settings
    from core.base.schemas import AuditRequest, Geography, Vertical
    from core.engine import DomainAuditor
    from verticals.proptech import register

    register()
    auditor = DomainAuditor(Settings(), store=None)
    req = AuditRequest(
        domain=domain.removeprefix("https://").removeprefix("http://").rstrip("/").lower(),
        vertical=Vertical(vertical),
        geography=Geography(geography),
        force_refresh=force,
    )
    report = asyncio.run(auditor.audit(req))

    if pretty:
        _print_pretty(report)
    else:
        print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))


# ---------------------------------------------------------------------------
# batch — CSV → JSONL
# ---------------------------------------------------------------------------

@app.command()
def batch(
    csv_file: Annotated[Path, typer.Argument(help="CSV file with a 'domain' column")],
    geography: Annotated[str, typer.Option("--geography", "-g")] = "uk",
    vertical: Annotated[str, typer.Option("--vertical", "-v")] = "proptech",
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Audit all domains in a CSV. Outputs JSONL — pipe directly into Clay or Supabase."""
    from config import Settings
    from core.base.schemas import AuditRequest, Geography, Vertical
    from core.engine import DomainAuditor
    from verticals.proptech import register

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

    register()
    auditor = DomainAuditor(Settings(), store=None)
    out_file = open(output, "w") if output else sys.stdout  # noqa: SIM115

    async def _run_all() -> None:
        for i, domain in enumerate(domains, 1):
            console.print(f"  [{i}/{len(domains)}] {domain}", end="\r")
            req = AuditRequest(
                domain=domain.removeprefix("https://").removeprefix("http://").rstrip("/").lower(),
                vertical=Vertical(vertical),
                geography=Geography(geography),
            )
            report = await auditor.audit(req)
            print(json.dumps(report.model_dump(mode="json"), default=str), file=out_file)

    asyncio.run(_run_all())

    if output:
        out_file.close()
        console.print(f"\n[green]Done.[/green] Written to {output}")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@app.command()
def show(
    audit_id: Annotated[str, typer.Argument(help="Audit ID to retrieve")],
) -> None:
    """Retrieve a stored audit by ID (requires Supabase configured)."""
    err_console.print("[yellow]Requires SUPABASE_URL + SUPABASE_SERVICE_KEY in .env[/yellow]")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Legacy pretty printer (used by --pretty flag)
# ---------------------------------------------------------------------------

def _print_pretty(report: Any) -> None:
    console.print(Panel(
        f"[bold]{report.domain}[/bold]  |  {report.geography.value.upper()}  |  v{report.rules_version}",
        title="GTM Audit",
    ))

    if report.triage:
        col = {"auto_approved": "green", "pending_review": "yellow", "flagged": "red"}.get(
            report.triage.review_status.value, "white"
        )
        console.print(
            f"Triage: [{col}]{report.triage.review_status.value}[/{col}]  "
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
            col = _SEV_COLOUR.get(s.severity.value, "white")
            table.add_row(
                s.signal_id,
                f"[{col}]{s.severity.value}[/{col}]",
                f"{s.confidence:.0%}",
                s.m360_module.value,
            )
        console.print(table)

    if report.outbound:
        console.print(Panel(report.outbound.hook_text, title="Hook"))
        console.print(f"Subject: [italic]{report.outbound.subject_line}[/italic]")
