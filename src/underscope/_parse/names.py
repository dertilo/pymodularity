"""Name/module-path helpers shared by `_check` (privacy enforcement),
`_summary` (rendering) and `_resolve`/`_graph` (import resolution)."""


def module_comp(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Dotted components for a module path. `parts` is usually a .py file's
    relative path, but a directory (namespace package, or a target equal to
    its own package root) is also valid: empty parts stay empty, and a
    directory component (no `.py` suffix) is kept as-is rather than having
    3 chars stripped off."""
    if not parts:
        return ()
    if parts[-1] == "__init__.py":
        return parts[:-1]
    if parts[-1].endswith(".py"):
        return parts[:-1] + (parts[-1][:-3],)
    return parts


def is_private_name(name: str) -> bool:
    """A name private by Rust's convention, ported to PEP 8 underscores:
    one leading underscore, not a dunder, not the bare `_` placeholder
    name."""
    return name.startswith("_") and not name.startswith("__") and name != "_"
