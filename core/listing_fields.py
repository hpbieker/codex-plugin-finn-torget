"""Project supported listing fields without fetching or exposing raw API data."""


def validate_include_fields(value, allowed=("description",)):
    if value is None:
        return ()
    if not isinstance(value, list) or not value or any(not isinstance(field, str) for field in value):
        raise ValueError("include_fields must be a nonempty array of field names; omit for defaults")
    if len(set(value)) != len(value):
        raise ValueError("include_fields must contain unique fields")
    if "*" in value:
        if value != ["*"]:
            raise ValueError("* must be used alone")
    elif any(field not in (*allowed, "id", "title") for field in value):
        raise ValueError("Supported include_fields: " + ", ".join(sorted(set(allowed) | {"id", "title"})) + "; or * alone")
    return tuple(value)


def canonical_listing(record):
    result = dict(record)
    result["id"] = result.pop("productId", result.get("id"))
    result["title"] = result.pop("name", result.get("title", ""))
    if "publicUrl" in result:
        result["url"] = result.pop("publicUrl")
    return result


def project_listing(record, fields, id_field="id"):
    record = canonical_listing(record)
    result = {id_field: record.pop("id"), "title": record.pop("title")}
    if fields == ("*",):
        result.update(record)
    else:
        result.update({field: record.get(field) for field in fields if field not in result and field not in {"id", "title"}})
    return result
