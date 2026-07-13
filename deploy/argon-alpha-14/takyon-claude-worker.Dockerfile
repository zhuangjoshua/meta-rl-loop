FROM nikolaik/python-nodejs:python3.11-nodejs20

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        fonts-noto-color-emoji \
    && npm install --global agent-browser@0.26.0 \
    && agent-browser --version \
    && chromium --version \
    && rm -rf /var/lib/apt/lists/* /root/.npm

ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
ENV AGENT_BROWSER_ARGS=--no-sandbox,--disable-dev-shm-usage
