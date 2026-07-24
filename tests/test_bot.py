from app.bot import split_telegram_text


def test_long_telegram_report_is_split_safely() -> None:
    text = "\n".join(f"Строка {index}: " + "x" * 80 for index in range(100))

    chunks = split_telegram_text(text, limit=500)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert "\n".join(chunks) == text
