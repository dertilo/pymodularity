"""Entry point for `python -m underscope`."""

from underscope.cli import main

if __name__ == "__main__":
    # nosemgrep: no-raise-outside-boundary  # boundary: CLI entry point, the process exit boundary
    raise SystemExit(main())
