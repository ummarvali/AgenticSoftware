"""Card transaction validation microservice (stdlib-only prototype).

This package mirrors the intended Go production service's API contract:
structural validation (Luhn/expiry/CVV), business rules (amount/currency/
network/merchant), and basic fraud heuristics (blacklist/velocity), with
PAN/CVV masking before any logging or persistence.
"""

__version__ = "0.1.0"
