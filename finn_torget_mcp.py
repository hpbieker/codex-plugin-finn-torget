"""FINN Torget MCP API. Tools call Python API functions directly; no CLI subprocesses."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import base64
import html
from datetime import datetime
from html.parser import HTMLParser
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urljoin, urlencode, quote
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "core"))
import finn_torget_core as api  # noqa: E402

from listing_text_query import compile_query
from listing_fulltext import search_rows
from listing_fields import validate_include_fields, project_listing

DETAIL_FIELDS = ('price', 'images', 'condition', 'shoe_size', 'state', 'state_raw', 'state_source', 'publication_status', 'publication_status_verified', 'category', 'trade_type', 'for_rent', 'rental_status_verified', 'rental_status_source', 'description', 'search_metadata')
SUMMARY_FIELDS = ('subtitle', 'state', 'expires', 'url')
SEARCH_FIELDS = ('price', 'location', 'published', 'url', 'trade_type', 'ad_type')

mcp = FastMCP("FINN Torget")


_CONDITION_LABELS = (
    "Helt ny - Uåpnet/med lapp", "Som ny - Ikke synlig brukt",
    "Pent brukt - I god stand", "Godt brukt - Synlig brukt",
    "Må fikses - Noen mangler/ødelagte deler",
)


def _validate_condition(condition):
    if condition is None:
        return
    allowed = {value.casefold() for label in _CONDITION_LABELS
               for value in (label, label.split(" - ")[0])}
    allowed.update(str(i) for i in range(1, 6))
    if condition.strip().casefold() not in allowed:
        raise ValueError("Invalid condition. Use one of: " + "; ".join(_CONDITION_LABELS)
                         + ". No FINN draft was created. For an existing draft use get_listing_options(listing_id).")


def _form_config(html_path):
    raw = html.unescape(api._attr(api._read(html_path), "data-config"))
    return json.loads(raw) if raw.lstrip().startswith("{") else json.loads(
        base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))


def _owner_listing_state(owner_listing):
    value = owner_listing.get("state")
    raw = value.get("type") if isinstance(value, dict) else value
    label = value.get("label") if isinstance(value, dict) else None
    source_value = raw or label
    normalized = re.sub(r"[_-]+", " ", str(source_value or "")).strip().casefold()
    publication = {
        "draft": "draft", "utkast": "draft",
        "active": "published", "published": "published", "aktiv": "published",
        "pending": "under_review", "under review": "under_review",
        "in review": "under_review", "til kontroll": "under_review",
        "under moderation": "under_review",
    }.get(normalized, "unknown")
    display = label or {"draft": "Utkast", "published": "Aktiv",
                        "under_review": "Til kontroll"}.get(publication, raw)
    return {"state": display, "state_raw": raw or label,
            "state_source": "owner_by_id", "publication_status": publication,
            "publication_status_verified": publication != "unknown"}


def _listing_state(html_path, listing_id=None, *, owner_lookup=None):
    item = _form_config(html_path).get("item") or {}
    raw = item.get("state")
    state = str(raw).casefold() if isinstance(raw, str) else None
    if listing_id is not None:
        try:
            _, owner_listing = owner_lookup if owner_lookup is not None else _owner_listing_by_id(str(listing_id))
        except (Exception, SystemExit):
            owner_listing = None
            owner_lookup_failed = True
        else:
            owner_lookup_failed = False
        if owner_listing is not None:
            return _owner_listing_state(owner_listing)
        # An absent owner record (404/410) or failed lookup is not proof that
        # the editable form still represents a publishable draft.
        return {"state": raw, "state_raw": raw,
                "state_source": "owner_lookup_unverified" if owner_lookup_failed else "owner_listing_absent",
                "publication_status": "unknown", "publication_status_verified": False}
    if state == "draft":
        return {"state": "Utkast", "state_raw": raw,
                "state_source": "edit_form_draft", "publication_status": "draft",
                "publication_status_verified": True}
    return {"state": raw, "state_raw": raw, "state_source": "edit_form_unverified",
            "publication_status": "unknown", "publication_status_verified": False}


def _request(url: str, *, method: str = "GET", data: bytes | None = None,
             headers: dict[str, str] | None = None, timeout: int = 30,
             authenticated: bool = False) -> tuple[int, str, bytes]:
    request = Request(url, data=data, headers=headers or {}, method=method)
    if authenticated:
        opener = build_opener(HTTPCookieProcessor(api._safari_cookie_jar(url)), api._SafeRedirect())
    else:
        opener = build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.geturl(), response.read()
    except HTTPError as exc:
        return exc.code, exc.geturl(), exc.read()


class _AppConfigParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.config: str | None = None
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id") == "app" and values.get("data-config"):
            self.config = values.get("data-config")


def _message_context() -> tuple[str, str]:
    messages_url = "https://www.finn.no/messages"
    try:
        status, final_url, body = _request(messages_url, authenticated=True, timeout=60)
    except SystemExit as exc:
        # Cookie loading uses SystemExit; keep it a tool error, not a server exit.
        if str(exc).startswith("Safari has no FINN cookies"):
            raise RuntimeError("auth_required: Safari has no FINN cookies; sign in to FINN in Safari and retry") from None
        raise RuntimeError("safari_session_unavailable: Could not import the Safari session; check the local Safari cookie service and retry") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("messaging_network_error: Could not fetch FINN messages; check connectivity and retry") from None
    if status == 401:
        raise RuntimeError("auth_required: FINN messages returned HTTP 401; sign in to FINN in Safari and retry")
    if status == 403:
        raise RuntimeError("messaging_access_denied: FINN messages returned HTTP 403; check access to FINN messages in Safari")
    if status >= 400:
        raise RuntimeError(f"messaging_http_error: FINN messages returned HTTP {status}; retry later if FINN is temporarily unavailable")
    final = urlparse(final_url)
    if final.hostname in {"login.finn.no", "login.vend.no", "login.schibsted.com", "login.spid.no"} or (
        final.hostname == "www.finn.no" and final.path.startswith(("/login", "/auth"))
    ):
        raise RuntimeError("auth_required: FINN redirected to sign-in; sign in to FINN in Safari and retry")
    if final.scheme != "https" or final.netloc != "www.finn.no" or final.path.rstrip("/") != "/messages":
        raise RuntimeError("messaging_page_unexpected: FINN returned an unexpected page; check FINN messages in Safari before retrying")
    parser = _AppConfigParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    if not parser.config:
        raise RuntimeError("messaging_config_missing: FINN messages HTML has no app data-config; the page format may have changed. Update the FINN Torget plugin parser")
    try:
        config = json.loads(base64.b64decode(parser.config).decode("utf-8"))
    except (ValueError, UnicodeError):
        raise RuntimeError("messaging_config_invalid: FINN app data-config is not valid base64 JSON; update the FINN Torget plugin parser") from None
    if not isinstance(config, dict):
        raise RuntimeError("messaging_config_invalid: FINN app data-config must be a JSON object; update the FINN Torget plugin parser")
    raw_base = config.get("basePath")
    if not isinstance(raw_base, str) or not raw_base.strip():
        raise RuntimeError("messaging_config_invalid: FINN messaging basePath is missing; update the FINN Torget plugin parser")
    base = urljoin(messages_url, raw_base).rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "https" or parsed.netloc != "www.finn.no"
            or parsed.path != "/messages" or parsed.params or parsed.query or parsed.fragment):
        raise RuntimeError("messaging_config_invalid: FINN messaging basePath is not an expected FINN HTTPS messages URL; update the FINN Torget plugin parser")
    login_id = str(config.get("loginId") or "")
    if not login_id.isascii() or not login_id.isdigit():
        raise RuntimeError("messaging_login_missing: FINN configuration has no valid loginId; open FINN messages in Safari and verify sign-in before retrying")
    return base, login_id


def _message_json(url: str) -> object:
    status, _, body = _request(url, authenticated=True, timeout=60)
    return _parse_json_response(status, body, "FINN messages API")


def _conversation_groups(base: str, login_id: str, offset: int) -> list[dict]:
    query = urlencode({"offset": offset, "limit": 20, "numberOfConversationsInGroup": 10})
    data = _message_json(f"{base}/api/conversations/users/{login_id}/conversationgroups?{query}")
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _conversation_rows(group: dict, base: str, login_id: str, offset: int = 0) -> list[dict]:
    basis = group.get("groupBasis") or {}
    info = basis.get("itemInfo") or {}
    item_id, item_type = info.get("itemId"), info.get("itemType")
    raw = group.get("conversations") or []
    if offset < len(raw):
        return [r for r in raw[offset:offset + 10] if isinstance(r, dict)]
    if not item_id or not item_type:
        return []
    query = urlencode({"offset": offset, "limit": 10})
    data = _message_json(f"{base}/api/conversations/users/{login_id}/conversationgroups/{quote(str(item_type), safe='')}/{quote(str(item_id), safe='')}?{query}")
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _finn_summary(row: dict, group: dict | None = None) -> dict:
    basis = (group or {}).get("groupBasis") or {}
    info = basis.get("itemInfo") or {}
    return {"conversation_id": row.get("conversationId"), "platform": "finn_torget",
        "listing_id": row.get("itemId") or info.get("itemId"), "listing_type": row.get("itemType") or info.get("itemType"),
        "title": info.get("title"), "partner": row.get("partnerName"), "updated_at": row.get("lastMessageDate"),
        "unread_count": row.get("unseenCounter", 0), "preview": row.get("lastMessagePreview"),
        "outgoing": row.get("lastMessageOutgoing"), "status": info.get("status")}


def _finn_messages(base: str, login_id: str, conversation_id: str, before: str | None, size: int) -> list[dict]:
    params: dict[str, object] = {"order": "asc", "size": size}
    if before:
        params.update({"next": "false", "dateTimeFromLongMilliseconds": before})
    data = _message_json(f"{base}/api/conversations/users/{login_id}/conversations/{conversation_id}/messages?{urlencode(params)}")
    if isinstance(data, dict):
        rows = data.get("messageResponseList") or []
        return [r for r in rows if isinstance(r, dict)]
    return []


@contextmanager
def _html_state(listing_id: str):
    _, _, body = _request(f"https://www.finn.no/recommerce/create/{listing_id}", authenticated=True, timeout=60)
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".html", prefix="finn-mcp-", dir="/private/tmp", delete=True) as tmp:
        tmp.write(body)
        tmp.flush()
        yield tmp.name


def _parse_json_response(status: int, body: bytes, service: str):
    if status >= 400:
        raise RuntimeError(f"{service} returned HTTP {status}")
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(f"{service} returned invalid JSON") from exc


def _download_image(url: str, output_path: str) -> dict:
    parsed = urlparse(url)
    allowed = {"finn.no", "finncdn.no"}
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise ValueError("Image URL is not on an expected FINN HTTPS image host")
    destination = Path(output_path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {destination.parent}")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
    temporary_path: Path | None = None
    try:
        with urlopen(request, timeout=60) as response:
            final = urlparse(response.geturl())
            final_host = (final.hostname or "").lower()
            if final.scheme != "https" or not any(final_host == domain or final_host.endswith("." + domain) for domain in allowed):
                raise ValueError("Image download redirected outside expected FINN HTTPS hosts")
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise RuntimeError(f"Expected an image response, got {content_type}")
            data = response.read(50 * 1024 * 1024 + 1)
        if not data:
            raise RuntimeError("Image response was empty")
        if len(data) > 50 * 1024 * 1024:
            raise RuntimeError("Image exceeds the 50 MiB download limit")
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".codex-image-", delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            raise FileExistsError(f"Refusing to overwrite existing file: {destination}") from None
        return {"path": str(destination), "bytes": len(data), "content_type": content_type}
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@mcp.tool()
def search_listings(
    query: Annotated[str, "Search words for FINN Torget."],
    category_ids: Annotated[list[str] | None, "FINN category IDs."] = None,
    sub_category_ids: Annotated[list[str] | None, "FINN subcategory IDs."] = None,
    location: Annotated[list[str] | None, "FINN location codes, not place names. Example: Oslo = 0.20061. Codes use dotted numeric hierarchy, e.g. 0.20061 or 1.20003.20046. Invalid names are rejected locally."] = None,
    condition: Annotated[list[str] | None, "Condition labels or FINN values."] = None,
    price_from: int | None = None,
    price_to: int | None = None,
    for_rent: Annotated[bool | None, "None/omitted: all ads; true: rent only; false: buy only."] = None,
    limit: Annotated[int, "Maximum listings returned from this API page (1–100). Follow next_page and next_page_offset to continue without skipping unreturned items."] = 20,
    include_fields: Annotated[list[str] | None, 'Additional supported fields: price, location, published, url, trade_type, ad_type. Use [*] alone for all fields already available in this operation; never adds detail requests. Defaults: id and title. Explicit id/title selections are also accepted.'] = None,
    page: Annotated[int, "Upstream result page, starting at 1. Use returned next_page when continuing the same filters."] = 1,
    page_offset: Annotated[int, "Offset within that API page, starting at 0. Use returned next_page_offset with next_page; avoids skipping results when limit is smaller than a full API page."] = 0,
) -> dict:
    """Search public FINN Torget ads and return compact listing data.

    total is the query-wide match count, not the page size. Continue with both
    next_page and next_page_offset; has_more describes accessible continuation.
    FINN can cap accessible pages below the total match count.

    If own listing IDs are already known, prefer get_listing for one or get_listings_many for several instead of searching/listing again.
    """
    if type(page) is not int or page < 1 or type(page_offset) is not int or page_offset < 0:
        raise ValueError("page must be a positive integer; page_offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if location is not None and (not isinstance(location, list) or any(
            not isinstance(code, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", code) is None
            for code in location)):
        raise ValueError("location requires FINN location codes, not place names; for Oslo use location=['0.20061'], not ['Oslo']")
    extra_fields = validate_include_fields(include_fields, ('price', 'location', 'published', 'url', 'trade_type', 'ad_type'))
    args = argparse.Namespace(query=query, sort=None, price_from=price_from, price_to=price_to,
        published=None, page=page, location=location or [], category=category_ids or [],
        sub_category=sub_category_ids or [], product_category=[], condition=condition or [],
        trade_type=None, for_rent=None if for_rent is None else ("rent" if for_rent else "buy"), seller=None, param=[],
        search_key=api.DEFAULT_SEARCH_KEY)
    url = api._search_api_url(args)
    status, _, body = _request(url, headers={"User-Agent": "codex-finn-torget/1.0", "Accept": "application/json"})
    data = _parse_json_response(status, body, "FINN search")
    docs = []
    page_docs = data.get("docs") or []
    if page_offset > len(page_docs):
        raise ValueError("page_offset exceeds the number of records in this API page; use the returned continuation values")
    for item in page_docs[page_offset:page_offset + limit]:
        price = item.get("price") or {}
        docs.append({"id": item.get("id") or item.get("ad_id"), "title": item.get("heading"),
            "price": price.get("amount") if isinstance(price, dict) else price,
            "location": item.get("location"), "published": item.get("timestamp"),
            "url": item.get("canonical_url"), "trade_type": item.get("trade_type"),
            "ad_type": item.get("ad_type")})
    metadata = data.get("metadata") or {}
    rental_facets = {}
    for facet in data.get("filters") or []:
        if facet.get("name") != "for_rent":
            continue
        for row in facet.get("filter_items") or []:
            key = {"0": "buy", "1": "rent"}.get(str(row.get("value")))
            hits = row.get("hits")
            if key is not None and type(hits) is int and hits >= 0:
                rental_facets[key] = hits
    total = (metadata.get("result_size") or {}).get("match_count")
    if type(total) is not int or total < 0:
        total = None  # num_results is page-local, never an authoritative total.
    paging = metadata.get("paging") or {}
    current = paging.get("current", page)
    last = paging.get("last")
    if type(current) is not int or current != page:
        raise RuntimeError("FINN returned a different result page than requested")
    consumed = page_offset + len(docs)
    if consumed < len(page_docs):
        has_more, next_page, next_page_offset = True, page, consumed
    elif type(last) is int:
        has_more = page < last and bool(page_docs)
        next_page, next_page_offset = (page + 1, 0) if has_more else (None, None)
    elif metadata.get("is_end_of_paging") is True or total == 0:
        has_more, next_page, next_page_offset = False, None, None
    else:
        has_more, next_page, next_page_offset = None, None, None
    return {"total": total, "page": page, "page_offset": page_offset,
            "returned": len(docs), "has_more": has_more,
            "next_page": next_page, "next_page_offset": next_page_offset, "listings": [project_listing(row, extra_fields) for row in docs], "include_fields": list(extra_fields),
            "rental_filter": "all" if for_rent is None else ("rent" if for_rent else "buy"),
            "rental_facets": rental_facets}


def _listing_search_metadata(listing_id: str) -> dict:
    """Classify only an exact singleton ID hit using FINN's rent/buy facet counts."""
    search = search_listings(query=str(listing_id), limit=2)
    rows = search["listings"]
    exact_match = (search["total"] == 1 and len(rows) == 1
                   and str(rows[0].get("id")) == str(listing_id))
    facets = search["rental_facets"]
    for_rent = None
    if exact_match and facets == {"buy": 0, "rent": 1}:
        for_rent = True
    elif exact_match and facets == {"buy": 1, "rent": 0}:
        for_rent = False
    return {"source": "public_search_facets", "exact_id_match": exact_match,
            "for_rent": for_rent, "verified": for_rent is not None,
            "rental_facets": facets,
            "trade_type": rows[0].get("trade_type") if exact_match else None,
            "ad_type": rows[0].get("ad_type") if exact_match else None}


def categories(
    kind: Annotated[str, "One of category, sub_category, product_category, location, or all."] = "category",
    query: str = "",
) -> list[dict]:
    """Fetch current FINN search facets for category and location IDs."""
    if kind not in {"category", "sub_category", "product_category", "location", "all"}:
        raise ValueError("Unsupported facet kind")
    args = argparse.Namespace(query=query, sort=None, price_from=None, price_to=None,
        published=None, page=None, location=[], category=[], sub_category=[], product_category=[],
        condition=[], trade_type=None, for_rent=None, seller=None, param=[], search_key=api.DEFAULT_SEARCH_KEY)
    status, _, body = _request(api._search_api_url(args), headers={"User-Agent": "codex-finn-torget/1.0", "Accept": "application/json"})
    data = _parse_json_response(status, body, "FINN categories")
    rows = api._filter_rows_from_data(data, {kind} if kind != "all" else {"category", "sub_category", "product_category", "location"})
    return api._dedupe_filter_rows(rows)


def _catalog_rows():
    return [dict(row, creation_category_id=int(row["value"].split(".")[-1])
                 if row["kind"] in {"sub_category", "product_category"} else None)
            for row in categories(kind="all")
            if row["kind"] != "location" and row["value"].split(".")[-1].isdigit()]


@mcp.tool()
def list_categories(limit: int = 100, offset: int = 0) -> dict:
    """List FINN category paths. Non-null creation_category_id can be used with create_listing."""
    rows = _catalog_rows()
    limit, offset = max(1, min(limit, 250)), max(0, offset)
    return {"categories": rows[offset:offset + limit], "total": len(rows),
            "next_offset": offset + limit if offset + limit < len(rows) else None}


@mcp.tool()
def search_categories(query: str, limit: int = 20) -> dict:
    """Search FINN category names and paths literally; does not suggest categories."""
    needle = query.strip().casefold()
    if not needle:
        raise ValueError("query cannot be empty")
    matches = [row for row in _catalog_rows() if needle in row["path"].casefold()]
    return {"categories": matches[:max(1, min(limit, 50))], "total": len(matches)}


@mcp.tool()
def list_listings(
    limit: int = 30,
    offset: int = 0,
    status_facet: Annotated[str, "Owner status filter: ACTIVE = active ads (default); DRAFT = started/unfinished ads (Påbegynt); DISPOSED = finished ads, including sold ads; ALL = all statuses."] = "ACTIVE",
    search: str | None = None,
    include_fields: Annotated[list[str] | None, 'Additional supported fields: subtitle, state, expires, url. Use [*] alone for all fields already available in this operation; never adds detail requests. Defaults: id and title. Explicit id/title selections are also accepted.'] = None,
) -> dict:
    """List only the user's FINN Torget ads (RECOMMERCE) using the current Safari login.

    If own listing IDs are already known, prefer get_listing for one or get_listings_many for several instead of searching/listing again.
    """
    extra_fields = validate_include_fields(include_fields, ('subtitle', 'state', 'expires', 'url'))
    facet = status_facet.strip().upper()
    if not facet:
        raise ValueError("status_facet cannot be empty")
    args = argparse.Namespace(limit=max(1, min(limit, 100)), search=search, facet=facet, type="RECOMMERCE")
    url = api._my_items_summary_url(args, max(0, offset))
    status, _, body = _request(url, authenticated=True, timeout=60)
    data = _parse_json_response(status, body, "FINN owner listings")
    ads = []
    for row in data.get("summaries") or []:
        detail = row.get("data") or {}
        state = row.get("state") or {}
        ads.append({"id": row.get("id"), "title": detail.get("title"), "subtitle": detail.get("subtitle"),
            "state": state.get("label") or state.get("type"), "expires": row.get("expires"),
            "url": f"https://www.finn.no/{row.get('id')}"})
    return {"total": data.get("total", 0), "listings": [project_listing(row, extra_fields) for row in ads], "include_fields": list(extra_fields)}


class UnsupportedListingType(ValueError):
    """The owner record identifies an ad outside FINN Torget."""


def _torget_owner_lookup(listing_id: str):
    lookup = _owner_listing_by_id(listing_id)
    row = lookup[1]
    if row is not None:
        vertical = row.get("vertical")
        model = row.get("model")
        if ((isinstance(vertical, str) and vertical and vertical != "RECOMMERCE")
                or (not vertical and model in {"car-used", "realestate-leisure-sale", "realestate-letting"})):
            raise UnsupportedListingType("Annonsetypen støttes ikke av FINN Torget.")
    return lookup


@mcp.tool()
def get_listing(
    listing_id: Annotated[str, "FINN Torget ad ID."],
    include_description: bool = True,
    include_search_metadata: Annotated[bool, "Read rent/buy metadata from one exact public search. A search miss remains unknown and does not affect owner publication status."] = True,
) -> dict:
    """Read owner contents/status and rent/buy metadata for one FINN Torget ad.

    Preferred when one own listing ID is already known; avoid searching for it again.
    Known non-Torget ad types are rejected before retrieving the Torget edit form.
    """
    try:
        owner_lookup = _torget_owner_lookup(listing_id)
    except UnsupportedListingType:
        raise
    except (Exception, SystemExit):
        # Preserve edit-form reads when owner type metadata is unavailable.
        owner_lookup = None
    with _html_state(listing_id) as html_path:
        returned_id, _, data = api._item_put_body_from_create_html(html_path)
        result = {"id": returned_id, "title": api._item_title(data), "price": api._price_amount(data),
            "images": data.get("image") or [], "condition": data.get("condition"), "shoe_size": data.get("shoe_size")}
        result.update(_listing_state(html_path, str(returned_id), owner_lookup=owner_lookup))
        result["category"] = data.get("category")
        result["trade_type"] = data.get("trade_type")
        result.update({"for_rent": None, "rental_status_verified": False,
                       "rental_status_source": None})
        if include_description:
            result["description"] = data.get("description") or ""
        if include_search_metadata:
            try:
                search_metadata = _listing_search_metadata(str(returned_id))
            except (Exception, SystemExit) as exc:
                search_metadata = {"source": "public_search_facets", "for_rent": None,
                                   "verified": False, "error": type(exc).__name__}
            result["search_metadata"] = search_metadata
            result["for_rent"] = search_metadata["for_rent"]
            result["rental_status_verified"] = search_metadata["verified"]
            if search_metadata["verified"]:
                result["rental_status_source"] = search_metadata["source"]
        return result


@mcp.tool()
def search_listings_fulltext(
    query: Annotated[str, 'Search own titles and descriptions using OmniFocus syntax: spaces/AND, OR, "phrases", -exclusion, parentheses. Case-insensitive substrings.'],
    scan_limit: Annotated[int, "Maximum own ads to retrieve (1–500). Costly: separate detail retrieval per ad, up to four concurrently."] = 100,
    limit: Annotated[int, "Maximum matching ads to return (1–100). Does not reduce scan cost."] = 50,
    status_facet: Annotated[list[Literal["ACTIVE", "DRAFT", "DISPOSED"]], Field(min_length=1, max_length=3, description="Owner statuses to scan (OR). Default: [ACTIVE]. ACTIVE = active ads; DRAFT = started/unfinished ads; DISPOSED = finished/sold ads. ALL is unsupported here, but remains supported by list_listings.")] = ["ACTIVE"],
    include_fields: Annotated[list[str] | None, 'Additional fields from already-fetched details: price, images, condition, shoe_size, state, state_raw, state_source, publication_status, publication_status_verified, category, trade_type, for_rent, rental_status_verified, rental_status_source, description, search_metadata, snippets, url. Use [*] alone for all supported details and snippets. Defaults: listing_id and title; no extra API calls. Explicit id/title selections are also accepted.'] = None,
) -> dict:
    """Search own listing titles and full descriptions locally; expensive, not an upstream fulltext API.

    status_facet is an array: ["ACTIVE"] by default; select DRAFT and/or DISPOSED
    explicitly when needed. Multiple statuses mean OR. ALL is unsupported here;
    list_listings still accepts ALL for summaries. scan_limit applies across all
    selected statuses, and each listing is retrieved at most once.
    Returns ID/title matches and coverage. Select descriptions or excerpts via include_fields.
    Includes per-ad failures; no retries.
    Terms may match across fields; quoted phrases must match within one field.
    Uppercase AND/OR are operators; lowercase and/or are literal words.

    If own listing IDs are already known, prefer get_listing for one or get_listings_many for several instead of searching/listing again.
    """
    if (not isinstance(status_facet, list) or not 1 <= len(status_facet) <= 3
            or any(not isinstance(value, str) or value not in {"ACTIVE", "DRAFT", "DISPOSED"}
                   for value in status_facet)
            or len(set(status_facet)) != len(status_facet)):
        raise ValueError("status_facet must be a non-empty array of unique ACTIVE, DRAFT, and/or DISPOSED values; ALL is supported only by list_listings")
    extra_fields = validate_include_fields(include_fields, ('price', 'images', 'condition', 'shoe_size', 'state', 'state_raw', 'state_source', 'publication_status', 'publication_status_verified', 'category', 'trade_type', 'for_rent', 'rental_status_verified', 'rental_status_source', 'description', 'search_metadata', 'snippets', 'url'))
    expression = compile_query(query)  # Reject invalid queries before any network work.
    if not 1 <= scan_limit <= 500 or not 1 <= limit <= 100:
        raise ValueError("scan_limit must be 1–500 and limit must be 1–100")
    rows, seen = [], set()
    total = 0
    enumeration_complete = True
    for facet in status_facet:
        offset, facet_seen = 0, set()
        facet_complete = False
        while True:
            # Even if the shared cap is reached, one summary request supplies the
            # total for this status. No additional detail requests are made.
            remaining = scan_limit - len(rows)
            page = list_listings(limit=max(1, min(50, remaining)), offset=offset,
                                 status_facet=facet, include_fields=["url"])
            facet_total = page["total"]
            if offset == 0:
                total += facet_total
            if remaining <= 0:
                facet_complete = facet_total == 0
                break
            raw = page["listings"]
            overlap = False
            for row in raw:
                value = str(row["id"])
                if value in facet_seen or value in seen:
                    overlap = True
                facet_seen.add(value)
                if value not in seen and len(rows) < scan_limit:
                    seen.add(value)
                    rows.append({"id": value, "url": row["url"]})
            offset += len(raw)
            if overlap:
                break  # Changed/overlapping pages cannot establish full coverage.
            if len(facet_seen) >= facet_total and len(facet_seen) == offset:
                facet_complete = True
                break
            if not raw or len(rows) >= scan_limit:
                break
        enumeration_complete = enumeration_complete and facet_complete
    result = search_rows(expression, query, rows, get_listings_many, limit,
                         total, enumeration_complete, include_fields=extra_fields)
    result["status_facet"] = list(status_facet)
    return result


@mcp.tool()
def get_listings_many(
    listing_ids: Annotated[list[str], "1–50 FINN owner ad IDs. One separate detail retrieval per ID."],
    include_search_metadata: Annotated[bool, "Perform an additional public rent/buy search per ad. Slower; disabled by default."] = False,
    include_fields: Annotated[list[str] | None, 'Additional supported fields: price, images, condition, shoe_size, state, state_raw, state_source, publication_status, publication_status_verified, category, trade_type, for_rent, rental_status_verified, rental_status_source, description, search_metadata. Use [*] alone for all fields already available in this operation; never adds detail requests. Defaults: id and title. Explicit id/title selections are also accepted.'] = None,
) -> dict:
    """Read full owner listings, including descriptions, with separate costly detail retrievals.

    This is NOT an upstream bulk API: each ID triggers its own retrieval, with
    at most four in parallel. Returns ordered per-ID successes/errors; no retries.

    Preferred when multiple own listing IDs are already known; use include_fields to return only needed details. Avoid searching for known IDs again.
    """
    extra_fields = validate_include_fields(include_fields, ('price', 'images', 'condition', 'shoe_size', 'state', 'state_raw', 'state_source', 'publication_status', 'publication_status_verified', 'category', 'trade_type', 'for_rent', 'rental_status_verified', 'rental_status_source', 'description', 'search_metadata'))
    if not 1 <= len(listing_ids) <= 50:
        raise ValueError("Provide between 1 and 50 listing IDs")
    if any(not isinstance(value, str) or not value.isascii() or not value.isdigit() or int(value) <= 0 for value in listing_ids):
        raise ValueError("Listing IDs must be positive numeric strings")
    def read_one(listing_id):
        try:
            listing = get_listing(listing_id, include_description=True,
                                  include_search_metadata=include_search_metadata)
            if str(listing.get("id")) != str(listing_id):
                raise RuntimeError("Returned listing ID did not match the requested ID")
            return {"listing_id": listing_id, "listing": project_listing(listing, extra_fields)}
        except UnsupportedListingType:
            return {"listing_id": listing_id, "error": {
                "type": "UnsupportedListingType", "code": "unsupported_listing_type",
                "message": "Annonsetypen støttes ikke av FINN Torget."}}
        except (Exception, SystemExit) as exc:
            # Raw transport errors can contain authenticated URLs or response data.
            return {"listing_id": listing_id, "error": {"type": type(exc).__name__,
                    "message": "Could not retrieve this listing; check its ID and Safari sign-in."}}

    with ThreadPoolExecutor(max_workers=min(4, len(listing_ids))) as pool:
        results = list(pool.map(read_one, listing_ids))
    succeeded = sum("listing" in result for result in results)
    return {"mode": "per_listing_requests", "include_fields": list(extra_fields), "requested": len(listing_ids),
            "succeeded": succeeded, "failed": len(results) - succeeded,
            "results": results}


@mcp.tool()
def get_listing_options(listing_id: str | None = None, category_id: int | None = None) -> dict:
    """List supported condition choices before creation, or read live choices for an existing ad/draft."""
    if listing_id is None:
        return {"category": category_id, "source": "supported_condition_catalog",
                "category_applicability_verified": False,
                "fields": {"condition": {"options": [{"value": str(i), "label": label}
                    for i, label in enumerate(_CONDITION_LABELS, 1)]}}}
    with _html_state(listing_id) as html_path:
        returned_id, _, data = api._item_put_body_from_create_html(html_path)
        if str(returned_id) != str(listing_id):
            raise RuntimeError("FINN returned a different ad ID")
        selected = category_id if category_id is not None else data.get("category")
        result = {"id": returned_id, "category": selected, "source": "live_form_by_id",
                  "category_applicability_verified": True, "fields": {}}
        for field in _form_config(html_path).get("model") or []:
            if field.get("name") not in {"condition", "shoe_size"}:
                continue
            dependencies = (field.get("dependencies") or {}).get("category")
            applicable = not dependencies or str(selected) in {str(value) for value in dependencies}
            result["fields"][field["name"]] = {"available": applicable,
                "options": [{"value": row.get("value"), "label": row.get("label")}
                            for row in field.get("options") or []] if applicable else []}
        return result


def _owner_listing_by_id(listing_id: str) -> tuple[int, dict | None]:
    status, _, body = _request(f"https://www.finn.no/my-items/api/single?adId={listing_id}",
                               authenticated=True)
    if status in {404, 410}:
        return status, None
    result = _parse_json_response(status, body, "FINN owner listing by ID")
    if not isinstance(result, dict) or str(result.get("id")) != str(listing_id):
        raise RuntimeError("FINN did not return the requested owner listing ID")
    return status, result


@mcp.tool()
def delete_listing(listing_id: Annotated[str, "ID of the user's FINN listing to permanently delete."]) -> dict:
    """Permanently delete one explicitly selected owner listing. Never retries deletion automatically."""
    if not listing_id.isdigit() or int(listing_id) <= 0:
        raise ValueError("listing_id must be a positive numeric FINN ID")
    _, before = _owner_listing_by_id(listing_id)
    if before is None:
        return {"listing_id": listing_id, "deleted": False, "already_absent": True,
                "verified": True, "verified_by": "listing_id", "retry_deletion": False}
    expected_path = f"/items/{listing_id}"
    actions = [action for action in before.get("actions") or [] if isinstance(action, dict)
               and action.get("name") == "DELETE" and action.get("method") == "DELETE"
               and action.get("path") == expected_path]
    if len(actions) != 1:
        raise ValueError("FINN does not offer a DELETE action for this listing; no deletion was sent")
    status, _, body = _request("https://www.finn.no/my-items", authenticated=True)
    if status >= 400:
        raise RuntimeError(f"FINN owner page returned HTTP {status}; no deletion was sent")
    match = re.search(r"window\.MYADS_STATE\s*=\s*", body.decode("utf-8", errors="replace"))
    if not match:
        raise RuntimeError("FINN did not expose owner-page configuration; no deletion was sent")
    config, _ = json.JSONDecoder().raw_decode(body.decode("utf-8", errors="replace")[match.end():])
    token = config.get("csrfToken") if isinstance(config, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("FINN did not expose a CSRF token; no deletion was sent")
    write_error = None
    accepted = False
    try:
        status, _, _ = _request(f"https://www.finn.no/my-items/api/action{expected_path}",
            method="DELETE", headers={"CSRF-Token": token}, authenticated=True)
        accepted = status < 400
        if not accepted:
            write_error = f"HTTP {status}"
    except (Exception, SystemExit) as exc:
        write_error = type(exc).__name__
    try:
        _, after = _owner_listing_by_id(listing_id)
        deleted = after is None
        verification_error = None
    except (Exception, SystemExit) as exc:
        deleted = False
        verification_error = type(exc).__name__
    return {"listing_id": listing_id, "title": (before.get("data") or {}).get("title"),
            "delete_request_accepted": accepted, "deleted": deleted, "verified": deleted,
            "verified_by": "listing_id" if deleted else None,
            "outcome": "deleted" if deleted else "unconfirmed",
            "request_error": write_error, "verification_error": verification_error,
            "retry_deletion": False}


@mcp.tool()
def download_image(
    listing_id: Annotated[str, "FINN Torget ad ID."],
    image_index: Annotated[int, "One-based image position on the ad."],
    output_path: Annotated[str, "Destination path in an existing local directory. Existing files are never overwritten."],
) -> dict:
    """Download one image from the signed-in owner's FINN Torget ad."""
    with _html_state(listing_id) as html_path:
        returned_id, _, data = api._item_put_body_from_create_html(html_path)
    images = data.get("image") or []
    if not 1 <= image_index <= len(images):
        raise ValueError(f"Image position must be between 1 and {len(images)}")
    image = images[image_index - 1]
    uri = image.get("uri")
    if not isinstance(uri, str) or not uri:
        raise RuntimeError("FINN did not return a downloadable image URI")
    image_url = urljoin("https://images.finncdn.no/dynamic/original/", uri.lstrip("/"))
    result = _download_image(image_url, output_path)
    result.update({"listing_id": returned_id, "image_index": image_index,
                   "image_uri": uri})
    return result


@mcp.tool()
def update_listing(
    listing_id: Annotated[str, "FINN Torget ad ID."],
    title: str | None = None,
    description: str | None = None,
    price: int | None = None,
    condition: Annotated[str | None, "Optional: Helt ny, Som ny, Pent brukt, Godt brukt, or Må fikses. Do not use the ambiguous Brukt."] = None,
    shoe_size: str | None = None,
    image_paths: list[str] | None = None,
    verify_public: bool = False,
) -> dict:
    """Update selected fields on one FINN ad and verify the saved owner state."""
    _validate_condition(condition)
    if all(value is None for value in (title, description, price, condition, shoe_size)) and not image_paths:
        raise ValueError("Provide at least one field to update")
    with _html_state(listing_id) as html_path:
        returned_id, etag, data = api._item_put_body_from_create_html(html_path)
        args = argparse.Namespace(ad_id=listing_id, title=title, description=description, description_file=None,
            price=price, condition=condition, shoe_size=shoe_size, image=[], select_html_path=html_path)
        changes = (api._update_fields_from_args(data, args)
                   if any(value is not None for value in (title, description, price, condition, shoe_size)) else {})
        if str(returned_id) != str(listing_id):
            raise RuntimeError("FINN returned a different ad ID; no update was sent")
    uploaded_uris = []
    upload_error = None
    for path in image_paths or []:
        try:
            image = api._upload_item_image(listing_id, path)
        except (Exception, SystemExit) as exc:
            upload_error = exc
            break
        data.setdefault("image", []).append(image)
        uploaded_uris.append(image["uri"])
    expected_images = [image.get("uri") for image in data.get("image") or []]

    def verify(updated):
        return ((title is None or api._item_title(updated) == title)
            and (description is None or str(updated.get("description") or "") == description)
            and (price is None or str(api._price_amount(updated)) == str(price))
            and (condition is None or str(updated.get("condition")) == str(args.expected_select_fields.get("condition")))
            and (shoe_size is None or str(updated.get("shoe_size")) == str(args.expected_select_fields.get("shoe_size")))
            and (not image_paths or [image.get("uri") for image in updated.get("image") or []] == expected_images))

    updated, result = _commit_item_update(listing_id, etag, data, verify, upload_error=upload_error)
    result.update({"title": api._item_title(updated) if updated is not None else None,
                   "price": api._price_amount(updated) if updated is not None else None})
    if image_paths:
        result["image_changes"] = {"uploaded_count": len(uploaded_uris),
            "expected_image_uris": expected_images if upload_error is None else None,
            "uploaded_image_uris": uploaded_uris,
            "image_uris": [image.get("uri") for image in updated.get("image") or []] if updated is not None else None,
            "verified": upload_error is None and updated is not None
                and [image.get("uri") for image in updated.get("image") or []] == expected_images}
    if verify_public and description is not None and result["verified"]:
        try:
            public_status, _, public_body = _request(f"https://www.finn.no/{listing_id}")
            result["public_verified"] = public_status < 400 and description in public_body.decode("utf-8", errors="replace")
        except (Exception, SystemExit) as exc:
            result["public_verified"] = False
            result["public_verification_error"] = type(exc).__name__
    result["changed_fields"] = [key for key in ("title", "description_chars", "price_amount", "condition", "shoe_size") if key in changes]
    return result


@mcp.tool()
def saved_searches() -> list[dict]:
    """Read saved FINN search names, IDs, and notification settings."""
    status, _, body = _request(api.SAVED_SEARCH_SETTINGS_URL,
        headers={"Accept": "application/json"}, authenticated=True, timeout=60)
    rows = _parse_json_response(status, body, "FINN saved searches")
    if not isinstance(rows, list):
        raise RuntimeError("FINN saved search response was not a list")
    return [api._saved_search_summary(row) for row in rows if isinstance(row, dict)]


def _notification_codes(notifications):
    mapping = {"web": "NC", "push": "PUSH", "email": "EMAIL"}
    if any(value not in mapping for value in notifications):
        raise ValueError("notifications must contain only web, push, and/or email")
    return list(dict.fromkeys(mapping[value] for value in notifications))


def _saved_search_rows(status, body, context):
    rows = _parse_json_response(status, body, context)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError(context + " was not a list of saved searches")
    return rows


@mcp.tool()
def update_saved_search(
    search_id: int,
    notifications: Annotated[list[str], "Complete desired set: any of web, push, email; an empty list disables notifications."],
) -> dict:
    """Replace notifications for one saved FINN search and verify the saved settings."""
    expected = _notification_codes(notifications)
    if search_id <= 0:
        raise ValueError("search_id must be positive")
    status, _, body = _request(api.SAVED_SEARCH_SETTINGS_URL, headers={"Accept": "application/json"}, authenticated=True)
    rows = _saved_search_rows(status, body, "FINN saved searches")
    selected = api._select_saved_search(rows, search_id, None)
    payload = {key: selected.get(key) for key in ("id", "type", "userKey", "searchKey", "description", "parameters")}
    payload["notifications"] = expected
    status, _, _ = _request(api.SAVED_SEARCH_SETTINGS_URL, method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"}, authenticated=True)
    if status >= 400:
        raise RuntimeError(f"FINN notification update failed with HTTP {status}")
    # The endpoint may return an empty body; always verify with a fresh GET.
    status, _, body = _request(api.SAVED_SEARCH_SETTINGS_URL, headers={"Accept": "application/json"}, authenticated=True)
    verified_rows = _saved_search_rows(status, body, "FINN saved searches readback")
    verified = api._select_saved_search(verified_rows, search_id, None)
    actual = verified.get("notifications") or []
    if set(actual) != set(expected):
        raise RuntimeError("FINN notification preferences did not match the requested notifications")
    return {"id": search_id, "name": verified.get("description"), "notifications": actual, "verified": True}


@mcp.tool()
def create_saved_search(
    query: str,
    name: str | None = None,
    category_ids: list[str] | None = None,
    location: list[str] | None = None,
    price_from: int | None = None,
    price_to: int | None = None,
    notifications: Annotated[list[str] | None, "Desired web, push, email notifications; [] disables all, omitted enables all three."] = None,
) -> dict:
    """Create a saved FINN search from live search metadata and verify it by ID."""
    chosen_channels = ["web", "push", "email"] if notifications is None else notifications
    expected_notifications = _notification_codes(chosen_channels)
    args = argparse.Namespace(query=query, name=name, channel=chosen_channels,
        search_key=api.DEFAULT_SEARCH_KEY, sort=None, price_from=price_from, price_to=price_to,
        published=None, page=None, location=location or [], category=category_ids or [],
        sub_category=[], product_category=[], condition=[], trade_type=None, for_rent=None,
        seller=None, param=[], timeout=30)
    status, _, body = _request(api._search_api_url(args), headers={"User-Agent": "codex-finn-torget/1.0", "Accept": "application/json"})
    metadata_data = _parse_json_response(status, body, "FINN saved search metadata")
    metadata = metadata_data.get("metadata") or {}
    if not metadata.get("is_savable_search"):
        raise ValueError("FINN reports that this search cannot be saved")
    descriptions = metadata.get("descriptions") or {}
    label = (name or descriptions.get("saved_search") or query).strip()
    if not label or len(label) > 50:
        raise ValueError("Saved-search name must contain 1 to 50 characters")
    search_key, parameters = metadata.get("search_key"), metadata.get("params")
    if not search_key or not isinstance(parameters, dict):
        raise RuntimeError("FINN search metadata is missing saveable parameters")
    payload = {"description": label, "notifications": expected_notifications,
        "type": "alert", "searchKey": search_key, "parameters": parameters}
    status, _, body = _request(api.SAVED_SEARCH_SETTINGS_URL, headers={"Accept": "application/json"}, authenticated=True)
    before = _saved_search_rows(status, body, "FINN saved searches")
    before_ids = {int(row["id"]) for row in before if str(row.get("id") or "").isdigit()}
    try:
        status, _, create_body = _request(api.SAVED_SEARCH_CREATE_URL, method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, authenticated=True)
    except (Exception, SystemExit) as exc:
        raise RuntimeError("FINN saved search creation outcome is uncertain; inspect saved_searches before retrying") from exc
    if status >= 400:
        raise RuntimeError(f"FINN saved search creation failed with HTTP {status}")
    try:
        status, _, body = _request(api.SAVED_SEARCH_SETTINGS_URL, headers={"Accept": "application/json"}, authenticated=True)
        after = _saved_search_rows(status, body, "FINN saved searches readback")
        created_id = api._created_saved_search_id(create_body.decode("utf-8", errors="replace"), before_ids, after, payload)
        verified = api._select_saved_search(after, created_id, None)
        if (created_id in before_ids or verified.get("description") != label
                or verified.get("searchKey") != search_key
                or api._saved_search_parameters_for_compare(verified.get("parameters")) != parameters
                or set(verified.get("notifications") or []) != set(expected_notifications)):
            raise RuntimeError("Saved search fields did not match the requested settings")
    except (Exception, SystemExit) as exc:
        raise RuntimeError("FINN accepted saved search creation but verification failed; inspect saved_searches before retrying") from exc
    return {"id": created_id, "name": verified.get("description"),
        "notifications": verified.get("notifications") or [], "verified": True}


@mcp.tool()
def create_listing(
    title: str,
    description: str,
    price: Annotated[int, "Price in NOK."],
    category_id: Annotated[int, "creation_category_id from search_categories/list_categories; use a subcategory or product category, not a top-level category."],
    image_paths: Annotated[list[str], "Optional local image paths to attach to the listing."] | None = None,
    condition: Annotated[str | None, "Optional: Helt ny, Som ny, Pent brukt, Godt brukt, or Må fikses. Do not use the ambiguous Brukt."] = None,
    shoe_size: str | None = None,
    publish_free_standard: Annotated[bool, "Publish using only free Standard after creating and verifying the ad."] = False,
    draft_id: Annotated[str | None, "Resume this existing draft instead of creating a new ad. Inspect it first with get_listing/get_listing_options."] = None,
) -> dict:
    """Create a FINN Torget listing, verify it, and optionally publish free Standard."""
    _validate_condition(condition)
    if draft_id is not None and not draft_id.isdigit():
        raise ValueError("draft_id must be a numeric FINN ad ID")
    if not title.strip() or not description.strip() or price < 0:
        raise ValueError("Title and description are required and price cannot be negative")
    image_paths = [str(Path(path).expanduser()) for path in (image_paths or [])]
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif", "image/avif"}
    for image_path in image_paths:
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image file does not exist: {path}; no FINN draft was created")
        if api.mimetypes.guess_type(path.name)[0] not in allowed_types:
            raise ValueError(f"Unsupported image type for {path.name}; no FINN draft was created")
        if path.stat().st_size == 0:
            raise ValueError(f"Image file is empty: {path}; no FINN draft was created")
    valid_categories = categories(kind="all")
    if not any(row["kind"] in {"sub_category", "product_category"}
               and row["value"].split(".")[-1] == str(category_id) for row in valid_categories):
        raise ValueError(f"Invalid listing category {category_id}. Use creation_category_id from "
                         "search_categories/list_categories (subcategory or product category); no FINN draft was created")
    args = argparse.Namespace(title=title, description=description, description_file=None,
        price=price, category=category_id, condition=condition, shoe_size=shoe_size,
        trade_type="SELL", postalcode=api.DEFAULT_ADDRESS["postalcode"],
        postalname=api.DEFAULT_ADDRESS["postalname"], country="NO",
        latitude=api.DEFAULT_ADDRESS["latitude"], longitude=api.DEFAULT_ADDRESS["longitude"],
        accuracy=api.DEFAULT_ADDRESS["accuracy"], create_response=None)
    captured = io.StringIO()
    listing_id = draft_id
    stage = "loading existing draft" if draft_id else "creating draft"
    try:
        with redirect_stdout(captured):
            if listing_id is None:
                listing_id = api._new_recommerce_draft(args)
            html_path = f"/private/tmp/finn_{listing_id}_mcp_create.html"
            body_path = f"/private/tmp/finn_{listing_id}_mcp_body.json"
            response_path = f"/private/tmp/finn_{listing_id}_mcp_commit.json"
            stage = "loading draft form"
            fetched_id, etag, data = api._fetch_create_data_with_retry(listing_id, html_path, attempts=2)
            if str(fetched_id) != str(listing_id):
                raise RuntimeError("FINN returned a different ad ID")
            if draft_id:
                stage = "verifying existing draft status"
                if _listing_state(html_path).get("state_raw") != "draft":
                    raise ValueError("draft_id does not refer to an editable draft; inspect get_listing before proceeding")
                owner_state = _listing_state(html_path, str(fetched_id))
                if (owner_state.get("state_source") != "owner_by_id"
                        or owner_state.get("publication_status") != "draft"
                        or not owner_state.get("publication_status_verified")):
                    raise ValueError("FINN owner status did not confirm this ID is still a draft; "
                                     "no fields, images, or publication were changed")
            listing_id = fetched_id
            stage = "validating draft fields"
            args.select_html_path = html_path
            api._populate_create_data(data, args)
            for image_path in image_paths:
                stage = "uploading images"
                image = api._upload_item_image(listing_id, image_path)
                data.setdefault("image", []).append(image)
            stage = "committing listing"
            api._write_json(body_path, {"commit": True, "data": data})
            api._post_item_body(listing_id, etag, body_path, response_path)
            stage = "verifying listing by ID"
            with _html_state(listing_id) as verify_path:
                verified_id, _, saved = api._item_put_body_from_create_html(verify_path)
                status_info = _listing_state(verify_path, str(verified_id))
            verified = (api._item_title(saved) == title and saved.get("description") == description
                and str(api._price_amount(saved)) == str(price)
                and str(saved.get("category")) == str(category_id)
                and all(str(saved.get(key)) == str(value) for key, value in getattr(args, "expected_select_fields", {}).items())
                and all(image.get("uri") in {row.get("uri") for row in saved.get("image") or []}
                        for image in data.get("image") or []))
            if not verified:
                raise RuntimeError("Created listing did not match its requested fields during ID readback")
            submission_confirmed = False
            post_submission_content_verified = None
            verification_error = None
            if publish_free_standard:
                stage = "submitting free Standard listing"
                api._publish_free_standard(verified_id)
                submission_confirmed = True
                stage = "verifying submitted listing by ID"
                try:
                    readback = get_listing(verified_id, include_description=True, include_search_metadata=False)
                    post_submission_content_verified = (readback.get("title") == title
                        and readback.get("description") == description
                        and str(readback.get("price")) == str(price))
                    if not post_submission_content_verified:
                        verification_error = "Submitted listing contents did not match the requested fields; inspect by ID before further writes"
                    status_info = {key: readback.get(key) for key in
                        ("state", "state_raw", "state_source", "publication_status", "publication_status_verified")}
                except (Exception, SystemExit) as exc:
                    status_info = {"state": None, "state_raw": None, "state_source": None,
                                   "publication_status": "unknown", "publication_status_verified": False}
                    verification_error = "Submission confirmed, but ID readback failed: " + type(exc).__name__

    except (Exception, SystemExit) as exc:
        if listing_id:
            raise RuntimeError(
                f"FINN create failed during {stage} for ad {listing_id}: {exc}. "
                f"Inspect get_listing(listing_id={listing_id}) and get_listing_options(listing_id={listing_id}). "
                f"If still a draft, resume with create_listing(draft_id='{listing_id}', ...) instead of creating a new ad."
            ) from exc
        raise RuntimeError(
            f"FINN create failed during {stage}; FINN may have created an ad but returned no ID. "
            f"Check owner drafts before retrying. Details: {exc}"
        ) from exc
    return {"id": verified_id, "title": title, **status_info,
        "url": f"https://www.finn.no/{verified_id}", "verified": True, "content_verified": True,
        "submission_confirmed": submission_confirmed, "verified_by": "listing_id", "resumed_draft": draft_id is not None,
        "post_submission_content_verified": post_submission_content_verified,
        "verification_error": verification_error, "retry_creation": False}


@mcp.tool()
def publish_listing(listing_id: Annotated[str, "FINN Torget ad ID to publish."]) -> dict:
    """Publish an existing draft using only free Standard and verify owner state."""
    before = get_listing(listing_id, include_search_metadata=False)
    if str(before.get("id")) != str(listing_id):
        raise RuntimeError("FINN returned a different ad ID")
    if before["publication_status"] in {"published", "under_review"}:
        return {"id": listing_id, **{key: before[key] for key in ("state", "state_raw", "state_source", "publication_status", "publication_status_verified")},
                "already_submitted": True, "url": f"https://www.finn.no/{listing_id}"}
    if before["publication_status"] != "draft" or before.get("state_source") not in {"owner_by_id", "edit_form_draft"}:
        raise ValueError("FINN owner status did not confirm an editable draft; inspect current state before submitting")
    if not before.get("title") or not before.get("description"):
        raise ValueError("Publishing requires a title and description")
    with redirect_stdout(io.StringIO()):
        api._publish_free_standard(listing_id)
    try:
        after = get_listing(listing_id, include_search_metadata=False)
        status_info = {key: after[key] for key in ("state", "state_raw", "state_source", "publication_status", "publication_status_verified")}
    except (Exception, SystemExit):
        status_info = {"state": None, "state_raw": None, "publication_status": "unknown", "publication_status_verified": False}
    return {"id": listing_id, **status_info, "submission_confirmed": True,
            "url": f"https://www.finn.no/{listing_id}", "retry_submission": False}


def _write_item_state(listing_id: str, etag: str, data: dict) -> dict:
    url = f"https://www.finn.no/recommerce/create/api/item/{listing_id}"
    status, _, body = _request(url, method="PUT",
        data=json.dumps({"commit": True, "data": data}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "E-Tag": etag}, authenticated=True, timeout=60)
    if status >= 400:
        raise HTTPError(url, status, "FINN update rejected", None, None)
    response = json.loads(body.decode("utf-8")) if body else {}
    if not isinstance(response, dict):
        raise RuntimeError("FINN update response was not an object")
    if response.get("violations"):
        raise ValueError("FINN rejected the change with validation violations")
    return response


def _commit_item_update(listing_id, etag, data, verify, *, upload_error=None):
    """Attempt one write, then reconcile by ID even if its response was lost."""
    request_error = upload_error
    accepted = False if upload_error is not None else None
    if upload_error is None:
        try:
            _write_item_state(listing_id, etag, data)
            accepted = True
        except (Exception, SystemExit) as exc:
            request_error = exc
            if isinstance(exc, HTTPError) and 400 <= exc.code < 500:
                accepted = False
    saved = None
    verified = False
    verification_error = None
    try:
        with _html_state(listing_id) as verify_path:
            returned_id, _, saved = api._item_put_body_from_create_html(verify_path)
        if str(returned_id) != str(listing_id):
            saved = None
            raise RuntimeError("FINN readback returned a different ad ID")
        verified = upload_error is None and verify(saved)
        if not verified:
            verification_error = "Requested update could not be confirmed from saved contents"
    except (Exception, SystemExit) as exc:
        verification_error = type(exc).__name__
    error = (f"HTTP {request_error.code}" if isinstance(request_error, HTTPError)
             else type(request_error).__name__ if request_error is not None else None)
    return saved, {"id": listing_id, "listing_id": listing_id, "verified": verified,
        "outcome": "verified" if verified else "unconfirmed", "write_attempted": upload_error is None,
        "write_request_accepted": accepted, "request_error": error,
        "verification_error": verification_error,
        "recovered_after_write_error": request_error is not None and verified,
        "retry_update": False}


def _save_item_images(listing_id, returned_id, etag, data, *, upload_error=None):
    if str(returned_id) != str(listing_id):
        raise RuntimeError("FINN returned a different ad ID; no update was sent")
    expected = [image.get("uri") for image in data.get("image") or []]
    saved, result = _commit_item_update(listing_id, etag, data,
        lambda row: [image.get("uri") for image in row.get("image") or []] == expected,
        upload_error=upload_error)
    result["image_order"] = [image.get("uri") for image in saved.get("image") or []] if saved is not None else None
    result["expected_image_order"] = expected if upload_error is None else None
    return result


@mcp.tool()
def move_image(
    listing_id: str,
    image_index: Annotated[int, "Current one-based image position."],
    to_index: Annotated[int, "New one-based position; use 1 to set the cover image."],
) -> dict:
    """Move an existing listing photo and verify the complete image order."""
    with _html_state(listing_id) as html_path:
        returned_id, etag, data = api._item_put_body_from_create_html(html_path)
    images = list(data.get("image") or [])
    if not 1 <= image_index <= len(images) or not 1 <= to_index <= len(images):
        raise ValueError(f"Image positions must be between 1 and {len(images)}")
    moved = images.pop(image_index - 1)
    images.insert(to_index - 1, moved)
    data["image"] = images
    return _save_item_images(listing_id, returned_id, etag, data)


@mcp.tool()
def remove_image(
    listing_id: str,
    image_index: Annotated[int, "Current one-based image position to remove."],
) -> dict:
    """Remove one listing photo while preserving other fields and verify the remaining order."""
    with _html_state(listing_id) as html_path:
        returned_id, etag, data = api._item_put_body_from_create_html(html_path)
    images = list(data.get("image") or [])
    if not 1 <= image_index <= len(images):
        raise ValueError(f"Image position must be between 1 and {len(images)}")
    images.pop(image_index - 1)
    data["image"] = images
    return _save_item_images(listing_id, returned_id, etag, data)


@mcp.tool()
def rotate_image(
    listing_id: str,
    image_index: Annotated[int, "Current one-based image position."],
    source_image: Annotated[str, "Original local image path. The original file is left unchanged."],
    degrees: Annotated[int, "Clockwise rotation: 90, 180, or 270."],
) -> dict:
    """Rotate a local original, replace one FINN photo in place, and verify image order."""
    if degrees not in {90, 180, 270}:
        raise ValueError("Degrees must be 90, 180, or 270")
    original = Path(source_image).expanduser()
    if not original.is_file():
        raise FileNotFoundError(original)
    with _html_state(listing_id) as html_path:
        returned_id, etag, data = api._item_put_body_from_create_html(html_path)
    images = list(data.get("image") or [])
    if not 1 <= image_index <= len(images):
        raise ValueError(f"Image position must be between 1 and {len(images)}")
    with tempfile.NamedTemporaryFile(suffix=original.suffix, prefix="finn-mcp-rotated-", dir="/private/tmp") as rotated:
        result = subprocess.run(["sips", "-r", str(degrees), str(original), "--out", rotated.name],
            capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"Could not rotate source image: {result.stderr.strip()}")
        upload_error = None
        try:
            replacement = api._upload_item_image(listing_id, rotated.name)
        except (Exception, SystemExit) as exc:
            upload_error = exc
    if upload_error is None:
        images[image_index - 1] = replacement
    data["image"] = images
    return _save_item_images(listing_id, returned_id, etag, data, upload_error=upload_error)


@mcp.tool()
def list_conversations(
    limit: Annotated[int, "Maximum conversations to return (1-100)."] = 50,
    cursor: Annotated[str | None, "Cursor returned by the previous page, if any."] = None,
    unread_only: bool = False,
) -> dict:
    """Read FINN message threads in listing groups. This tool never sends messages."""
    limit = max(1, min(limit, 100))
    state = {"offset": 0, "group": 0, "conversation": 0}
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            state = {key: max(0, int(decoded.get(key, 0))) for key in state}
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid conversation cursor") from exc
    base, login_id = _message_context()
    groups = _conversation_groups(base, login_id, state["offset"])
    rows: list[dict] = []
    next_state = None
    for group_index in range(state["group"], len(groups)):
        group = groups[group_index]
        item = group.get("groupBasis", {}).get("itemInfo", {})
        total = int(group.get("totalNumberOfConversations") or 0)
        conv_offset = state["conversation"] if group_index == state["group"] else 0
        while conv_offset < total and len(rows) < limit:
            page = _conversation_rows(group, base, login_id, conv_offset)
            if not page:
                break
            for row in page:
                conv_offset += 1
                summary = _finn_summary(row, group)
                if not unread_only or int(summary.get("unread_count") or 0) > 0:
                    rows.append(summary)
                    if len(rows) >= limit:
                        break
        if len(rows) >= limit:
            if conv_offset < total:
                next_state = {"offset": state["offset"], "group": group_index, "conversation": conv_offset}
            elif group_index + 1 < len(groups):
                next_state = {"offset": state["offset"], "group": group_index + 1, "conversation": 0}
            elif len(groups) == 20:
                next_state = {"offset": state["offset"] + len(groups), "group": 0, "conversation": 0}
            break
        state["conversation"] = 0
    else:
        if len(groups) == 20:
            next_state = {"offset": state["offset"] + len(groups), "group": 0, "conversation": 0}
    encoded = base64.urlsafe_b64encode(json.dumps(next_state, separators=(",", ":")).encode()).decode().rstrip("=") if next_state else None
    return {"conversations": rows, "count": len(rows), "next_cursor": encoded,
        "complete": encoded is None, "unread_only": unread_only}


@mcp.tool()
def get_conversation(
    conversation_id: Annotated[str, "FINN conversation ID."],
    limit: Annotated[int, "Messages to return (1-200, 50 per API page)."] = 50,
    before: Annotated[str | None, "Timestamp in milliseconds from the oldest message, to load an older page."] = None,
) -> dict:
    """Read a FINN conversation page. This tool never sends messages."""
    if not conversation_id.strip():
        raise ValueError("conversation_id cannot be empty")
    if not all(char.isalnum() or char == "-" for char in conversation_id):
        raise ValueError("conversation_id contains unsupported characters")
    limit = max(1, min(limit, 200))
    base, login_id = _message_context()
    messages, seen = [], set()
    cursor = before
    older_cursor = None
    while len(messages) < limit:
        size = min(limit - len(messages), 50)
        page = _finn_messages(base, login_id, conversation_id, cursor, size)
        normalized = [{key: row.get(key) for key in ("id", "body", "sent", "messageType", "outgoing", "read", "partnerRead", "attachments") if key in row}
                      for row in page]
        fresh = []
        for row in normalized:
            key = str(row.get("id") or json.dumps(row, sort_keys=True))
            if key not in seen:
                seen.add(key)
                fresh.append(row)
        messages = fresh + messages
        oldest = normalized[0].get("sent") if normalized else None
        try:
            next_cursor = str(int(datetime.fromisoformat(str(oldest).replace("Z", "+00:00")).timestamp() * 1000)) if oldest else None
        except (ValueError, TypeError, OverflowError):
            next_cursor = None
        older_cursor = next_cursor if len(page) == size else None
        if not fresh or not older_cursor or older_cursor == cursor:
            break
        cursor = older_cursor
    return {"conversation_id": conversation_id, "messages": messages[-limit:], "count": min(len(messages), limit),
            "older_cursor": older_cursor}



@mcp.tool()
def search_messages(
    query: Annotated[str, "Text to find in FINN messages, listing titles, names, and previews."],
    conversation_limit: Annotated[int, "Maximum recent threads to inspect (1-50)."] = 20,
    messages_per_conversation: Annotated[int, "Recent messages to inspect per thread (1-50)."] = 50,
    unread_only: bool = False,
) -> dict:
    """Search recent FINN message text within a bounded number of threads; read-only."""
    needle = query.strip().casefold()
    if not needle:
        raise ValueError("query cannot be empty")
    conversation_limit = max(1, min(conversation_limit, 50))
    messages_per_conversation = max(1, min(messages_per_conversation, 50))
    listed = list_conversations(limit=conversation_limit, unread_only=unread_only)
    base, login_id = _message_context()
    matches = []
    scanned = 0
    message_pages_complete = True
    for summary in listed["conversations"]:
        scanned += 1
        if not summary.get("conversation_id"):
            continue
        messages = _finn_messages(base, login_id, str(summary["conversation_id"]), None, messages_per_conversation)
        message_pages_complete = message_pages_complete and len(messages) < messages_per_conversation
        metadata = " ".join(str(summary.get(key) or "") for key in ("title", "partner", "preview"))
        if needle in metadata.casefold():
            matches.append({"match_type": "conversation", "conversation": summary})
        for message in messages:
            body = str(message.get("body") or "")
            if needle in body.casefold():
                matches.append({"conversation": summary,
                    "message": {key: message.get(key) for key in ("id", "body", "sent", "messageType", "outgoing", "attachments") if key in message}})
    return {"matches": matches, "count": len(matches), "searched_conversations": scanned,
        "complete": listed.get("complete", False) and message_pages_complete, "query": query}


def _existing_listing_conversation(base: str, login_id: str, listing_id: str) -> str | None:
    url = f"{base}/api/conversations/users/{login_id}/conversations/{listing_id}/exists"
    status, _, response = _request(url, authenticated=True, timeout=60)
    if status == 404:
        return None
    data = _parse_json_response(status, response, "FINN conversation lookup")
    ids = data.get("obfuscatedExternalConversationIds") if isinstance(data, dict) else None
    if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
        raise RuntimeError("messaging_conversation_ambiguous: Expected exactly one conversation; nothing sent")
    return ids[0]


def _send_text(base: str, login_id: str, body: str, *, conversation_id: str | None = None,
               listing_id: str | None = None) -> dict:
    message_id = str(uuid.uuid4())
    sent = datetime.now().astimezone().isoformat(timespec="milliseconds")
    message = {"clientMessageId": message_id, "body": body, "messageType": "textMessage",
               "attachments": []}
    url = f"{base}/api/conversations/users/{login_id}/conversations"
    if conversation_id:
        url += f"/{quote(conversation_id, safe='')}/messages"
        payload = {**message, "sent": sent}
    else:
        payload = {"item": {"id": listing_id, "type": "ad"}, "message": message}
    accepted, request_error, verification_error = False, None, None
    response_data = {}
    try:
        status, _, response = _request(url, method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, authenticated=True, timeout=60)
        accepted = 200 <= status < 300
        if not accepted:
            request_error = f"messaging_http_error: HTTP {status}"
        else:
            try:
                response_data = json.loads(response) if response else {}
            except (ValueError, UnicodeError):
                pass
    except (Exception, SystemExit):
        # Transport errors may contain authenticated request details. Never expose them.
        request_error = "messaging_write_error: Sending outcome is unknown"
    verified, server_message_id = False, None
    try:
        if not conversation_id:
            # The creation response's id is a conversation ID, not a message ID.
            if isinstance(response_data, dict) and isinstance(response_data.get("id"), str):
                conversation_id = response_data["id"]
            else:
                conversation_id = _existing_listing_conversation(base, login_id, listing_id)
        if conversation_id:
            rows = _finn_messages(base, login_id, conversation_id, None, 50)
            for row in rows:
                if (row.get("id") == message_id or row.get("clientMessageId") == message_id) and row.get("body") == body and row.get("outgoing") is True:
                    verified, server_message_id = True, row.get("id")
                    break
    except (Exception, SystemExit):
        verification_error = "messaging_verification_error: Could not read back the sent message"
    return {"listing_id": listing_id, "conversation_id": conversation_id,
        "client_message_id": message_id, "message_id": server_message_id,
        "verified": verified, "outcome": "verified" if verified else "unconfirmed",
        "write_attempted": True, "write_request_accepted": accepted,
        "request_error": request_error, "verification_error": verification_error,
        "recovered_after_write_error": verified and not accepted, "retry_send": False}


@mcp.tool()
def send_message(
    conversation_id: Annotated[str, "FINN conversation ID."],
    body: Annotated[str, "Message text explicitly authorized by the user."],
) -> dict:
    """Send in an existing FINN conversation and verify readback. Never retries a write."""
    if not isinstance(body, str) or not body.strip() or not conversation_id.strip():
        raise ValueError("Conversation ID and nonempty message body are required")
    base, login_id = _message_context()
    # Verify access before writing; the account ID always comes from the signed-in session.
    _message_json(f"{base}/api/conversations/users/{login_id}/conversations/{quote(conversation_id, safe='')}")
    return _send_text(base, login_id, body, conversation_id=conversation_id)


@mcp.tool()
def send_listing_message(
    listing_id: Annotated[str, "FINN Torget listing ID to contact."],
    body: Annotated[str, "Message text explicitly authorized by the user."],
) -> dict:
    """Send to a Torget seller by listing ID, reusing a conversation or starting one. Requires explicit send authorization."""
    if not isinstance(listing_id, str) or not re.fullmatch(r"[1-9][0-9]*", listing_id):
        raise ValueError("A numeric FINN listing ID is required")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Message body cannot be empty")
    base, login_id = _message_context()
    contact = _message_json(f"{base}/api/contact/configuration/{listing_id}")
    if not isinstance(contact, dict) or contact.get("type") != "MESSAGING":
        raise RuntimeError("messaging_unavailable: Listing does not support messaging; nothing sent")
    if contact.get("isOwner") is not False:
        raise RuntimeError("messaging_owner_check_failed: Cannot contact own or unverified listing; nothing sent")
    ad_data = _message_json(f"{base}/api/listing?ids=ad{listing_id}")
    ads = ad_data.get("ads", []) if isinstance(ad_data, dict) else []
    if len(ads) != 1 or str(ads[0].get("vertical", "")).lower() != "recommerce":
        raise RuntimeError("messaging_not_torget: Expected a Torget listing; nothing sent")
    conversation_id = _existing_listing_conversation(base, login_id, listing_id)
    return _send_text(base, login_id, body, conversation_id=conversation_id, listing_id=listing_id)



class ListingUpdate(BaseModel):
    """One listing ID and only the fields to change; description is a full replacement."""
    model_config = ConfigDict(extra="forbid", strict=True)
    listing_id: Annotated[str, Field(strict=True, pattern=r"^[1-9][0-9]*$")]
    title: str | None = None
    description: str | None = None
    price: Annotated[int, Field(strict=True, ge=0)] | None = None
    condition: str | None = None
    shoe_size: str | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        changes = self.model_dump(exclude_none=True, exclude={"listing_id"})
        if not changes:
            raise ValueError("Each update must contain at least one field to change")
        _validate_condition(self.condition)
        return self


@mcp.tool()
def update_listings_many(
    updates: Annotated[list[ListingUpdate], Field(min_length=1, max_length=50)],
    verify_public: bool = False,
) -> dict:
    """Update 1-50 owned listings using individual patches. Supply listing_id and only
    fields to change; omitted/null fields stay unchanged. description replaces the
    full text. Image uploads use update_listing instead. Validates all input
    before writes. Uses up to two concurrent per-listing operations, with saved-state
    readback and optional public verification. Not atomic: partial success is possible.
    Returns compact per-ID outcomes in input order. Never retries writes automatically;
    read an unconfirmed listing by ID before considering another write.
    """
    if not isinstance(updates, list) or not 1 <= len(updates) <= 50:
        raise ValueError("updates must contain 1-50 items")
    validated = [ListingUpdate.model_validate(value) for value in updates]
    rows = [value.model_dump(exclude_none=True) for value in validated]
    if len({row["listing_id"] for row in rows}) != len(rows):
        raise ValueError("updates must contain unique listing_id values")

    def apply(row):
        try:
            result = update_listing(**row, verify_public=verify_public)
            keys = ("verified", "outcome", "write_attempted", "write_request_accepted",
                    "request_error", "verification_error", "recovered_after_write_error",
                    "public_verified", "public_verification_error", "retry_update")
            return {"listing_id": row["listing_id"],
                    **{key: result[key] for key in keys if key in result}}
        except (Exception, SystemExit) as exc:
            return {"listing_id": row["listing_id"], "verified": False,
                    "outcome": "unconfirmed", "error": type(exc).__name__,
                    "retry_update": False}

    with ThreadPoolExecutor(max_workers=min(2, len(rows))) as pool:
        results = list(pool.map(apply, rows))
    verified = sum(result.get("verified") is True for result in results)
    return {"mode": "per_listing_requests", "requested": len(rows),
            "verified": verified, "unconfirmed": len(rows) - verified,
            "verify_public": verify_public, "results": results}

if __name__ == "__main__":
    mcp.run()
