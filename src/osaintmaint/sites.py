"""Small, explicit workspace for approved public-source bookmarks."""

from __future__ import annotations

import webbrowser
from urllib.parse import urlparse


class SiteError(ValueError):
    """Raised when a bookmark is not a normal public HTTP(S) URL."""


def validate_site_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SiteError("site URL must be a complete http:// or https:// address")
    if parsed.username or parsed.password:
        raise SiteError("site URL must not include credentials")
    return url


def open_site(url: str) -> bool:
    """Open a user-approved URL in the operating system's default browser."""
    return webbrowser.open(validate_site_url(url), new=2)
