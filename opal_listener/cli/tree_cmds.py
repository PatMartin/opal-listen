"""`opal listen tree`: render the live CLI command tree via click introspection.

The tree is generated from the Typer app at call time, so it always reflects the
current commands and parameters: mandatory options print as ``--option``, optional
options as ``[--option]``, positional arguments as ``name`` / ``[name]``. The
built-in ``--help`` option, shell-completion plumbing, and hidden params/commands
are skipped.
"""

from __future__ import annotations

import typer
from typer.main import get_command


def _param_label(param: object) -> str | None:
    if getattr(param, "hidden", False) or getattr(param, "expose_value", True) is False:
        return None
    kind = getattr(param, "param_type_name", "")
    required = getattr(param, "required", False)
    if kind == "argument":
        name = param.name  # type: ignore[attr-defined]
        return name if required else f"[{name}]"
    if kind != "option":
        return None
    opts = [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]
    flag = next((o for o in opts if o.startswith("--")), opts[0] if opts else param.name)  # type: ignore[attr-defined]
    if flag == "--help":
        return None
    return flag if required else f"[{flag}]"


def _usage(cmd: object) -> str:
    labels = [_param_label(p) for p in getattr(cmd, "params", ())]
    shown = " ".join(label for label in labels if label)
    return f" {shown}" if shown else ""


def _command_lines(group: object, prefix: str) -> list[str]:
    commands: dict[str, object] = getattr(group, "commands", {})
    visible = [(name, cmd) for name, cmd in commands.items() if not getattr(cmd, "hidden", False)]
    lines: list[str] = []
    for i, (name, cmd) in enumerate(visible):
        last = i == len(visible) - 1
        lines.append(f"{prefix}{'`-- ' if last else '+-- '}{name}{_usage(cmd)}")
        if getattr(cmd, "commands", None):
            lines.extend(_command_lines(cmd, prefix + ("    " if last else "|   ")))
    return lines


def render_tree(root: object, label: str) -> str:
    """Render a click Command/Group as an ASCII tree rooted at `label`."""
    lines = [label + _usage(root)]
    lines.extend(_command_lines(root, ""))
    return "\n".join(lines)


def register(app: typer.Typer, label: str = "opal listen") -> None:
    """Attach the `tree` command, which prints the app's live command tree."""

    @app.command("tree")
    def tree() -> None:
        """Print the command tree with per-command parameters."""
        typer.echo(render_tree(get_command(app), label))
