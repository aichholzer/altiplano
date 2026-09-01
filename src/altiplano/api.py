"""Talking to Vikunja, and normalising what it says back.

Three things live here because the tool modules should not each have to know them:
which API version is in play and how its verbs differ, how a request is sent and
how a failure is reported, and how a response is unwrapped and trimmed.

The leading underscores mark these as package-private, so the tool modules import
them freely. Nothing outside `altiplano` should.
"""

from typing import Any

import httpx

from altiplano.config import _base, _headers

# Vikunja has no null for a date. An unset one is Go's zero time, both on the
# wire and in the database, so writing this value back is how a date is cleared.
_NO_DATE = "0001-01-01T00:00:00Z"


# Vikunja 2.4.0 added a v2 API alongside v1. Paths are identical for everything
# this server does, but the verbs for create and update differ, so the version is
# taken from the URL the user configured. Nothing is probed, so pointing
# VIKUNJA_URL at /api/v2 is the whole opt-in.
# `replace` is the third action because three places need a whole-resource write:
# the task replace, a comment edit, and placing a task in a bucket. v1 spells that
# the same way it spells an update, which is the root of the hazard _replace_task
# exists to work around.
_VERBS = {
    1: {"create": "PUT", "update": "POST", "replace": "POST"},
    2: {"create": "POST", "update": "PATCH", "replace": "PUT"},
}


def _version() -> int:
    """2 when VIKUNJA_URL ends in /api/v2, otherwise 1."""
    return 2 if _base().endswith("/api/v2") else 1


def _verb(action: str) -> str:
    """The verb this API version uses for `create`, `update` or `replace`."""
    return _VERBS[_version()][action]


def _md_params() -> dict[str, str]:
    """Ask v2 to exchange rich-text fields as Markdown. v1 has no such option.

    Descriptions and comments are stored as HTML. v2 will convert in both
    directions, so callers can write Markdown and leave the HTML to the server. On
    v1 this is empty and the fields stay HTML.
    """
    return {"format": "markdown"} if _version() == 2 else {}


def _date(value: str) -> str:
    """Translate an empty string into the value Vikunja uses for no date.

    Dates can otherwise only be overwritten, never cleared: `None` means "leave
    this out of the payload", and an empty string is not a datetime Vikunja will
    parse. There is no null to send, so clearing one means writing the zero time.
    """
    return _NO_DATE if value == "" else value


def _error_detail(r: httpx.Response) -> str:
    """A message that says what the server actually objected to.

    httpx's own message stops at the status code, which for a rejected filter
    expression or a validation failure leaves an agent nothing to act on. Vikunja
    explains itself in the body: v2 follows RFC 9457 (`detail`, alongside a numeric
    `code`), v1 uses `message`.
    """
    where = f"{r.status_code} {r.reason_phrase} for {r.request.method} {r.request.url}"
    if r.has_redirect_location:
        # Nearly always a wrong VIKUNJA_URL, so name where the request was sent.
        return f"{where}: redirected to {r.headers['Location']}"
    try:
        payload = r.json()
    except ValueError:
        return where
    if not isinstance(payload, dict):
        return where
    detail = payload.get("detail") or payload.get("message") or payload.get("title")
    if not detail:
        return where
    code = payload.get("code")
    return f"{where}: {detail}" + (f" (code {code})" if code else "")


async def _send(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request. Raises on any non-2xx, carrying the server's own explanation.

    The check is `is_success`, so a redirect counts as a failure: it means the
    configured URL is wrong, and decoding its body as a result would hide that.
    """
    async with httpx.AsyncClient(base_url=_base(), headers=_headers(), timeout=30) as client:
        r = await client.request(method, path, **kwargs)
        if not r.is_success:
            raise httpx.HTTPStatusError(_error_detail(r), request=r.request, response=r)
        return r


def _decode(r: httpx.Response) -> Any:
    if r.status_code == 204 or not r.content:
        return {"ok": True}
    return r.json()


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    return _decode(await _send(method, path, **kwargs))


def _items(data: Any) -> list:
    """Normalise a collection response across both API versions.

    v1 returns a bare array, or a literal `null` for some empty collections. v2
    wraps every collection in a pagination envelope. The check is on shape, so a
    mismatch with the configured version cannot break it.

    Anything else means the response was not a collection at all: most likely a
    bodyless response, which `_request` reports as a status dict. That is an error,
    because reporting it as empty is indistinguishable from genuinely having no
    items.
    """
    if data is None:
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if not isinstance(data, list):
        raise RuntimeError(f"expected a list from the API, got {type(data).__name__}")
    return data


def _task_summary(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "identifier": t.get("identifier"),
        "title": t.get("title"),
        "done": t.get("done"),
        "priority": t.get("priority"),
    }
