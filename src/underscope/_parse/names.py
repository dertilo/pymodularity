"""Name/module-path helpers shared by `_check` (privacy enforcement),
`_summary` (rendering) and `_resolve`/`_graph` (import resolution)."""


def module_comp(parts: tuple[str, ...]) -> tuple[str, ...]:
    if parts[-1] == "__init__.py":
        return parts[:-1]
    return parts[:-1] + (parts[-1][:-3],)


def is_private_name(name: str) -> bool:
    """A name private by Rust's convention, ported to PEP 8 underscores:
    one leading underscore, not a dunder, not the bare `_` placeholder
    name."""
    return name.startswith("_") and not name.startswith("__") and name != "_"
