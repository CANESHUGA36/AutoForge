# Use the existing local image as base to avoid network issues.
# The local autoforge:latest already has python:3.12-slim + all system deps installed.
# We only need to copy the updated source code on top.
FROM autoforge:latest

WORKDIR /app

# --- Project source (updated files only) ---
COPY *.py ./
COPY skills/ ./skills/
COPY harness/ ./harness/
COPY tools/ ./tools/
COPY prompts/ ./prompts/
COPY config.py ./
COPY context.py ./
COPY dashboard.py ./
COPY eval_cache.py ./
COPY workspace_state.py ./

CMD ["python", "run.py"]
