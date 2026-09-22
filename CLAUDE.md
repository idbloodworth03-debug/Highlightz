# Highlightz

Twitch/YouTube stream clipping bot. Python, no frontend build step — the
dashboard is HTML/CSS/JS embedded as string constants in
`src/dashboard/api.py` (`LOGIN_HTML`, `DASHBOARD_HTML`).

## Always end a change with the droplet deploy command

After any change that gets committed and pushed, finish the reply with the
copy-pasteable command to deploy it to the DigitalOcean droplet. Don't wait
to be asked.

Template — substitute the branch the work actually landed on:

```bash
cd /opt/highlightz && \
git fetch origin <branch> && \
git checkout -B <branch> origin/<branch> && \
systemctl restart highlightz
```

Add `venv/bin/pip install -r requirements.txt` before the restart **only** if
`requirements.txt` changed. Note when a browser hard-refresh is needed (any
edit to the embedded HTML/CSS/JS, since it is baked in at import time).

## Droplet layout

Set up by `deploy/setup.sh` on Ubuntu 22.04, running as root:

- App dir: `/opt/highlightz` (a git clone)
- Virtualenv: `/opt/highlightz/venv`
- Service: `highlightz` (systemd, `Restart=always`, env from `/opt/highlightz/.env`)
- Entrypoint: `python -m src.main`
- Redis: `redis-server`, and nginx reverse-proxies to `127.0.0.1:8000`
- Logs: `journalctl -u highlightz -f`

`docker-compose.yml` is empty — systemd is the real deploy path, not Docker.

## Dashboard UI conventions

Dark Twitch-style palette. The accent is purple — reuse it rather than
introducing new hues:

- Accent / links / headings: `#bf94ff`, hover `#a970ff`
- Backgrounds: page `#0e0e10`, panels `#1f1f23`, raised `#26262c`, sidebar `#1a1a1f`
- Borders: `#2d2d35`, inputs `#3a3a44`
- Text: `#efeff1`, muted `#adadb8`

Green/amber/red stay reserved for semantic state (approved, pending,
rejected, score tiers). Keep text at 4.5:1 contrast or better against its
own background.
