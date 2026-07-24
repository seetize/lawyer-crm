import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedRequest:
    query: str
    city: str
    criteria: list[str]


def parse_request(text: str, default_city: str) -> ParsedRequest:
    value = " ".join(text.strip().split())
    parts = [part.strip() for part in value.split("|")]
    if len(parts) > 1:
        query = parts[0]
        city = parts[1] or default_city
        criteria = _criteria(parts[2]) if len(parts) > 2 else []
        return ParsedRequest(query=query, city=city, criteria=criteria)

    explicit_city = re.search(
        r"\s+(?:в\s+городе|город|г\.)\s+([а-яёa-z][а-яёa-z -]+)$",
        value,
        flags=re.IGNORECASE,
    )
    if explicit_city:
        return ParsedRequest(
            query=value[: explicit_city.start()].strip(" ,-"),
            city=explicit_city.group(1).strip(),
            criteria=[],
        )

    without_default = re.sub(
        rf"(?:[\s,]+){re.escape(default_city)}\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" ,-")
    return ParsedRequest(
        query=without_default or value,
        city=default_city,
        criteria=[],
    )


def _criteria(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
