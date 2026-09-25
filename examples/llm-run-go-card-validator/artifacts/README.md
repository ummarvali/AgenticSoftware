# Card Transaction Validation Microservice (prototype)

A standard-library-only Python prototype of a card transaction validation
service. It runs transactions through a layered pipeline:

1. **Structural validation** — Luhn check, PAN length/network match, expiry
   format/expiration, CVV length-by-network.
2. **Business rules** — amount min/max limits, supported currency codes,
   supported card networks, merchant ID format.
3. **Fraud/risk heuristics** — hashed-PAN blacklist lookup, sliding-window
   velocity check, simple combined risk score.

Raw PAN/CVV are masked/hashed immediately on receipt (`cardvalidator/masking.py`)
and never persisted or logged in plaintext. Only masked/hashed audit metadata
is stored in SQLite for the `/v1/audit/{request_id}` endpoint.

This prototype mirrors the API contract intended for a production Go
microservice (see architecture notes in the code comments).

## Run

```bash
python -m cardvalidator.server --host 127.0.0.1 --port 8080
```

Configuration via environment variables (all optional, defaults shown):

```
CV_HOST=127.0.0.1
CV_PORT=8080
CV_DB_PATH=cardvalidator.db
CV_MIN_AMOUNT=0.50
CV_MAX_AMOUNT=5000.00
CV_SUPPORTED_CURRENCIES=USD,EUR,GBP
CV_SUPPORTED_NETWORKS=VISA,MASTERCARD,AMEX,DISCOVER
CV_VELOCITY_WINDOW_SECONDS=60
CV_VELOCITY_MAX_ATTEMPTS=5
CV_API_KEYS=dev-key-123
CV_PAN_SALT=change-me-salt
```

## API examples (curl)

All protected endpoints require header `X-API-Key: dev-key-123` (or your
configured key).

### POST /v1/validate

```bash
curl -s -X POST http://127.0.0.1:8080/v1/validate \
  -H "X-API-Key: dev-key-123" -H "Content-Type: application/json" \
  -d '{
        "pan": "4111111111111111",
        "expiry_month": 12,
        "expiry_year": 2099,
        "cvv": "123",
        "amount": 100.0,
        "currency": "USD",
        "merchant_id": "MERCH123"
      }'
```

### POST /v1/blacklist

```bash
curl -s -X POST http://127.0.0.1:8080/v1/blacklist \
  -H "X-API-Key: dev-key-123" -H "Content-Type: application/json" \
  -d '{"hashed_pan": "'"$(python3 -c 'print("a"*64)')"'", "reason": "reported fraud"}'
```

### GET /v1/audit/{request_id}

```bash
curl -s http://127.0.0.1:8080/v1/audit/<request_id-from-validate-response> \
  -H "X-API-Key: dev-key-123"
```

### GET /healthz

```bash
curl -s http://127.0.0.1:8080/healthz
```

### GET /readyz

```bash
curl -s http://127.0.0.1:8080/readyz
```

### GET /metrics

```bash
curl -s http://127.0.0.1:8080/metrics
```

## Tests

Unit tests cover validators/masking/fraud logic directly; integration tests
spin up the real HTTP server on an ephemeral localhost port and drive every
endpoint end-to-end, including error cases (invalid input -> 400, missing
API key -> 401, unknown route -> 404, unknown audit id -> 404).

```bash
python -m unittest discover -s tests -v
```
