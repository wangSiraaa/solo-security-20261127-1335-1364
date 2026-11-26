"""Allow ``python -m privacy_desk ...`` as a shortcut for the CLI."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
