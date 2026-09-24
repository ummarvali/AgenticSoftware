# The agent system itself (not the demo service — that image is demo/Dockerfile).
#   docker build -t agentic-sdlc .
#   docker run --rm -v "$PWD/runs:/app/runs" agentic-sdlc --file examples/greenfield.txt
#   docker run --rm -e ANTHROPIC_API_KEY -v "$PWD/runs:/app/runs" agentic-sdlc --provider claude --file examples/greenfield.txt
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY examples/*.txt ./examples/
COPY demo/ ./demo/
RUN pip install --no-cache-dir -e ".[anthropic,llm]"

RUN useradd --create-home --uid 10001 agent && mkdir -p /app/runs && chown -R agent /app
USER agent

ENTRYPOINT ["agentic-sdlc"]
CMD ["--file", "examples/greenfield.txt"]
