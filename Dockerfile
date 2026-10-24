# =============================================================================
# RUNECLAW v2 Dockerfile — Multi-stage build (bot + API bridge)
# =============================================================================

# ── Stage 1: builder (compiles wheels) ──────────────────────────────────
# Pin to digest for reproducible, tamper-evident builds.
# python:3.11-slim multi-arch index, resolved 2026-10-23 from registry-1.docker.io.
# Debian 13 (trixie), Python 3.11.17, image created 2026-10-21. Includes linux/arm64.
# The previous bookworm digest no longer resolves (registry 404 on that date).
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377 AS builder

WORKDIR /app

# gcc and the OpenSSL headers are needed to BUILD some wheels and are needed by
# nothing at runtime. They used to be installed in a stage the production image
# then declared `FROM`, so the "multi-stage" build discarded nothing and
# shipped a C toolchain to production. Install into a prefix here and copy only
# the result across.
#
# ca-certificates comes from Debian security. trixie main still ships
# 20250419, which trusts the withdrawn emSign roots; trixie-security ships
# 20250419+deb13u1 (DSA-6572-1). The comparison fails the build if apt did
# not select that update.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates gcc libssl-dev \
    && dpkg --compare-versions "$(dpkg-query -W -f '$${Version}' ca-certificates)" ge "20250419+deb13u1" \
    && rm -rf /var/lib/apt/lists/*

# The base image's pip vendors its own CA bundle, so the system store above
# does not protect the next download. This is the official pip 26.2.2 wheel;
# pip checks the hash itself. Hash verified against the wheel on 2026-10-23.
RUN python -m pip install --no-cache-dir \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"

COPY bot/requirements.txt ./requirements.txt
# The same wheel again, into the prefix the production stage copies, so the
# image that runs is not left on the base image's pip.
RUN python -m pip install --no-cache-dir --prefix=/install \
    "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70" \
    -r requirements.txt \
    fastapi==0.141.1 "uvicorn[standard]==0.52.3"

# ── Stage 2: production image ───────────────────────────────────────────
FROM python:3.11-slim@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377 AS production

# curl is what HEALTHCHECK below runs. ca-certificates is the same security
# update as the builder: trixie main's 20250419 still trusts the withdrawn roots.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && dpkg --compare-versions "$(dpkg-query -W -f '$${Version}' ca-certificates)" ge "20250419+deb13u1" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

ARG BUILD_SHA=dev
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.revision="${BUILD_SHA}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"
LABEL maintainer="Humanoid Traders"
# Links the ghcr.io package to this repository, so the package page shows the
# source and inherits its access settings.
LABEL org.opencontainers.image.source="https://github.com/metafrogmeme-droid/001"
LABEL org.opencontainers.image.licenses="BUSL-1.1"

WORKDIR /app

COPY . .

# Stamp the build so /api/version and /status report the real commit even in a
# .git-less image (app/lib/version.js reads this at the repo root).
RUN printf '{"sha":"%s","committed_at":"%s"}\n' "$BUILD_SHA" "$BUILD_DATE" > /app/build-info.json

RUN mkdir -p /app/logs /app/data

# Non-root user (security hardening)
RUN useradd -m -u 1001 runeclaw && chown -R runeclaw:runeclaw /app
USER runeclaw

HEALTHCHECK --interval=15s --timeout=5s --retries=4 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Default: API bridge — override with CMD in compose for the bot service.
#
# ONE WORKER. This said --workers 2, while docker-compose.yml explains at
# length why it must not: api_bridge's lifespan constructs its own
# RuneClawEngine per worker, and every worker mounts the same ./data as the bot
# container. At two workers that was THREE engines sharing one paper book,
# which PortfolioTracker loads once and then overwrites whole — so a second
# writer did not merge, it erased. Compose overrode this, so the documented
# path was safe and `docker run runeclaw:latest` reproduced the bug exactly.
# A default should be the safe value; the override should be the exception.
# Raising this needs the state to stop being per-process first.
CMD ["uvicorn", "api_bridge:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
