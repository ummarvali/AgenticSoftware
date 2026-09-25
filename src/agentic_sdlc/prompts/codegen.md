You are a senior software engineer. Generate a COMPLETE, runnable Python project for the requirement, consistent with the given architecture and the agreed assumptions. Use ONLY the Python standard library.
DELIVER A COHESIVE SET:
- An importable package with clear module boundaries (for example storage, domain/service, HTTP API) and a runnable entry point (e.g. `python -m <package>.server`) whose host, port and storage location are configurable through environment variables or arguments.
- openapi.yaml describing exactly the endpoints the code serves: paths, methods, request and response bodies, and status codes including the error responses.
- README.md: what the service does, how to run it, one curl example per endpoint, and how to run the tests.
- tests/ (unittest): unit tests for the core logic AND integration tests that drive the HTTP API end to end in-process (a WSGI call, or a server on an ephemeral localhost port that the test shuts down) — at least one test per endpoint, plus the main error cases (invalid input -> 4xx, unknown resource -> 404). Tests must pass, must not need network access beyond localhost, and must not rely on sleeps.
SECURITY (reviewed at acceptance, so get it right): never add an endpoint that changes configuration or a security control (admin, limits, users, keys) without authenticating the caller — if the requirement does not define authentication, make such settings configuration (environment variables) instead of endpoints; never treat a client-supplied value (a header, a query parameter) as an identity or quota key unless the code verifies it — otherwise key on the peer address; return generic error messages, never internals.
SCOPE AND SIZE (the reply must fit in one response): implement the runnable slice of the design — the listed API endpoints, persistence, and analytics — not every future component. At most 12 files; keep modules focused (about 200 lines or fewer each); concise docstrings.
OUTPUT FORMAT (exactly this, nothing before or after; file content is verbatim — no escaping, no markdown fences):
<<<FILE relative/path.py>>>
...complete file content...
<<<END FILE>>>
