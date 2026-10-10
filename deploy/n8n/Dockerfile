# ==========================================================================
# n8n as its own service, deployable to Render.
#
# A thin wrapper around the official image. The official entrypoint stays in
# charge (it performs the database migration on boot); only the port wiring and
# a health probe are added on top.
#
# PERSISTENCE IS THE ONE THING YOU MUST GET RIGHT.
# Render's free web services have an EPHEMERAL filesystem: /home/node/.n8n is
# wiped on every redeploy and every restart. Pick exactly one:
#
#   (a) DB_TYPE=postgresdb  -> workflows and credentials live in PostgreSQL.
#       Strongly recommended; works on the free tier.
#   (b) Render Persistent Disk mounted at /home/node/.n8n.
#
# With neither, you lose every workflow and every stored credential on each
# deploy. Losing workflows is recoverable (re-import the JSON). Losing
# credentials means re-entering every Buffer token in the UI.
# ==========================================================================
FROM n8nio/n8n:latest

# n8n reads both of these natively from its own configuration; Render assigns
# the port through the environment rather than fixing it.
ENV N8N_PORT=5678 \
    N8N_LISTEN_ADDRESS=0.0.0.0 \
    NODE_ENV=production \
    N8N_RUNNERS_ENABLED=true

# n8n encrypts its credential store with this key. If it is not set, n8n
# generates a new one on every boot and every previously saved credential
# becomes undecryptable - the symptom is "credential not found" after a
# restart. Set it in the service environment and back it up somewhere safe.
#
# Deliberately empty here: a key baked into an image is a leaked key.
ENV N8N_ENCRYPTION_KEY=""

EXPOSE 5678

# /healthz returns 200 once the instance is serving and 503 while the database
# migration is still running, which is exactly what a deploy gate should see.
# Uses Node's built-in fetch so nothing is downloaded at probe time.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:'+(process.env.N8N_PORT||5678)+'/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

# NO CMD override here on purpose.
#
# The official image already ends with `tini -- /docker-entrypoint.sh`, and that
# entrypoint runs `n8n` itself when it receives no arguments. Adding
# `CMD ["n8n"]` passes "n8n" as $1, which the entrypoint tries to execute as a
# script, and the container exits with code 1 - which Render reports as
# `nonZeroExit: 1` with no log output.
#
# Override the command in the dashboard or with the `dockerCommand` field only
# if you genuinely need arguments.