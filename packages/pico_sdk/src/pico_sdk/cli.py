"""The ``pico`` command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import sys

from pico_core.fsm import LoopEvent

from .config import Settings, load_settings
from .providers import (
    FREE_MODEL_ALIAS,
    create_provider,
    missing_required,
    resolve_free_model,
)
from .session import AgentSession


def format_event(event: LoopEvent) -> str | None:
    """Return the text to print for an event, or ``None`` to print nothing."""
    if event.kind == "text":
        return event.text
    if event.kind == "tool_request" and event.tool_request is not None:
        if event.tool_request.tool_call.name == "bash":
            return "$ " + event.tool_request.tool_call.arguments.get("command", "") + "\n"
    if event.kind == "tool_result" and event.tool_result is not None:
        if event.tool_result.name == "bash":
            return event.tool_result.content.rstrip() + "\n"
        if event.tool_result.is_error:
            # Surface failures (denied/unknown/error) for non-bash tools;
            # successful read/write/edit/grep/fetch/websearch/todo results
            # stay quiet — the model's summary covers them.
            return f"error [{event.tool_result.name}]: {event.tool_result.content.rstrip()}\n"
    return None


def apply_cli_overrides(args: argparse.Namespace, settings: Settings) -> bool:
    """Apply CLI flags onto ``settings`` (permission gating + skills).

    ``--allow-tools`` (comma-separated) wins over ``--no-bash`` per ADR-0003
    precedence; ``--skills-dir`` overrides the configured skills directory;
    ``--no-skills`` disables skill loading entirely (returns the flag).
    """
    if getattr(args, "allow_tools", None):
        settings.allowed_tools = [
            name.strip() for name in args.allow_tools.split(",") if name.strip()
        ]
    if getattr(args, "skills_dir", None):
        settings.skills_dir = args.skills_dir
    return not getattr(args, "no_skills", False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="picocli-chat", description="A headless coding agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the agent on a prompt.")
    run.add_argument("prompt", nargs="+", help="The prompt to run.")
    run.add_argument(
        "--no-bash",
        action="store_true",
        help="Disable unsandboxed bash (on by default; ignored when "
        "allowed_tools is set without 'bash').",
    )
    run.add_argument("--model", default=None, help="Override the configured model.")
    run.add_argument("--cwd", default=None, help="Working directory (default: current).")
    run.add_argument("--session", default=None, help="Resume an existing session by id.")
    run.add_argument(
        "--allow-tools",
        default=None,
        help="Comma-separated tool allowlist (e.g. 'read,grep,bash'); "
        "overrides settings.allowed_tools and --no-bash precedence.",
    )
    run.add_argument(
        "--skills-dir", default=None, help="Override the configured skills directory."
    )
    run.add_argument(
        "--no-skills", action="store_true", help="Disable SKILL.md loading."
    )
    run.add_argument(
        "--provider", default=None, help="Provider id (e.g. 'openai', 'ollama')."
    )
    return parser


async def run_command(args: argparse.Namespace) -> int:
    settings = load_settings()
    if args.provider:
        settings.provider = args.provider
    model = args.model or settings.model
    load_skills = apply_cli_overrides(args, settings)
    missing = missing_required(settings.provider, settings)

    if missing:
        sys.stderr.write(
            f"error: provider '{settings.provider}' is missing required "
            f"config: {', '.join(missing)}.\n"
             f"Run `picocli` and use /provider to configure it, or set "
            f"the corresponding environment variable.\n"
        )
        return 1

    provider = create_provider(settings)
    if model == FREE_MODEL_ALIAS and settings.provider == "openrouter":
        resolved = await resolve_free_model(provider)
        if resolved:
            model = resolved
            sys.stdout.write(f"using free model: {model}\n")
    if args.session:
        session = AgentSession.load(
            args.session,
            provider=provider,
            model=model,
            settings=settings,
            working_dir=args.cwd,
            allow_bash=not args.no_bash,
            load_skills=load_skills,
        )
    else:
        session = AgentSession(
            provider=provider,
            model=model,
            settings=settings,
            working_dir=args.cwd,
            allow_bash=not args.no_bash,
            load_skills=load_skills,
        )
    prompt = " ".join(args.prompt)
    if prompt.startswith("/compact"):
        instructions = prompt[len("/compact") :].strip()
        await session.compact(instructions)
        sys.stdout.write("compacted context\n")
        session.save()
        return 0
    async for event in session.stream(prompt):
        rendered = format_event(event)
        if rendered:
            sys.stdout.write(rendered)
            sys.stdout.flush()
    sys.stdout.write("\n")
    session.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(run_command(args))
    parser.error(f"unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
