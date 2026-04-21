# Use official Python slim image as base.
# We install Node.js and Playwright manually to control versions precisely.
FROM python:3.12-slim

WORKDIR /app

# --- System dependencies ---
# git: Builder uses git for version snapshots
# curl: NodeSource setup script
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
    && rm -rf /var/lib/apt/lists/*

# --- Node.js + npm (Debian packages) ---
# Avoid NodeSource curl+TLS in CI/Docker (can fail with SSL EOF); Debian's nodejs
# package does not always pull in npm, so install both explicitly.
# Sets npm mirror globally so npm/npx use npmmirror by default (fast in China).
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set registry https://registry.npmmirror.com

# --- Pre-install common dev servers (used by single-file HTML projects) ---
RUN npm install -g serve http-server

# --- Pre-cache project templates (avoid npm create timeouts in container) ---
RUN mkdir -p /templates && cd /templates && \
    npm create vite@latest template-vite-react-ts -- --template react-ts 2>&1 || true && \
    cd template-vite-react-ts && npm install 2>&1 || true && \
    cd /templates && \
    npx create-next-app@latest template-nextjs-app --typescript --tailwind --eslint --app --src-dir --no-turbopack 2>&1 || true && \
    cd template-nextjs-app && npm install 2>&1 || true && \
    cd /templates && \
    echo "Templates cached" && ls -la

# --- Python packages ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Playwright browsers ---
# --with-deps installs all required OS-level libraries for Chromium.
# Only install chromium to keep the image lean.
RUN playwright install --with-deps chromium

# --- Project source ---
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

# Default parent dir for timestamped project folders (must match compose volume).
ENV HARNESS_PROJECTS_DIR=/projects
RUN mkdir -p /projects

# git identity for the snapshot commits made by harness.py
RUN git config --global user.email "harness@autoforge" \
    && git config --global user.name "AutoForge"

CMD ["python", "run.py"]
