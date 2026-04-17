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

# --- Node.js 20 LTS ---
# Sets npm mirror globally inside the image so all npm/npx commands
# use npmmirror by default (fast in China, harmless elsewhere).
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set registry https://registry.npmmirror.com

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

# Default parent dir for timestamped project folders (must match compose volume).
ENV HARNESS_PROJECTS_DIR=/projects
RUN mkdir -p /projects

# git identity for the snapshot commits made by harness.py
RUN git config --global user.email "harness@autoforge" \
    && git config --global user.name "AutoForge"

CMD ["python", "harness.py"]
