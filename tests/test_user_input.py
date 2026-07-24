from app.user_input import parse_request


def test_plain_request_ignores_case_and_extra_spaces() -> None:
    parsed = parse_request(
        "  gSnV   LaB   АСТРАХАНЬ  ",
        "Астрахань",
    )

    assert parsed.query == "gSnV LaB"
    assert parsed.city == "Астрахань"
    assert parsed.criteria == []


def test_hyphenated_name_needs_no_special_format() -> None:
    parsed = parse_request("GSNV-LAB", "Астрахань")

    assert parsed.query == "GSNV-LAB"
    assert parsed.city == "Астрахань"


def test_city_can_be_written_as_normal_phrase() -> None:
    parsed = parse_request(
        "GSNV lab в городе Казань",
        "Астрахань",
    )

    assert parsed.query == "GSNV lab"
    assert parsed.city == "Казань"


def test_advanced_pipe_format_remains_supported() -> None:
    parsed = parse_request(
        "Париж | Москва | рейтинг, отзывы",
        "Астрахань",
    )

    assert parsed.query == "Париж"
    assert parsed.city == "Москва"
    assert parsed.criteria == ["рейтинг", "отзывы"]
