"""AdmitPerf CLI entry point."""

from __future__ import annotations

import click

from admitperf import __version__


@click.group()
@click.version_option(__version__)
def main() -> None:
    """AdmitPerf — benchmark LLM admission-control policies."""


@main.command()
def policies() -> None:
    """List available admission-control policies."""
    click.echo("policies: (none registered yet — see src/admitperf/policies/)")


@main.command()
def traces() -> None:
    """List available trace loaders."""
    click.echo("traces: (none registered yet — see src/admitperf/traces/)")


@main.command()
@click.option("--policy", required=True, help="Policy name")
@click.option("--trace", required=True, help="Trace name")
@click.option("--engine-url", default="http://localhost:8000/v1", help="OpenAI-compatible engine URL")
def run(policy: str, trace: str, engine_url: str) -> None:
    """Run a policy against a trace on a live engine."""
    click.echo(f"run: policy={policy} trace={trace} engine={engine_url} (not implemented)")


if __name__ == "__main__":
    main()
