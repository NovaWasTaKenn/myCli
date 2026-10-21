# Build: docker build -t azd-shell .
# Run:   docker run --rm -it -v "$PWD":/workspace -v azd-config:/root/.azd azd-shell
# Login inside the container with: azd auth login --use-device-code
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    AZD_SKIP_UPDATE_CHECK=true \
    AZURE_DEV_COLLECT_TELEMETRY=no

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tar \
    && curl -fsSL https://aka.ms/install-azd.sh | bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["bash"]
