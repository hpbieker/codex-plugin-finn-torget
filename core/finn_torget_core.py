#!/usr/bin/env python3
"""Small helpers for FINN Torget responses."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import html
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from http.cookiejar import Cookie, CookieJar
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener, urlopen


BASE_SEARCH_URL = "https://www.finn.no/recommerce/forsale/search"
BASE_SEARCH_API_URL = "https://www.finn.no/recommerce/forsale/search/api/search"
MY_ITEMS_SUMMARY_URL = "https://www.finn.no/my-items/api/summary"
CREATE_RECOMMERCE_URL = "https://www.finn.no/create-item/start/podium-resource/ad-insertion-frontpage-private-podlet/api/create"
SAVED_SEARCH_SETTINGS_URL = (
    "https://www.finn.no/notification-settings/podium-resource/"
    "notificationSavedSearchSettings/api/saved-search-settings"
)
SAVED_SEARCH_CREATE_URL = (
    "https://www.finn.no/saved-search-settings-proxy/podium-resource/"
    "savedSearchSettings/api/saved-search/"
)
DEFAULT_SEARCH_KEY = "SEARCH_ID_BAP_COMMON"
DEFAULT_CATEGORY_QUERY = ""
DEFAULT_ADDRESS = {
    "postalcode": "0776",
    "postalname": "Oslo",
    "country": "NO",
    "latitude": "59.95310294117101",
    "longitude": "10.69060425516443",
    "accuracy": 5,
}
TRADE_TYPE = {
    "sell": "1",
    "sale": "1",
    "til-salgs": "1",
    "give-away": "2",
    "gis-bort": "2",
    "wanted": "3",
    "onskes-kjopt": "3",
    "ønskes-kjøpt": "3",
}
FOR_RENT = {
    "buy": "0",
    "kjop": "0",
    "kjøp": "0",
    "rent": "1",
    "leie": "1",
}
SELLER_SEGMENT = {
    "private": "1",
    "privat": "1",
    "dealer": "3",
    "butikk": "3",
    "forhandler": "3",
}
CONDITION = {
    "new": "1",
    "ny": "1",
    "unopened": "1",
    "as-new": "2",
    "som-ny": "2",
    "good": "3",
    "pent-brukt": "3",
    "used": "4",
    "godt-brukt": "4",
    "fixer": "5",
    "ma-fikses": "5",
    "må-fikses": "5",
}


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _write_json(path: str, data: object) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<.*?>", "", raw))).strip()


def _script_json(text: str, element_id: str) -> object:
    match = re.search(
        rf'<script[^>]+id=["\']{re.escape(element_id)}["\'][^>]*>(.*?)</script>',
        text,
        flags=re.S | re.I,
    )
    if not match:
        raise SystemExit(f"Could not find script id={element_id!r}")
    try:
        return json.loads(html.unescape(match.group(1)).strip())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not decode JSON from script id={element_id!r}: {exc}") from exc


def _attr(text: str, name: str) -> str:
    match = re.search(rf'\b{re.escape(name)}=["\']([^"\']+)["\']', text, flags=re.I)
    if not match:
        raise SystemExit(f"Could not find attribute {name!r}")
    return html.unescape(match.group(1))


def _decode_props(text: str) -> dict:
    encoded = _attr(text, "data-props")
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        props = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not decode data-props: {exc}") from exc
    if not isinstance(props, dict):
        raise SystemExit("Decoded data-props was not an object")
    return props


def _choose_products_context(text: str) -> tuple[str, str]:
    """Read choose-products identity and CSRF data from old and new FINN pages."""
    try:
        ad_id = _attr(text, "data-ad-id")
        token = _attr(text, "data-hmac-token")
        return ad_id, token
    except SystemExit:
        pass

    try:
        config = json.loads(base64.b64decode(_attr(text, "data-config")).decode("utf-8"))
        props = config.get("props") if isinstance(config, dict) else None
        ad_id = str(props.get("adId")) if isinstance(props, dict) and props.get("adId") else ""
        token = str(props.get("csrfToken")) if isinstance(props, dict) and props.get("csrfToken") else ""
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Could not decode choose-products data-config: {exc}") from exc
    if not ad_id or not token:
        raise SystemExit("Could not find adId/csrfToken in choose-products page")
    return ad_id, token


def _mapped_value(raw: str, mapping: dict[str, str], label: str) -> str:
    value = raw.strip()
    key = value.lower()
    if key in mapping:
        return mapping[key]
    if value.isdigit():
        return value
    allowed = ", ".join(sorted(mapping))
    raise SystemExit(f"Unknown {label}: {raw}. Use a numeric FINN value or one of: {allowed}")


def _append(params: list[tuple[str, str]], key: str, value: object | None) -> None:
    if value is not None and str(value) != "":
        params.append((key, str(value)))




def _search_params(args: argparse.Namespace) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [("q", args.query)]
    _append(params, "sort", args.sort)
    _append(params, "price_from", args.price_from)
    _append(params, "price_to", args.price_to)
    _append(params, "published", args.published)
    _append(params, "page", args.page)

    for location in args.location or []:
        _append(params, "location", location)
    for category in args.category or []:
        _append(params, "category", category)
    for sub_category in args.sub_category or []:
        _append(params, "sub_category", sub_category)
    for product_category in args.product_category or []:
        _append(params, "product_category", product_category)
    for condition in args.condition or []:
        _append(params, "condition", _mapped_value(condition, CONDITION, "condition"))
    if args.trade_type:
        _append(params, "trade_type", _mapped_value(args.trade_type, TRADE_TYPE, "trade type"))
    if args.for_rent:
        _append(params, "for_rent", _mapped_value(args.for_rent, FOR_RENT, "for-rent"))
    if args.seller:
        _append(params, "dealer_segment", _mapped_value(args.seller, SELLER_SEGMENT, "seller segment"))

    for raw in args.param or []:
        if "=" not in raw:
            raise SystemExit(f"Expected --param key=value, got: {raw}")
        key, value = raw.split("=", 1)
        if key:
            params.append((key, value))
    return params




def _search_api_url(args: argparse.Namespace) -> str:
    params = _search_params(args)
    return f"{BASE_SEARCH_API_URL}/{args.search_key}?{urlencode(params)}"




def _my_items_summary_url(args: argparse.Namespace, offset: int) -> str:
    params: list[tuple[str, str]] = [
        ("limit", str(args.limit)),
        ("offset", str(offset)),
    ]
    _append(params, "search", args.search)
    _append(params, "facet", args.facet)
    _append(params, "type", args.type)
    return f"{MY_ITEMS_SUMMARY_URL}?{urlencode(params)}"


def _fetch_my_items_summary(args: argparse.Namespace, out: str, offset: int) -> dict:
    _run_safari_request(["-L", _my_items_summary_url(args, offset), "-o", out])
    return json.loads(_read(out))




def _json_ld_items(text: str) -> list[dict]:
    match = re.search(
        r'<script[^>]+id=["\']seoStructuredData["\'][^>]*>(.*?)</script>',
        text,
        flags=re.S | re.I,
    )
    if not match:
        return []
    try:
        payload = json.loads(html.unescape(match.group(1)).strip())
    except json.JSONDecodeError:
        return []
    graph = payload.get("@graph") if isinstance(payload, dict) else None
    items: list[dict] = []
    if isinstance(graph, list):
        for obj in graph:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") in {"Product", "Offer", "ListItem"} or obj.get("url"):
                items.append(obj)
    elif isinstance(payload, dict):
        items.append(payload)
    return items


def _article_items(text: str) -> list[dict]:
    items = []
    for article in re.findall(r"<article\b.*?</article>", text, flags=re.S | re.I):
        href = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', article, flags=re.I)
        heading = re.search(r"<h[23][^>]*>(.*?)</h[23]>", article, flags=re.S | re.I)
        price = re.search(r'class=["\'][^"\']*font-bold[^"\']*["\'][^>]*>(.*?)</div>', article, flags=re.S | re.I)
        meta = re.search(
            r'class=["\'][^"\']*s-text-subtle[^"\']*["\'][^>]*>\s*<span[^>]*>(.*?)</span>\s*<span[^>]*>(.*?)</span>',
            article,
            flags=re.S | re.I,
        )
        if not href or not heading:
            continue
        url = _clean(href.group(1))
        if url.startswith("/"):
            url = "https://www.finn.no" + url
        title = _clean(heading.group(1))
        price_text = _clean(price.group(1)) if price else ""
        item = {"title": title, "url": url, "price": price_text}
        id_match = re.search(r"/item/(\d+)", url)
        if id_match:
            item["id"] = id_match.group(1)
        if meta:
            item["location"] = _clean(meta.group(1))
            item["published"] = _clean(meta.group(2))
        if re.search(r"\b(fiks ferdig|helt hjem|posten|postnord)\b", article, flags=re.I):
            item["shipping"] = "shipping-visible"
        items.append(item)
    return items


def _result_count(text: str) -> str | None:
    patterns = [
        r"Du finner\s+([\d\s]+)\s+annonse",
        r"([\d\s]+)\s+treff",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return re.sub(r"\s+", "", html.unescape(match.group(1)))
    return None






def _filter_rows_from_items(items: list[dict], kinds: set[str], parent: str = "") -> list[dict]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        value = item.get("value")
        label = item.get("display_name") or item.get("label") or item.get("title") or ""
        path = f"{parent} > {label}" if parent and label else str(label or parent)
        if name in kinds and value is not None and label:
            rows.append(
                {
                    "kind": name,
                    "value": str(value),
                    "label": str(label),
                    "hits": item.get("hits"),
                    "path": path,
                }
            )
        rows.extend(_filter_rows_from_items(item.get("filter_items") or [], kinds, path))
    return rows


def _filter_rows_from_data(data: object, kinds: set[str]) -> list[dict]:
    rows = []
    if isinstance(data, dict):
        for filter_def in data.get("filters") or []:
            if not isinstance(filter_def, dict):
                continue
            rows.extend(_filter_rows_from_items(filter_def.get("filter_items") or [], kinds))
    return rows


def _dedupe_filter_rows(rows: list[dict]) -> list[dict]:
    deduped: dict[tuple[str, str], dict] = {}
    for row in rows:
        deduped[(row["kind"], row["value"])] = row
    return sorted(deduped.values(), key=lambda row: (row["kind"], row["path"].lower(), row["value"]))


def _print_filter_rows(rows: list[dict], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    fields = ["kind", "value", "label", "hits", "path"]
    if fmt == "tsv":
        print("\t".join(fields))
    for row in rows:
        print("\t".join(str(row.get(field) if row.get(field) is not None else "").replace("\t", " ") for field in fields))




def _item_put_body_from_create_html(html_path: str) -> tuple[str, str, dict]:
    text = _read(html_path)
    config = None
    try:
        encoded = _attr(text, "data-config")
    except SystemExit:
        encoded = None
    if encoded:
        try:
            raw = html.unescape(encoded)
            if raw.lstrip().startswith("{"):
                config = json.loads(raw)
            else:
                config = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            config = None

    # Current FINN edit pages embed the item, ETag, and item id in the
    # base64-encoded data-config on the app root. Keep the script fallback for
    # older pages that still expose data-item and data-ad-id attributes.
    item = config.get("item") if isinstance(config, dict) else None
    if not isinstance(item, dict):
        item = _script_json(text, "data-item")
    if not isinstance(item, dict):
        raise SystemExit("FINN item state was not an object")
    if isinstance(config, dict) and config.get("itemId") is not None:
        ad_id = str(config["itemId"])
    else:
        ad_id = _attr(text, "data-ad-id")
    etag = item.get("etag")
    if not etag:
        raise SystemExit("FINN item state did not contain etag")
    data = item.get("data")
    if not isinstance(data, dict):
        raise SystemExit("FINN item state did not contain object data")
    return ad_id, str(etag), data


def _item_title(data: dict) -> str:
    heading = data.get("heading")
    if isinstance(heading, dict):
        text = heading.get("text")
        if text:
            return str(text)
    return str(data.get("title") or data.get("heading") or "")


def _set_item_title(data: dict, title: str) -> None:
    heading = data.get("heading")
    if isinstance(heading, dict):
        heading["text"] = title
    elif "title" in data:
        data["title"] = title
    else:
        data["heading"] = title


def _price_amount(data: dict) -> object:
    price = data.get("price")
    if isinstance(price, dict):
        return price.get("amount") or price.get("value") or price.get("price_amount") or price.get("price")
    for key in ("price", "priceAmount", "rentalPrice"):
        value = data.get(key)
        if value not in (None, "") and not isinstance(value, dict):
            return value
    return None


def _set_price_amount(data: dict, amount: int) -> None:
    price = data.get("price")
    if isinstance(price, dict):
        for key in ("price_amount", "amount", "value", "price"):
            if key in price:
                price[key] = amount
                return
        price["amount"] = amount
        return
    for key in ("price", "priceAmount", "rentalPrice"):
        if key in data:
            data[key] = amount
            return
    data["price"] = amount


def _fetch_edit_html(ad_id: str, *, suffix: str = "create") -> str:
    out = f"/private/tmp/finn_{ad_id}_{suffix}.html"
    _run_safari_request(["-L", f"https://www.finn.no/recommerce/create/{ad_id}", "-o", out])
    return out



def _select_field_options(html_path: str, field_name: str, category: object) -> list[dict]:
    raw = html.unescape(_attr(_read(html_path), "data-config"))
    config = json.loads(raw) if raw.lstrip().startswith("{") else json.loads(
        base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    for field in config.get("model") or []:
        if field.get("name") != field_name:
            continue
        categories = (field.get("dependencies") or {}).get("category")
        if categories and str(category) not in {str(value) for value in categories}:
            raise SystemExit(f"{field_name} is unavailable for category {category}")
        return field.get("options") or []
    raise SystemExit(f"FINN did not expose options for {field_name}")


def _resolve_select_field(html_path: str, field_name: str, requested: object, category: object) -> int:
    options = _select_field_options(html_path, field_name, category)
    value = str(requested).strip().casefold().replace(".", ",")
    # Match human labels first: fractional shoe sizes use unrelated FINN ids.
    matches = [row for row in options if str(row.get("label", "")).casefold() == value]
    if not matches and field_name == "condition":
        matches = [row for row in options if str(row.get("label", "")).split(" - ")[0].casefold() == value]
    if not matches:
        matches = [row for row in options if str(row.get("value")) == value]
    if len(matches) != 1:
        labels = ", ".join(str(row.get("label")) for row in options)
        raise SystemExit(f"Invalid {field_name}: {requested}. Allowed: {labels}")
    return int(matches[0]["value"])


def _apply_select_fields(data: dict, args: argparse.Namespace, html_path: str) -> dict:
    changes = {}
    for field_name, argument in (("condition", "condition"), ("shoe_size", "shoe_size")):
        requested = getattr(args, argument, None)
        if requested is None:
            continue
        value = _resolve_select_field(html_path, field_name, requested, data.get("category"))
        changes[f"old_{field_name}"] = data.get(field_name)
        data[field_name] = value
        changes[field_name] = value
    args.expected_select_fields = {key: data[key] for key in ("condition", "shoe_size") if key in changes}
    return changes












def _description_from_args(args: argparse.Namespace) -> str | None:
    if getattr(args, "description", None) is not None and getattr(args, "description_file", None):
        raise SystemExit("Use only one of --description and --description-file")
    if getattr(args, "description", None) is not None:
        return args.description
    if getattr(args, "description_file", None):
        return _read(args.description_file).rstrip("\n")
    return None


def _upload_item_image(ad_id: str, image_path: str) -> dict:
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise SystemExit(f"Image file does not exist: {path}")
    mime_type, _ = mimetypes.guess_type(path.name)
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "image/heif", "image/avif"}
    if mime_type not in allowed_types:
        raise SystemExit(f"Unsupported image type for {path.name}: {mime_type or 'unknown'}")
    size = path.stat().st_size
    url = (
        f"https://www.finn.no/recommerce/create/api/image/{quote(str(ad_id), safe='')}"
        f"?type={quote(mime_type, safe='')}&size={size}"
    )
    response_path = f"/private/tmp/finn_{ad_id}_image_{uuid.uuid4().hex[:8]}.json"
    _run_safari_request(
        [
            "-sS",
            "--fail-with-body",
            "-L",
            "-X",
            "POST",
            url,
            "-F",
            f"image=@{path};type={mime_type}",
            "-o",
            response_path,
        ]
    )
    try:
        response = json.loads(_read(response_path))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FINN image upload returned invalid JSON; response saved at {response_path}") from exc
    image = response.get("data") if isinstance(response, dict) else None
    if not isinstance(image, dict) or not image.get("path"):
        raise SystemExit(f"FINN image upload did not return an image path; response saved at {response_path}")
    return {
        "uri": image["path"],
        "width": int(image["width"]),
        "height": int(image["height"]),
    }


def _update_fields_from_args(data: dict, args: argparse.Namespace) -> dict[str, object]:
    changes: dict[str, object] = {}
    title = getattr(args, "title", None)
    description = _description_from_args(args)
    price = getattr(args, "price", None)
    image_paths = getattr(args, "image", None) or []
    if title is None and description is None and price is None and not image_paths and getattr(args, "condition", None) is None and getattr(args, "shoe_size", None) is None:
        raise SystemExit("Specify at least one of --title, --description, --description-file, --price, --image, --condition, or --shoe-size")

    if title is not None:
        changes["old_title"] = _item_title(data)
        _set_item_title(data, title)
        changes["title"] = title
    if description is not None:
        changes["old_description_chars"] = len(str(data.get("description") or ""))
        data["description"] = description
        changes["description_chars"] = len(description)
    if price is not None:
        changes["old_price_amount"] = _price_amount(data)
        _set_price_amount(data, price)
        changes["price_amount"] = price
    changes.update(_apply_select_fields(data, args, args.select_html_path))
    uploaded_uris = []
    images = list(data.get("image") or [])
    for image_path in image_paths:
        image = _upload_item_image(args.ad_id, image_path)
        images.append(image)
        uploaded_uris.append(image["uri"])
    if image_paths:
        data["image"] = images
        args.uploaded_image_uris = uploaded_uris
        changes["image_count"] = len(images)
    return changes


def _print_update_changes(changes: dict[str, object]) -> None:
    for key in ("old_title", "title", "old_price_amount", "price_amount", "old_description_chars", "description_chars", "old_condition", "condition", "old_shoe_size", "shoe_size"):
        if key in changes:
            print(f"{key}\t{changes[key]}")


def _verify_edit_state(args: argparse.Namespace) -> bool:
    verify_html = f"/private/tmp/finn_{args.ad_id}_create_verify.html"
    _run_safari_request(["-L", f"https://www.finn.no/recommerce/create/{args.ad_id}", "-o", verify_html])
    _, _, data = _item_put_body_from_create_html(verify_html)
    ok = True
    if getattr(args, "title", None) is not None:
        title_ok = _item_title(data) == args.title
        print(f"verified_title\t{title_ok}")
        ok = ok and title_ok
    description = _description_from_args(args)
    if description is not None:
        description_ok = str(data.get("description") or "") == description
        print(f"verified_description\t{description_ok}")
        ok = ok and description_ok
    if getattr(args, "price", None) is not None:
        price_ok = str(_price_amount(data)) == str(args.price)
        print(f"verified_price\t{price_ok}")
        ok = ok and price_ok
    for key, expected in getattr(args, "expected_select_fields", {}).items():
        field_ok = str(data.get(key)) == str(expected)
        print(f"verified_{key}\t{field_ok}")
        ok = ok and field_ok
    expected_image_uris = getattr(args, "uploaded_image_uris", [])
    if expected_image_uris:
        image_uris = {image.get("uri") for image in data.get("image") or [] if isinstance(image, dict)}
        images_ok = all(uri in image_uris for uri in expected_image_uris)
        print(f"verified_images\t{images_ok}")
        ok = ok and images_ok
    return ok


def _safari_cookie_jar(url: str) -> CookieJar:
    """Import domain-scoped Safari cookies through the local socket, in memory."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise SystemExit("Could not determine FINN request host")
    socket_path = os.environ.get("CURL_SAFARI_SOCKET_PATH", "/private/tmp/curl-safari.sock")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(socket_path)
            client.sendall(json.dumps({"op": "fetch", "domain": host, "url": url}).encode() + b"\n")
            with client.makefile("rb") as reader:
                response = reader.readline(1_048_577)
    except OSError as exc:
        raise SystemExit(f"Could not read Safari cookies through the local socket: {exc}") from exc
    try:
        payload = json.loads(response) if len(response) <= 1_048_576 else None
    except (ValueError, UnicodeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("cookie_jar"), str):
        raise SystemExit("Safari cookie service did not return a valid cookie jar")

    jar = CookieJar()
    now = int(time.time())
    for line in payload["cookie_jar"].splitlines():
        http_only = line.startswith("#HttpOnly_")
        fields = line.removeprefix("#HttpOnly_").split("\t")
        if len(fields) != 7 or fields[0].startswith("#"):
            continue
        domain, include_subdomains, path, secure, expires_raw, name, value = fields
        domain = domain.lower()
        bare_domain = domain.lstrip(".")
        if not (host == bare_domain or host.endswith("." + bare_domain)) or not value:
            continue
        try:
            expires = int(expires_raw) if expires_raw and expires_raw != "0" else None
        except ValueError:
            expires = None
        if expires is not None and expires <= now:
            continue
        domain_specified = include_subdomains.upper() == "TRUE"
        rest = {"HttpOnly": None} if http_only else {}
        jar.set_cookie(Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=domain_specified,
            domain_initial_dot=domain.startswith("."), path=path or "/",
            path_specified=True, secure=secure.upper() == "TRUE", expires=expires,
            discard=expires is None, comment=None, comment_url=None, rest=rest,
            rfc2109=False,
        ))
    if not list(jar):
        raise SystemExit("Safari has no FINN cookies; log in to FINN in Safari and retry")
    return jar


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if redirected and urlparse(request.full_url).hostname != urlparse(new_url).hostname:
            for name in ("Authorization", "E-Tag", "x-csrf-token"):
                redirected.remove_header(name)
        return redirected


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _safari_request(parts: list[str], *, capture: bool = False) -> str:
    """Handle the helper's curl-style request options with urllib."""
    method = None
    headers: dict[str, str] = {}
    data: bytes | None = None
    form_files: list[tuple[str, str, str, bytes]] = []
    output_path = None
    write_out = None
    follow_redirects = False
    fail_with_body = False
    url = None
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in {"-L", "--location"}:
            follow_redirects = True
        elif part in {"-sS", "--silent", "--show-error"}:
            pass
        elif part == "--fail-with-body":
            fail_with_body = True
        elif part == "-X":
            i += 1; method = parts[i]
        elif part == "-H":
            i += 1
            key, sep, value = parts[i].partition(":")
            if not sep:
                raise SystemExit("Invalid HTTP header passed to FINN helper")
            headers[key.strip()] = value.strip()
        elif part == "-o":
            i += 1; output_path = parts[i]
        elif part == "-w":
            i += 1; write_out = parts[i]
        elif part == "--data-binary":
            i += 1
            raw = parts[i]
            data = Path(raw[1:]).read_bytes() if raw.startswith("@") else raw.encode("utf-8")
        elif part == "-F":
            i += 1
            field, sep, spec = parts[i].partition("=")
            if not sep or not spec.startswith("@"):
                raise SystemExit("Unsupported multipart field in FINN helper")
            file_info = spec[1:].split(";type=", 1)
            file_path = Path(file_info[0])
            mime_type = file_info[1] if len(file_info) == 2 else (mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
            form_files.append((field, file_path.name, mime_type, file_path.read_bytes()))
        elif part.startswith("-"):
            raise SystemExit(f"Unsupported FINN request option: {part}")
        else:
            url = part
        i += 1

    if not url:
        raise SystemExit("FINN request is missing its URL")
    if form_files:
        boundary = "----CodexFinn" + uuid.uuid4().hex
        chunks: list[bytes] = []
        for field, filename, mime_type, content in form_files:
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode(),
                content, b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        data = b"".join(chunks)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = Request(url, data=data, headers=headers, method=method or ("POST" if data is not None else "GET"))
    cookie_jar = _safari_cookie_jar(url)
    handlers = [HTTPCookieProcessor(cookie_jar), _SafeRedirect() if follow_redirects else _NoRedirect()]
    opener = build_opener(*handlers)
    try:
        with opener.open(request, timeout=60) as response:
            status, final_url, body = response.status, response.geturl(), response.read()
    except HTTPError as exc:
        status, final_url, body = exc.code, exc.geturl(), exc.read()
    if output_path:
        Path(output_path).write_bytes(body)
    if fail_with_body and status >= 400:
        location = f"; response saved at {output_path}" if output_path else ""
        raise SystemExit(f"FINN request failed with HTTP {status}{location}")
    if write_out is not None:
        return write_out.replace("%{http_code}", str(status)).replace("%{url_effective}", final_url)
    if capture and not output_path:
        return body.decode("utf-8", errors="replace")
    return ""


def _run_safari_request(parts: list[str]) -> None:
    _safari_request(parts)


def _run_safari_request_capture(parts: list[str]) -> str:
    return _safari_request(parts, capture=True)


def _fetch_saved_searches(out: str) -> list[dict]:
    _run_safari_request(
        ["--fail-with-body", "-sS", SAVED_SEARCH_SETTINGS_URL, "-H", "Content-Type: application/json", "-o", out]
    )
    data = json.loads(_read(out))
    if not isinstance(data, list):
        raise SystemExit("FINN saved-search settings response was not a list")
    return [row for row in data if isinstance(row, dict)]


def _select_saved_search(rows: list[dict], search_id: int | None, name: str | None) -> dict:
    if search_id is not None:
        matches = [row for row in rows if str(row.get("id")) == str(search_id)]
    else:
        expected = str(name or "").strip().casefold()
        matches = [row for row in rows if str(row.get("description") or "").strip().casefold() == expected]
    if not matches:
        selector = f"id {search_id}" if search_id is not None else f"name {name!r}"
        raise SystemExit(f"Could not find a saved search with {selector}")
    if len(matches) > 1:
        ids = ", ".join(str(row.get("id")) for row in matches)
        raise SystemExit(f"Saved-search name is ambiguous; use --id. Matching ids: {ids}")
    return matches[0]


def _saved_search_summary(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "name": row.get("description"),
        "notifications": row.get("notifications") or [],
        "category": row.get("verticalDescription") or row.get("searchKeyDescription"),
    }






def _saved_search_create_input(args: argparse.Namespace) -> dict:
    url = _search_api_url(args)
    request = Request(url, headers={"User-Agent": "codex-finn-torget/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=args.timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    metadata = data.get("metadata") or {}
    if not metadata.get("is_savable_search"):
        raise SystemExit("FINN reports that this search cannot be saved")
    descriptions = metadata.get("descriptions") or {}
    name = (args.name or descriptions.get("saved_search") or args.query).strip()
    if not name:
        raise SystemExit("Saved-search name cannot be empty")
    if len(name) > 50:
        raise SystemExit(f"Saved-search name is {len(name)} characters; FINN allows at most 50")
    search_key = metadata.get("search_key")
    parameters = metadata.get("params")
    if not search_key or not isinstance(parameters, dict):
        raise SystemExit("FINN search metadata did not contain search_key and params")
    channel_values = {"web": "NC", "push": "PUSH", "email": "EMAIL"}
    channels = args.channel or ["web", "push", "email"]
    return {
        "description": name,
        "notifications": [channel_values[channel] for channel in channels],
        "type": "alert",
        "searchKey": search_key,
        "parameters": parameters,
    }


def _created_saved_search_id(response_text: str, before_ids: set[int], rows: list[dict], payload: dict) -> int:
    try:
        response = json.loads(response_text)
    except json.JSONDecodeError:
        response = None
    candidates: list[object] = []
    if isinstance(response, dict):
        candidates.extend([response.get("id"), response.get("savedSearchId")])
    else:
        candidates.append(response)
    for candidate in candidates:
        if str(candidate or "").isdigit():
            return int(candidate)
    matches = [
        row
        for row in rows
        if str(row.get("id") or "").isdigit()
        and int(row["id"]) not in before_ids
        and row.get("description") == payload["description"]
        and row.get("searchKey") == payload["searchKey"]
        and _saved_search_parameters_for_compare(row.get("parameters")) == payload["parameters"]
    ]
    if len(matches) != 1:
        raise SystemExit("Could not identify exactly one newly created saved search during verification")
    return int(matches[0]["id"])


def _saved_search_parameters_for_compare(parameters: object) -> object:
    if not isinstance(parameters, dict):
        return parameters
    return {key: value for key, value in parameters.items() if key != "stored-id"}




def _extract_ad_id(text: str) -> str | None:
    patterns = [
        r"/recommerce/create/(\d{6,})",
        r"/bap/fiks-ferdig/(\d{6,})",
        r"adId=(\d{6,})",
        r"finnkode=(\d{6,})",
        r"\b(\d{8,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _fetch_create_data_with_retry(ad_id: str, html_path: str, attempts: int = 6) -> tuple[str, str, dict]:
    last_error = ""
    for attempt in range(attempts):
        _run_safari_request(["-L", f"https://www.finn.no/recommerce/create/{ad_id}", "-o", html_path])
        try:
            return _item_put_body_from_create_html(html_path)
        except SystemExit as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                time.sleep(1)
    raise SystemExit(f"Could not fetch create state for {ad_id}: {last_error}")


def _description_for_create(args: argparse.Namespace) -> str:
    description = _description_from_args(args)
    if not description:
        raise SystemExit("create requires --description or --description-file")
    return description


def _populate_create_data(data: dict, args: argparse.Namespace) -> None:
    data["title"] = args.title
    data["description"] = _description_for_create(args)
    data["trade_type"] = args.trade_type
    data["price"] = {"price_amount": args.price}
    data["category"] = args.category
    _apply_select_fields(data, args, args.select_html_path)
    data["address"] = {
        "postalcode": args.postalcode,
        "postalname": args.postalname,
        "country": args.country,
        "latitude": str(args.latitude),
        "longitude": str(args.longitude),
        "accuracy": args.accuracy,
    }
    data.setdefault("image", [])
    data["edited_by_system_tmp"] = "recommerce-item-insertion"


def _new_recommerce_draft(args: argparse.Namespace) -> str:
    out = args.create_response or f"/private/tmp/finn_create_recommerce_{uuid.uuid4().hex[:8]}.raw"
    stdout = _run_safari_request_capture(
        [
            "-sS",
            "-L",
            "-X",
            "POST",
            CREATE_RECOMMERCE_URL,
            "-H",
            "Content-Type: application/x-www-form-urlencoded",
            "--data-binary",
            "adType=recommerce",
            "-o",
            out,
            "-w",
            "%{http_code}\t%{url_effective}\n",
        ]
    )
    response_text = _read(out) if Path(out).exists() else ""
    ad_id = _extract_ad_id(stdout + "\n" + response_text)
    if not ad_id:
        raise SystemExit(f"Could not extract new FINN ad id from create response saved at {out}")
    print(f"create_response\t{out}")
    return ad_id


def _post_item_body(ad_id: str, etag: str, body_path: str, response_path: str) -> dict:
    _run_safari_request(
        [
            "-L",
            "-X",
            "PUT",
            f"https://www.finn.no/recommerce/create/api/item/{ad_id}",
            "-H",
            "Content-Type: application/json",
            "-H",
            f"E-Tag: {etag}",
            "--data-binary",
            f"@{body_path}",
            "-o",
            response_path,
        ]
    )
    try:
        response = json.loads(_read(response_path))
    except json.JSONDecodeError:
        response = {}
    violations = response.get("violations") if isinstance(response, dict) else None
    if violations:
        raise SystemExit(f"FINN validation violations for {ad_id}: {violations}")
    return response if isinstance(response, dict) else {}


def _free_standard_urn_from_data(data: dict) -> str:
    candidates = [
        row
        for row in _package_rows(data)
        if row.get("rawActual") == 0 and "standard" in str(row.get("heading") or "").lower()
    ]
    if not candidates:
        raise SystemExit("Could not find a free Standard package in sales context")
    return quote(str(candidates[0]["urn"]), safe="")



def _finish_free_order(ad_id: str, order_id: str) -> None:
    payment_path = f"/private/tmp/finn_{ad_id}_payment.html"
    receipt_path = f"/private/tmp/finn_{ad_id}_receipt.html"
    _run_safari_request(["-sS", "--fail-with-body", "-L",
        f"https://www.finn.no/order-and-payment/payment-options?adId={ad_id}&orderId={order_id}&funnel=recommerce",
        "-o", payment_path])
    expected = f"https://www.finn.no/order-and-payment/ad-receipt?adId={ad_id}&orderId={order_id}"
    if expected not in _read(payment_path):
        raise SystemExit("FINN did not redirect this order directly to its free receipt; stopping before payment")
    _run_safari_request(["-sS", "--fail-with-body", "-L", expected, "-o", receipt_path])
    print(f"free_receipt\t{receipt_path}")

def _publish_free_standard(ad_id: str) -> None:
    fiks_path = f"/private/tmp/finn_{ad_id}_fiks.html"
    delivery_body_path = f"/private/tmp/finn_{ad_id}_delivery_body.json"
    delivery_response_path = f"/private/tmp/finn_{ad_id}_delivery_response.json"
    choose_path = f"/private/tmp/finn_{ad_id}_choose_products.html"
    sales_context_path = f"/private/tmp/finn_{ad_id}_sales_context.json"
    order_response_path = f"/private/tmp/finn_{ad_id}_ordernow_response.json"

    # Publishing preserves the existing delivery setup.
    _run_safari_request(["-L", f"https://www.finn.no/recommerce/choose-products?adId={ad_id}&funnel=recommerce", "-o", choose_path])
    choose_html = _read(choose_path)
    page_ad_id, token = _choose_products_context(choose_html)
    if page_ad_id != str(ad_id):
        raise SystemExit(f"choose-products page returned adId {page_ad_id}, expected {ad_id}")
    # Current FINN embeds the package choices in the page's data-config.
    # Older pages exposed them through the former baseSales context endpoint.
    try:
        config = json.loads(base64.b64decode(_attr(choose_html, "data-config")).decode("utf-8"))
        sales_context = {"choices": config["props"]["choices"]}
        _write_json(sales_context_path, sales_context)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        _run_safari_request(
            [
                "-L",
                f"https://www.finn.no/recommerce/choose-products/podium-resource/baseSales/api/context/{ad_id}",
                "-H",
                f"x-csrf-token: {token}",
                "-o",
                sales_context_path,
            ]
        )
        sales_context = json.loads(_read(sales_context_path))
    for row in _package_rows(sales_context):
        print(
            "\t".join(
                [
                    "package",
                    str(row.get("urn") or ""),
                    str(row.get("heading") or ""),
                    f"rawActual={row.get('rawActual')}",
                    str(row.get("formattedActual") or ""),
                ]
            )
        )
    urn = _free_standard_urn_from_data(sales_context)
    _run_safari_request(
        [
            "-L",
            "-X",
            "POST",
            f"https://www.finn.no/recommerce/choose-products/api/ordernow?adId={ad_id}&productSpecificationUrns={urn}",
            "-H",
            "Content-Type: application/json",
            "-H",
            f"x-csrf-token: {token}",
            "--data-binary",
            "{}",
            "-o",
            order_response_path,
        ]
    )
    order = json.loads(_read(order_response_path))
    order_id = order.get("orderId")
    if not order_id or not str(order_id).isdigit():
        raise SystemExit("FINN did not return an order id")
    print(f"ordered_free_standard\t{order_response_path}")
    _finish_free_order(ad_id, str(order_id))


def _summary_match(path: str, ad_id: str) -> dict | None:
    data = json.loads(_read(path))
    for ad in data.get("summaries") or []:
        if str(ad.get("id")) == str(ad_id):
            return ad
    return None


def _verify_created_ad_state(ad_id: str) -> str:
    """Read one ad's state from the signed-in owner summary by exact ID."""
    out = f"/private/tmp/finn_{ad_id}_verify.json"
    args = argparse.Namespace(limit=10, offset=0, search=str(ad_id), facet="ALL", type=None)
    _fetch_my_items_summary(args, out, 0)
    match = _summary_match(out, ad_id)
    if not match:
        print("verified_state\t-")
        return ""
    state_data = match.get("state") or {}
    state_type = str(state_data.get("type") or "").upper()
    state = state_data.get("label") or {
        "ACTIVE": "Aktiv",
        "PENDING": "Til kontroll",
        "DRAFT": "Påbegynt",
    }.get(state_type, state_type)
    print(f"verified_state\t{state}")
    print(f"verify_json\t{out}")
    return state
















def _package_price(package: dict) -> dict:
    price = package.get("price") or {}
    if isinstance(price, dict):
        return price
    return {}


def _package_rows(data: dict) -> list[dict]:
    rows = []
    packages = data.get("packages") or data.get("choices") or []
    for package in packages:
        if not isinstance(package, dict):
            continue
        price = _package_price(package)
        rows.append(
            {
                "urn": package.get("urn") or package.get("productSpecificationUrn") or package.get("specificationUrn") or "",
                "heading": package.get("heading") or package.get("title") or package.get("name") or "",
                "rawActual": price.get("rawActual"),
                "formattedActual": price.get("formattedActual") or price.get("actual") or "",
            }
        )
    return rows






def _messages_from_data(data: object) -> list[dict]:
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = data.get("messageResponseList") or data.get("messages") or data.get("content") or []
    else:
        raw = []
    return [message for message in raw if isinstance(message, dict)]


def _shorten(text: object, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"




def _local_iso_millis() -> str:
    return datetime.now().isoformat(timespec="milliseconds")








