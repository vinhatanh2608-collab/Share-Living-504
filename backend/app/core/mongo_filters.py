def sanitize_filter_values(value):
    """Check only user-supplied values. Add trusted Mongo operators separately."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or not key or key.startswith("$") or "." in key or "\0" in key:
                raise ValueError("Invalid document filter key")
            sanitize_filter_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            sanitize_filter_values(child)
    elif type(value) not in (str, int, float, bool, type(None)):
        raise ValueError("Invalid document filter value")
    return value


def lookup_id(value: str) -> str:
    if type(value) is not str:
        raise ValueError("Expected a string document identifier")
    return str(sanitize_filter_values(value))
