# Use official Python slim image as base.
# We install Node.js and Playwright manually to control versions precisely.
# Fallback to Aliyun mirror for regions where Docker Hub is unreachable.
ARG PYTHON_IMAGE=docker.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

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
    npm create vite@latest template-vite-react-ts -- --template react-ts && \
    cd template-vite-react-ts && npm install && \
    cd /templates && \
    npx create-next-app@latest template-nextjs-app --typescript --tailwind --eslint --app --src-dir --no-turbopack && \
    cd template-nextjs-app && npm install && \
    cd /templates && \
    echo "Templates cached" && ls -la

# --- Pre-install common frontend dependencies (speed up Builder npm install) ---
# These are frequently used by Builder for React projects
RUN mkdir -p /npm-cache && cd /npm-cache && \
    npm init -y && \
    npm install zustand lucide-react recharts && \
    npm install -D tailwindcss@latest postcss autoprefixer @types/node && \
    echo "Common dependencies cached"

# --- Pre-install Playwright MCP and browser ---
# This avoids runtime npx download delays and timeouts
# MCP requires chrome-for-testing, not plain chromium.
RUN mkdir -p /playwright-setup && cd /playwright-setup && \
    npm init -y && \
    npm install @playwright/mcp@latest && \
    npx @playwright/mcp install-browser chrome-for-testing

# --- Python packages ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Playwright system dependencies ---
# Use the same npm playwright as MCP to ensure version consistency.
RUN cd /playwright-setup && npx playwright install-deps chromium

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
