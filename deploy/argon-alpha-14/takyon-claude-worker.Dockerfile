FROM nikolaik/python-nodejs:python3.11-nodejs20

# This image owns isolated Claude Agent SDK execution, not browser availability. The legacy tag is
# retained for rollout compatibility, but optional visual rendering capability is detected at
# runtime. A missing Chromium binary must never prevent the coding worker from starting.
RUN node --version >/dev/null \
    && npm --version >/dev/null
