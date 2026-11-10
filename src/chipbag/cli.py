from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv

app = typer.Typer(help="Spec and test planning tools for silicon.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """chipbag command-line interface."""
    # Search upward from the current working directory (not this package's install dir).
    load_dotenv(find_dotenv(usecwd=True))


@app.command()
def hello(name: str = typer.Argument("world", help="Who to greet.")) -> None:
    """Say hello (placeholder command)."""
    typer.echo(f"Hello, {name}!")


@app.command()
def index(
    topic: str = typer.Option(..., "--topic", help='Topic to index, e.g. "MCI mailbox".'),
    repo: Path = typer.Option(..., "--repo", help="Design repository root."),
    name: str | None = typer.Option(None, help="Workspace name. Default: <repo dir>-<topic slug>."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    model: str | None = typer.Option(None, help="Model for the index agent."),
    max_turns: int | None = typer.Option(None, help="Agent turn budget."),
    max_budget_usd: float | None = typer.Option(None, help="Hard cost cap for this run."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept the manifest without asking."),
) -> None:
    """Discover the spec and verification sources for a topic and write the manifest."""
    import asyncio

    from chipbag import index as ix

    work_dir = work / (name or ix.default_name(repo, topic))
    result = asyncio.run(
        ix.run_index(
            repo,
            topic,
            model=model or ix.DEFAULT_MODEL,
            max_turns=max_turns or ix.DEFAULT_MAX_TURNS,
            max_budget_usd=max_budget_usd or ix.DEFAULT_MAX_BUDGET_USD,
            on_event=typer.echo,
        )
    )
    run_dir = ix.save_run(result, work_dir)
    cost = f"${result.cost_usd:.4f}" if result.cost_usd is not None else "unknown cost"
    typer.echo(f"\nRun saved to {run_dir}  ({result.turns} turns, {cost}, {result.submissions} submissions)")

    if result.manifest is None:
        typer.echo("No valid manifest was accepted.", err=True)
        for e in result.last_errors:
            typer.echo(f"  - {e}", err=True)
        raise typer.Exit(1)

    typer.echo("\n" + ix.summarize(result.manifest))
    if not yes and not typer.confirm("\nAccept this manifest?", default=True):
        typer.echo("Not accepted; the run's manifest is still saved for review.")
        raise typer.Exit(1)
    typer.echo(f"Manifest written to {ix.write_manifest(result.manifest, work_dir)}")


@app.command()
def requirements(
    name: str = typer.Option(..., help="Workspace name, e.g. caliptra-ss-mci-mailbox."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    manifest: Path | None = typer.Option(None, help="Manifest to read. Default: <workspace>/manifest.yaml."),
    section: list[str] | None = typer.Option(None, help="Only sections whose heading path matches this glob (repeatable)."),
    limit: int | None = typer.Option(None, help="Process at most N sections."),
    model: str | None = typer.Option(None, help="Model for extraction."),
    max_cost_usd: float | None = typer.Option(None, help="Hard cost cap for this run."),
    from_log: bool = typer.Option(False, help="Reuse logged LLM responses for unchanged sections."),
    agent_chunking: str | None = typer.Option(None, help="Path glob: chunk matching sources with the chunking agent even if a native chunker exists."),
) -> None:
    """Extract atomic requirements (with exact source locations) from the manifest's spec sources."""
    import asyncio

    import yaml

    from chipbag import requirements as rq

    work_dir = work / name
    manifest_path = manifest or work_dir / "manifest.yaml"
    if not manifest_path.is_file():
        typer.echo(f"Manifest not found: {manifest_path} (run `chipbag index` or pass --manifest)", err=True)
        raise typer.Exit(1)
    result = asyncio.run(
        rq.extract(
            yaml.safe_load(manifest_path.read_text()),
            work_dir,
            model=model or rq.DEFAULT_MODEL,
            max_cost_usd=max_cost_usd or rq.DEFAULT_MAX_COST_USD,
            section_glob=section,
            limit=limit,
            from_log=from_log,
            agent_chunking=agent_chunking,
            on_event=typer.echo,
        )
    )
    s = result["stats"]
    typer.echo(
        f"\n{s['requirements']} requirements ({s['property_requirements']} from register properties, "
        f"{s['by_construction']} by construction, {s['requirements'] - s['by_construction']} need a test) "
        f"from {s['sections']} sections; "
        f"{s['ambiguous_text']} ambiguous passages, {s['ambiguous_requirements']} under-specified requirements, "
        f"{s['unclassified']} unclassified, {s['dropped']} dropped (unverified quotes); "
        f"mean coverage {s['mean_coverage']:.1%}; ${s['cost_usd']:.3f}"
    )
    for skipped in result["skipped_sources"]:
        typer.echo(f"  skipped {skipped['path']}: {skipped['reason']}")
    typer.echo(f"Written to {rq.write(result, work_dir)}")


@app.command()
def cases(
    name: str = typer.Option(..., help="Workspace name, e.g. caliptra-ss-mci-mailbox."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    manifest: Path | None = typer.Option(None, help="Manifest to read. Default: <workspace>/manifest.yaml."),
    only: list[str] | None = typer.Option(None, help="Only chunks whose path or chunk name matches this glob (repeatable)."),
    limit: int | None = typer.Option(None, help="Process at most N chunks."),
    model: str | None = typer.Option(None, help="Model for extraction."),
    max_cost_usd: float | None = typer.Option(None, help="Hard cost cap for this run."),
    from_log: bool = typer.Option(False, help="Reuse logged LLM responses for unchanged chunks."),
    agent_chunking: str | None = typer.Option(None, help="Path glob: chunk matching sources with the chunking agent."),
) -> None:
    """Extract verification cases (stimulus, checks, exact citations) from the manifest's verification sources."""
    import asyncio

    import yaml

    from chipbag import cases as vc

    work_dir = work / name
    manifest_path = manifest or work_dir / "manifest.yaml"
    if not manifest_path.is_file():
        typer.echo(f"Manifest not found: {manifest_path} (run `chipbag index` or pass --manifest)", err=True)
        raise typer.Exit(1)
    result = asyncio.run(
        vc.extract(
            yaml.safe_load(manifest_path.read_text()),
            work_dir,
            model=model or vc.DEFAULT_MODEL,
            max_cost_usd=max_cost_usd or vc.DEFAULT_MAX_COST_USD,
            chunk_glob=only,
            limit=limit,
            from_log=from_log,
            agent_chunking=agent_chunking,
            on_event=typer.echo,
        )
    )
    s = result["stats"]
    k = s["by_kind"]
    typer.echo(
        f"\n{s['cases']} cases ({k['planned']} planned, {k['implemented']} implemented, {k['listed']} listed) "
        f"from {s['chunks']} chunks; {s['checks']} checks ({s['failing_checks']} fail the test on mismatch); "
        f"{s['implemented_without_failing_checks']} implemented cases with no failing check; "
        f"{s['ambiguous']} ambiguous, {s['unclassified']} unclassified, {s['uncited_code']} uncited code blocks, "
        f"{s['citation_failures']} citation failures; "
        f"mean coverage {s['mean_coverage']:.1%}; ${s['cost_usd']:.3f}"
    )
    for skipped in result["skipped_sources"]:
        typer.echo(f"  skipped {skipped['path']}: {skipped['reason']}")
    typer.echo(f"Written to {vc.write(result, work_dir)}")


@app.command()
def dedup(
    name: str = typer.Option(..., help="Workspace name, e.g. caliptra-ss-mci-mailbox."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    what: str = typer.Option("both", help="requirements, cases, both, or checkers (only re-scope shared checkers)."),
    model: str | None = typer.Option(None, help="Model for merging."),
    max_cost_usd: float | None = typer.Option(None, help="Hard cost cap for this run."),
    from_log: bool = typer.Option(False, help="Reuse logged LLM responses for unchanged clusters."),
) -> None:
    """Merge duplicate requirements and resolve verification cases into tests (writes canonical.yaml)."""
    import asyncio

    from chipbag import dedup as dd

    if what not in ("both", "requirements", "cases", "checkers"):
        raise typer.BadParameter("--what must be requirements, cases, both or checkers")
    result = asyncio.run(
        dd.run(work / name, what=what, model=model or dd.DEFAULT_MODEL,
               max_cost_usd=max_cost_usd or dd.DEFAULT_MAX_COST_USD, from_log=from_log, on_event=typer.echo)
    )
    if r := result.get("requirements_stats"):
        typer.echo(
            f"\nRequirements: {r['units']} -> {r['canonical']} canonical ({r['merged_groups']} merged groups holding "
            f"{r['members_in_merged_groups']}); {r['needs_test']} need a test; {r['conflicts']} conflicts, "
            f"{r['refinements']} refinements, {r['ambiguities_resolved']} ambiguities resolved; {r['issues']} issues"
        )
    if c := result.get("cases_stats"):
        typer.echo(
            f"Cases: {c['units']} -> {c['tests']} tests ({c['tests_implemented']} implemented, "
            f"{c['tests_listed_only']} listed only, {c['tests_multi_file']} multi-file, {c['tests_unlisted']} not in any "
            f"list) and {c['plans']} plan items ({c['plans_merged']} merged, {c['plans_unimplemented']} with no test); "
            f"{c['issues']} issues"
        )
    if k := result.get("checker_stats"):
        typer.echo(f"Checkers: roles {k['roles']}; "
                   f"{k['tests_with_checker']} tests linked to a checker; {len(result.get('checker_issues') or [])} issues")
    typer.echo(f"${result['cost_usd']:.3f}; written to {work / name / 'canonical.yaml'}")


@app.command()
def correlate(
    name: str = typer.Option(..., help="Workspace name, e.g. caliptra-ss-mci-mailbox."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    manifest: Path | None = typer.Option(None, help="Manifest (for the repo root). Default: <workspace>/manifest.yaml."),
    section: list[str] | None = typer.Option(None, help="Only requirements from sections matching this heading glob (repeatable)."),
    limit: int | None = typer.Option(None, help="Judge at most N requirement batches."),
    top_k: int | None = typer.Option(None, help="BM25 candidates per requirement and per unit."),
    model: str | None = typer.Option(None, help="Model for judgment."),
    max_cost_usd: float | None = typer.Option(None, help="Hard cost cap for this run."),
    from_log: bool = typer.Option(False, help="Reuse logged LLM responses for unchanged batches."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before spending above the confirmation threshold."),
) -> None:
    """Map canonical requirements to the tests and plan items that verify them (writes mapping.yaml)."""
    import asyncio

    from chipbag import correlate as cr

    work_dir = work / name
    prepared = cr.prepare(work_dir, manifest=manifest, section_glob=section, limit=limit, top_k=top_k or cr.TOP_K,
                          model=model or cr.DEFAULT_MODEL, from_log=from_log, on_event=typer.echo)
    typer.echo(f"{len(prepared.reqs)} requirements, {len(prepared.batches)} batches, "
               f"{len(prepared.plan_batches)} plan-link batches; estimated ${prepared.estimate_usd:.2f}")
    if prepared.estimate_usd > cr.CONFIRM_ABOVE_USD and not yes and not typer.confirm("Continue?", default=True):
        raise typer.Exit(1)
    result = asyncio.run(cr.execute(prepared, max_cost_usd=max_cost_usd or cr.DEFAULT_MAX_COST_USD,
                                    on_event=typer.echo))
    s = result["stats"]
    typer.echo(
        f"\n{s['requirements_judged']} of {s['needs_test']} requirements judged ({s['requirements_without_candidates']} "
        f"without candidates); {s['entries']} entries: {s['covers']} covers, {s['partial']} partial, "
        f"{s['conflicts']} conflicts; strength {s['by_strength']}"
    )
    typer.echo(
        f"Mapped: {s['requirements_mapped']} ({s['requirements_mapped_by_a_test']} by a test, "
        f"{s['requirements_mapped_only_by_plans_coverage_or_lists']} only by plans, coverage or list entries), "
        f"unmapped {s['requirements_unmapped']}; {s['entries_citing_a_checker']} entries cite a checker; "
        f"dropped {s['dropped']}"
    )
    typer.echo(
        f"Plan links: {s['plan_links']} judged + {s['plan_links_from_dedup']} from dedup; "
        f"{s['plans_with_a_test']} of {s['plans']} plan items have a test. Register categories: {s['register_categories']}"
    )
    typer.echo(f"{s['llm_calls']} LLM calls, {s['cached_calls']} cached, {s['errors']} errors; "
               f"${s['cost_usd']:.3f}; written to {work_dir / 'mapping.yaml'}")



@app.command()
def analyze(
    name: str = typer.Option(..., help="Workspace name."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    manifest: Path | None = typer.Option(None, help="Manifest (for the repo root). Default: <workspace>/manifest.yaml."),
    max_cost_usd: float | None = typer.Option(None, help="Hard cost cap for this run."),
    from_log: bool = typer.Option(False, help="Reuse logged LLM responses for unchanged prompts."),
) -> None:
    """Turn the mapping and the normalize/dedup records into candidate findings (writes analysis.yaml)."""
    import asyncio

    from chipbag import analyze as an
    from chipbag.correlate import repo_root

    work_dir = work / name
    root = repo_root(work_dir, manifest, typer.echo)
    result = asyncio.run(an.run(work_dir, max_cost_usd=max_cost_usd or an.DEFAULT_MAX_COST_USD, from_log=from_log,
                                repo_root=root, on_event=typer.echo))
    s = result["stats"]
    typer.echo(f"\n{s['findings']} candidate findings")
    for cat, types in s["by_category"].items():
        typer.echo(f"  {an.CATEGORIES[cat]}. {cat}: {sum(types.values())}  {types}")
    typer.echo(f"dropped {s['dropped']}; {s['errors']} errors; ${s['cost_usd']:.3f}; written to {work_dir / 'analysis.yaml'}")



@app.command()
def verify(
    name: str = typer.Option(..., help="Workspace name."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    manifest: Path | None = typer.Option(None, help="Manifest (for the repo root). Default: <workspace>/manifest.yaml."),
    only: list[str] | None = typer.Option(None, help="Only these categories or finding ids (repeatable)."),
    limit: int | None = typer.Option(None, help="Check at most N findings (in severity order)."),
    model: str | None = typer.Option(None, help="Model for the verify agents."),
    max_cost_usd: float | None = typer.Option(None, help="Stop launching agents once this much is spent."),
    force: bool = typer.Option(False, help="Re-check findings that already have a cached verdict."),
) -> None:
    """Check each candidate finding with a targeted agent that tries to refute it (writes findings.yaml)."""
    import asyncio

    from chipbag import verify as vf
    from chipbag.correlate import repo_root

    work_dir = work / name
    root = repo_root(work_dir, manifest, typer.echo)
    if root is None:
        raise typer.Exit(1)
    result = asyncio.run(vf.run(work_dir, root, model=model or vf.DEFAULT_MODEL, limit=limit, only=only, force=force,
                                max_cost_usd=max_cost_usd or vf.DEFAULT_MAX_COST_USD, on_event=typer.echo))
    s = result["stats"]
    typer.echo(f"\n{s['findings']} findings: {s['by_status']}; group items {s['group_items']}")
    for cat, st in s["by_category"].items():
        typer.echo(f"  {cat}: {st}")
    typer.echo(f"${s['cost_usd_this_run']:.2f} this run (${s['cost_usd_total']:.2f} total); "
               f"{len(result['manifest_additions'])} new sources found; written to {work_dir / 'findings.yaml'}")


@app.command()
def run(
    name: str | None = typer.Option(None, help="Workspace name. Default: <repo dir>-<topic slug> when --topic is given."),
    work: Path = typer.Option(Path("work"), help="Workspace root directory."),
    topic: str | None = typer.Option(None, help='Topic to index when the workspace has no manifest, e.g. "MCI mailbox".'),
    repo: Path | None = typer.Option(None, help="Design repository root (needed only to index)."),
    manifest: Path | None = typer.Option(None, help="Manifest to use. Default: <workspace>/manifest.yaml."),
    section: list[str] | None = typer.Option(None, help="Only spec sections matching this heading glob (repeatable; saved to scope.yaml)."),
    chunk: list[str] | None = typer.Option(None, help="Only verification chunks matching this glob (repeatable; saved to scope.yaml)."),
    max_cost_usd: float = typer.Option(50.0, help="Cost cap for the whole run."),
    verify_limit: int | None = typer.Option(None, help="Verify at most N uncached findings."),
    fresh: bool = typer.Option(False, help="Ignore cached LLM responses and verdicts (full cost)."),
    pdf: bool = typer.Option(False, help="Also render the report to PDF (needs Chrome or Chromium)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show each stage's progress lines."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before indexing or spending above the confirmation threshold."),
) -> None:
    """Run every stage, from the manifest to the report, reusing cached work (writes reports/<time>.md)."""
    import asyncio

    from chipbag import index as ix
    from chipbag import pipeline as pl

    if not name and not (topic and repo):
        raise typer.BadParameter("pass --name, or --topic and --repo")
    work_dir = work / (name or ix.default_name(repo, topic))
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest or work_dir / "manifest.yaml"
    if not manifest_path.is_file():
        if not (topic and repo):
            typer.echo(f"No manifest at {manifest_path}. Pass --topic and --repo to index the repository.", err=True)
            raise typer.Exit(1)
        typer.echo(f"[0/{len(pl.STAGES)}] index: finding spec and verification sources for {topic!r}")
        result = asyncio.run(ix.run_index(repo, topic, model=ix.DEFAULT_MODEL, max_turns=ix.DEFAULT_MAX_TURNS,
                                          max_budget_usd=ix.DEFAULT_MAX_BUDGET_USD,
                                          on_event=typer.echo if verbose else (lambda _: None)))
        ix.save_run(result, work_dir)
        if result.manifest is None:
            typer.echo("No valid manifest was accepted.", err=True)
            raise typer.Exit(1)
        typer.echo("\n" + ix.summarize(result.manifest) + "\n")
        if not yes and not typer.confirm("Accept this manifest?", default=True):
            raise typer.Exit(1)
        manifest_path = ix.write_manifest(result.manifest, work_dir)
    if section or chunk:
        pl.save_scope(work_dir, section, chunk)

    out = pl.run(work_dir, manifest_path, max_cost_usd=max_cost_usd, fresh=fresh, pdf=pdf, verify_limit=verify_limit,
                 say=typer.echo, detail=(lambda m: typer.echo("    " + m.strip())) if verbose else (lambda _: None),
                 confirm=(lambda q: True) if yes else (lambda q: typer.confirm(q, default=True)))
    typer.echo(f"\nDone ({out['status']}): ${out['cost_usd']:.2f}, {out['seconds']:.0f}s")
    for p in out["report"]:
        typer.echo(f"Report: {p}")


if __name__ == "__main__":
    app()
