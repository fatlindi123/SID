"""Conservative policy for read-only navigation in generated browser tests."""

import re
import unicodedata
from urllib.parse import urlsplit

from app.core.url_security import UnsafeTargetUrl, normalized_http_url


_SAFE_NAVIGATION_LABELS = frozenset(
    {
        "home",
        "about",
        "about us",
        "contact",
        "contact us",
        "help",
        "support",
        "faq",
        "documentation",
        "docs",
        "guide",
        "guides",
        "blog",
        "news",
        "product",
        "products",
        "catalog",
        "product catalog",
        "service",
        "services",
        "feature",
        "features",
        "pricing",
        "company",
        "team",
        "career",
        "careers",
        "resource",
        "resources",
        "login",
        "log in",
        "sign in",
        "next",
        "previous",
        "back",
    }
)

_SAFE_NAVIGATION_PATH_SEGMENTS = frozenset(
    {
        "home",
        "about",
        "about-us",
        "contact",
        "contact-us",
        "help",
        "support",
        "faq",
        "documentation",
        "docs",
        "guide",
        "guides",
        "blog",
        "news",
        "product",
        "products",
        "catalog",
        "service",
        "services",
        "feature",
        "features",
        "pricing",
        "company",
        "team",
        "career",
        "careers",
        "resource",
        "resources",
        "login",
        "signin",
        "sign-in",
        "index",
        "page",
        "pages",
        "next",
        "previous",
    }
)


def normalized_navigation_label(value: str | None) -> str | None:
    """Return a canonical allowlisted navigation name, or None when ambiguous."""
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        return None
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized = " ".join(normalized.split())
    return normalized if normalized in _SAFE_NAVIGATION_LABELS else None


def is_safe_navigation_target(value: str) -> bool:
    """Only allow saved, static content routes without query or fragment state."""
    try:
        parsed = urlsplit(value)
        normalized = normalized_http_url(value)
    except (UnsafeTargetUrl, TypeError, ValueError):
        return False
    if parsed.query or parsed.fragment:
        return False

    path = urlsplit(normalized).path
    segments = [segment.casefold() for segment in path.split("/") if segment]
    return all(segment in _SAFE_NAVIGATION_PATH_SEGMENTS for segment in segments)
