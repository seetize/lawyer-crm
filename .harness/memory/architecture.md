# Architecture memory

- Yandex Maps is the primary identity/card source. Additional map providers
  enrich reviews but must not overwrite a valid primary card.
- Public map data and online-booking data are separate contours. YCLIENTS code
  remains available but must not silently inject historic staff into map reports.
- `app/agents/` contains runtime report workflow roles. `.codex/agents/` contains
  development roles and is not imported by the application.
- Telegram is the current UI; `POST /reports` remains the product-facing API
  boundary for future frontends.
