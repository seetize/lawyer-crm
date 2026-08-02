from __future__ import annotations

import asyncio
import sys


def configure_asyncio_policy() -> None:
    """Use a selector loop required by curl_cffi on native Windows."""
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
