"""`opal listen tree`: render the live CLI command tree via click introspection.

The tree is generated from the Typer app at call time, so it always reflects the
current commands and parameters: mandatory options print as ``--option``, optional
options as ``[--option]``, positional arguments as ``name`` / ``[name]``. The
default view is a styled ``rich.tree.Tree`` (green root, blue guides, cyan
commands, yellow required params, dim optional params); colors are emitted only
on a terminal, so piping keeps plain Unicode box-drawing output. ``--ascii``
renders a color-free variant with ``|--`` / ```-- `` / ``|`` guides for logs and
non-UTF-8 environments. The built-in ``--help`` option, shell-completion
plumbing, and hidden params/commands are skipped.
"""

from __future__ import annotations

import typer
from rich.text import Text
from rich.tree import Tree
from typer.main import get_command

from opal_listener.cli.output import console


def _param_label(param: object) -> tuple[str, str] | None:
    """A param's ``(text, style)`` — yellow when required, dim when bracketed — or None to skip it."""
    if getattr(param, "hidden", False) or getattr(param, "expose_value", True) is False:
        return None
    kind = getattr(param, "param_type_name", "")
    required = getattr(param, "required", False)
    style = "yellow" if required else "dim"
    if kind == "argument":
        name = param.name  # type: ignore[attr-defined]
        return (name if required else f"[{name}]", style)
    if kind != "option":
        return None
    opts = [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]
    flag = next((o for o in opts if o.startswith("--")), opts[0] if opts else param.name)  # type: ignore[attr-defined]
    if flag == "--help":
        return None
    return (flag if required else f"[{flag}]", style)


def _usage_labels(cmd: object) -> list[tuple[str, str]]:
    return [label for p in getattr(cmd, "params", ()) if (label := _param_label(p))]


def _visible_commands(group: object) -> list[tuple[str, object]]:
    return [(name, cmd) for name, cmd in getattr(group, "commands", {}).items() if not getattr(cmd, "hidden", False)]


def _is_group(cmd: object) -> bool:
    return bool(getattr(cmd, "commands", None))


def _styled_label(name: str, cmd: object, name_style: str) -> Text:
    text = Text(name, style=name_style)
    for label, style in _usage_labels(cmd):
        text.append(f" {label}", style=style)
    return text


def _plain_label(name: str, cmd: object) -> str:
    return " ".join([name, *(label for label, _ in _usage_labels(cmd))])


def _add_commands(node: Tree, group: object) -> None:
    for name, cmd in _visible_commands(group):
        child = node.add(_styled_label(name, cmd, "bold cyan" if _is_group(cmd) else "cyan"), guide_style="blue")
        if _is_group(cmd):
            _add_commands(child, cmd)


def render_tree(root: object, label: str) -> Tree:
    """Render a click Command/Group as a styled rich Tree rooted at `label`."""
    tree = Tree(_styled_label(label, root, "bold green"), guide_style="blue")
    _add_commands(tree, root)
    return tree


def _ascii_lines(group: object, prefix: str) -> list[str]:
    visible = _visible_commands(group)
    lines: list[str] = []
    for i, (name, cmd) in enumerate(visible):
        last = i == len(visible) - 1
        lines.append(f"{prefix}{'`-- ' if last else '|-- '}{_plain_label(name, cmd)}")
        if _is_group(cmd):
            lines.extend(_ascii_lines(cmd, prefix + ("    " if last else "|   ")))
    return lines


def render_ascii_tree(root: object, label: str) -> str:
    """Render a click Command/Group as a color-free ASCII tree rooted at `label`."""
    lines = [_plain_label(label, root)]
    lines.extend(_ascii_lines(root, ""))
    return "\n".join(lines)


def register(app: typer.Typer, label: str = "opal listen") -> None:
    """Attach the `tree` command, which prints the app's live command tree."""

    @app.command("tree")
    def tree(
        ascii: bool = typer.Option(False, "--ascii", help="ASCII guides, no color (safe for logs and pipes)."),
    ) -> None:
        """Print the command tree with per-command parameters."""
        root = get_command(app)
        if ascii:
            typer.echo(render_ascii_tree(root, label))
        else:
            console.print(render_tree(root, label))
