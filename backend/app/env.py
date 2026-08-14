"""Environment-variable compatibility helpers for the XivPulse rename."""

from __future__ import annotations

import os


def env_value(*names: str, default: str | None = None) -> str | None:
    """Return the first explicitly configured value, including an empty string."""

    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


def xivpulse_env(suffix: str, default: str | None = None) -> str | None:
    """Read the XivPulse name first and the pre-rename variable as a fallback."""

    return env_value(f"XIVPULSE_{suffix}", f"ARXIV_DAILY_{suffix}", default=default)


__all__ = ["env_value", "xivpulse_env"]
