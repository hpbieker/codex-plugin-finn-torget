"""Local matching and compact results for costly owner-listing scans."""
import re
from listing_fields import project_listing


def matches_fields(expression, fields):
    """Terms can match different fields; quoted phrases stay within one field."""
    if expression.kind == "term":
        return any(expression.text in field.casefold() for field in fields)
    if expression.kind == "not":
        return not matches_fields(expression.children[0], fields)
    children = (matches_fields(child, fields) for child in expression.children)
    return all(children) if expression.kind == "and" else any(children)


def positive_terms(expression, excluded=False):
    if expression.kind == "term":
        return [] if excluded else [expression.text]
    if expression.kind == "not":
        return positive_terms(expression.children[0], not excluded)
    return [term for child in expression.children for term in positive_terms(child, excluded)]


def snippets(expression, fields):
    excerpts = []
    terms = positive_terms(expression)
    for field, text in fields.items():
        for term in terms:
            if term not in text.casefold():
                continue
            # Regex offsets refer to the original text, including Unicode letters.
            match = re.search(re.escape(term), text, re.I)
            start = max(0, match.start() - 70) if match else 0
            end = min(len(text), (match.end() + 110) if match else 180)
            item = {"field": field, "text": ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")}
            if item not in excerpts:
                excerpts.append(item)
            if len(excerpts) == 3:
                return excerpts
    if not excerpts:
        text = fields["description"] or fields["title"]
        excerpts.append({"field": "description" if fields["description"] else "title",
                         "text": text[:180] + ("…" if len(text) > 180 else "")})
    return excerpts


def search_rows(expression, query, rows, get_many, limit, total, enumeration_complete, include_fields=()):
    matches, errors = [], []
    scanned = 0
    for offset in range(0, len(rows), 50):
        batch = rows[offset:offset + 50]
        summaries = {str(row["id"]): row for row in batch}
        result = get_many([row["id"] for row in batch], include_fields=["*"])
        for item in result["results"]:
            if "error" in item:
                errors.append({"listing_id": item["listing_id"], "error": item["error"]})
                continue
            listing = item["listing"]
            if not isinstance(listing.get("description"), str):
                errors.append({"listing_id": item["listing_id"], "error": {
                    "type": "MissingDescription", "message": "Full description was not returned."}})
                continue
            scanned += 1
            fields = {"title": listing.get("title") or listing.get("name") or "",
                      "description": listing["description"]}
            if matches_fields(expression, tuple(fields.values())):
                summary = summaries[str(item["listing_id"])]
                record = dict(listing)
                record["url"] = summary.get("url") or listing.get("url") or listing.get("publicUrl")
                if include_fields == ("*",) or "snippets" in include_fields:
                    record["snippets"] = snippets(expression, fields)
                match = project_listing(record, include_fields, id_field="listing_id")
                matches.append(match)
    return {"query": query, "include_fields": list(include_fields), "mode": "per_listing_requests", "total_available": total,
            "selected": len(rows), "scanned": scanned, "failed": len(errors),
            "complete": enumeration_complete and not errors,
            "matched": len(matches), "returned": min(limit, len(matches)),
            "has_more_matches": len(matches) > limit, "matches": matches[:limit], "errors": errors}
