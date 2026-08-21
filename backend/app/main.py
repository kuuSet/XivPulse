"""ASGI deployment entrypoint."""

from . import create_app

app = create_app()


__all__ = ["app"]
