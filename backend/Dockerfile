# KiCAD is the source of truth for ERC/DRC, so this image ships the real
# kicad-cli instead of mocking it.
#
# Base choice is load-bearing, so don't "simplify" it:
#   - `kicad-cli sch erc` / `pcb drc` only exist from KiCAD 8 onward. Ubuntu
#     24.04 packages KiCAD 7.0.11, whose CLI has export subcommands only.
#   - The kicad PPAs publish amd64 only, so they yield no candidate on arm64
#     and cannot be used to backport 8.x onto an older base.
# Ubuntu 26.04 packages KiCAD 9.0.x natively for both amd64 and arm64.
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/root

RUN apt-get update && apt-get install -y --no-install-recommends \
        kicad \
        kicad-symbols \
        kicad-footprints \
        python3 \
        python3-venv \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fail the build - not a request at runtime - if the tools the validation
# pipeline depends on are absent or do not expose the probed subcommands.
# `--help` exits non-zero when INPUT_FILE is missing, hence the text match.
RUN kicad-cli --version \
    && kicad-cli sch erc --help 2>&1 | grep -qi "usage: erc" \
    && kicad-cli pcb drc --help 2>&1 | grep -qi "usage: drc" \
    && kicad-cli sch export svg --help 2>&1 | grep -qi "usage: svg" \
    && kicad-cli pcb export svg --help 2>&1 | grep -qi "usage: svg"

ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python3 -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/app backend/app
RUN pip install --no-cache-dir -e "./backend[dev]"

COPY backend backend
COPY fixtures fixtures

ENV KICAD_MITOS_WORKSPACE=/workspace \
    KICAD_MITOS_PROJECTS_DIR=/app/fixtures/projects
RUN mkdir -p /workspace

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
