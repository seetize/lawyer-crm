import base64
import json
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.providers.yclients import YClientsEnricher


def test_yclients_services_and_comments_are_normalized() -> None:
    services = YClientsEnricher.parse_services(
        {
            "services": [
                {
                    "id": 10,
                    "title": "Стрижка",
                    "price_min": 1000,
                    "price_max": 1500,
                    "seance_length": 3600,
                }
            ]
        },
        "https://n123.yclients.com/",
    )
    comments = YClientsEnricher.parse_comments(
        {
            "comments": [
                {
                    "id": 5,
                    "text": "Всё понравилось",
                    "rating": 5,
                    "user_name": "Анна",
                    "date": 123456,
                }
            ]
        }
    )

    assert services[0].provider_service_id == "10"
    assert services[0].price == "1000–1500 ₽"
    assert services[0].duration == "60 мин"
    assert comments[0].provider == "yclients"
    assert comments[0].provider_review_id == "5"


def test_public_widget_context_can_be_verified_by_yclients() -> None:
    before = int(time.time())
    headers = YClientsEnricher._public_headers(
        "public-token",
        "1.2.3",
        "https://n123.yclients.com",
    )
    nonce_encoded, ciphertext_encoded = headers[
        "X-App-Client-Context"
    ].split(":", 1)
    key = headers["X-App-Client-Context-Analytics-Udid"][:32].encode()
    decrypted = AESGCM(key).decrypt(
        base64.b64decode(nonce_encoded),
        base64.b64decode(ciphertext_encoded),
        None,
    )
    payload = json.loads(decrypted)

    assert payload["requestUdid"]
    assert before <= payload["timestamp"] <= int(time.time())


def test_nested_public_booking_dates_are_found() -> None:
    dates = YClientsEnricher._date_strings(
        {
            "working_dates": ["2026-07-29", "2026-07-30"],
            "metadata": {"generated": "not-a-date"},
        }
    )

    assert dates == ["2026-07-29", "2026-07-30"]


def test_yclients_booking_form_exposes_network_branches() -> None:
    branches = YClientsEnricher.parse_branches(
        {
            "companies": [
                {"id": 1, "name": "Сеть", "address": "ул. Кирова, 1"},
                {"id": 2, "name": "Сеть", "address": "ул. Победы, 2"},
            ]
        },
        "https://n123.yclients.com/",
    )

    assert [branch.provider_id for branch in branches] == ["1", "2"]
