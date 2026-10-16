# TutorAssist in a container — for hosting on a server later.
#
#   docker build -t tutorassist .
#   docker run -d -p 8787:8787 \
#     -e TUTOR_ACCESS_TOKEN=$(openssl rand -hex 16) \
#     -e TUTOR_ALLOWED_HOSTS=tutor.example.com \
#     -e ANTHROPIC_API_KEY=sk-ant-...            # or sign in once: docker exec -it <id> claude
#     -v tutor-data:/data tutorassist
#
# Then open https://tutor.example.com/?key=<TUTOR_ACCESS_TOKEN> once (it sets a cookie).
# Put it behind HTTPS (Caddy, Cloudflare Tunnel, Fly.io, …) — the agent can run commands.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git poppler-utils \
      texlive-latex-base texlive-latex-recommended texlive-latex-extra \
      texlive-fonts-recommended texlive-pictures texlive-science \
    && rm -rf /var/lib/apt/lists/*

# Claude Code, installed and run unmodified, as Anthropic publishes it.
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

ENV TUTORASSIST_HOME=/data/.app \
    TUTOR_HOST=0.0.0.0 \
    TUTOR_PORT=8787
VOLUME ["/data"]
EXPOSE 8787
CMD ["sh", "-c", "tutor init /data/workspace >/dev/null && tutor serve --workspace /data/workspace --no-browser"]
