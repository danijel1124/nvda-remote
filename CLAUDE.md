# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This repo contains a managed relay system for [NVDA Remote](https://nvdaremote.com), split into two independent sub-projects that communicate over a custom JSON-over-TCP/TLS protocol:

- **`server/`** — a Twisted-based relay server (Docker-deployed) with whitelist-based access control. See `server/CLAUDE.md`.
- **`client/`** — a modified NVDA screen-reader add-on (the "remote" add-on, v3.2) that acts as both the remote-control client and the server's admin GUI. See `client/CLAUDE.md`.

There is no shared build system between the two — treat them as separate projects that happen to share a wire protocol (documented in `client/protocol.md`).

## Architecture: the whitelist/session model

Unlike stock NVDA Remote (where any shared random key lets two clients pair), this fork enforces an access-controlled model:

- A **session name** (historically called "key") identifies a channel on the relay server. Two clients joining the same session name on the same server can exchange messages (one typically as `master`/controller, one as `slave`/controlled).
- The client **enforces the session name to equal the machine's hostname** (`socket.gethostname()`), overwriting any user-entered value — see `client/addon/globalPlugins/remoteClient/configuration.py`. This was a deliberate v3.1 change, and initially made the connect dialog's "control another machine" path dead (no way to type in a different target session name). **v3.2 replaced that path entirely**: connecting is now always "join as this machine's session" (slave-only, no role choice in the connect dialog), and controlling another already-online machine is a separate, later action — Remote menu → "Control another computer" — which asks the server (`list_sessions`, open to any authorized/joined client) for other online, controllable sessions and connects to the chosen one as an independent, concurrent master connection, without touching the slave connection.
- The server only relays traffic for **authorized** session names (`server/data/authorized_keys.json`); unauthorized sessions are quarantined and spoken-notified every 5s until an admin approves them via the admin API/GUI.
- Admin control (approve/block sessions, view online/offline/quarantined state) is done from the client's own GUI (`server_admin_gui.py`, Extras → Remote → Server Administration), authenticated with a per-server admin token — not with a separate tool.
- **Controller/observer model (v3.2):** a channel can have several masters at once. The first to join becomes the sole `controller` (auto-assigned, so the pre-v3.2 single-master flow needs zero client changes and behaves identically); further masters join as read-only observers whose input the server silently drops (with a throttled `control_denied` reply). An observer can take over with a plain **F10** keystroke while nobody controls; the controller releases control with **Alt+F10**. This reuses the existing `key` message (raw F10/Alt+F10 are intercepted server-side, `server.py`'s `handle_control_gesture`) rather than adding a new wire message for the gesture. See `server/CLAUDE.md` and `client/CLAUDE.md` for the message/handler details.
- **Deferred:** per-user/per-account visibility (each user seeing only their own machine, others requesting help) is intentionally not implemented yet — it needs a protocol change that isn't safely backward-compatible with already-deployed pre-v3.2 clients.
- **Self-update push (v3.2):** the server pushes `{version, url}` to every connecting client (`addon_update`, unconditional, before join/auth — same reasoning as `motd`); the client auto-downloads and installs a strictly newer version, never auto-downgrades, and never auto-restarts NVDA to apply it (only announces — see `client/CLAUDE.md`'s `addon_update.py` entry). The server's `data/addon_release.json` (written via `server/set_addon_release.py`, not by hand) is the single source of truth; it must only ever be updated *after* the corresponding GitHub release exists, since its `url` has to point at a real asset.
- **Server self-update check (v1.0.0):** the reverse direction — the server itself checking GitHub for a newer `server-vX.Y.Z` release of its own code (`server/update_check.py`). Check-only: logs and records the result, never downloads/installs/restarts anything (this is a live daemon relaying active sessions, unlike the client add-on which at least self-installs). Runs automatically inside the server process on a configurable interval (`server/data/server_config.json`'s `update_check_interval_hours`, default 24, via `server/set_update_check_interval.py`, no restart needed to change it) and on-demand via `server/check_server_update.py` for an immediate manual check. The same schedule also auto-detects the latest *official* client release and keeps `addon_release.json` up to date automatically — `set_addon_release.py` is only needed for a manual override now. See `server/CLAUDE.md` for the scheduling/threading details.

## Versioning

- Current add-on version: **3.2.4**, set in `client/buildVars.py` (`addon_info["addon_version"]`). Tagged/released as `vX.Y.Z`.
- Current server version: **1.2.0**, set as `SERVER_VERSION` in `server/server.py`. Tagged/released as `server-vX.Y.Z` (distinct tag namespace, so client and server releases don't collide in this shared repo). Versioned independently from the client — see `server/CLAUDE.md`'s Versioning section.
- Config migrations for upgrades live in `migrate_config()` in `client/addon/globalPlugins/remoteClient/configuration.py`.
- **Nightly releases (rule, not per-commit numbered releases)**: don't cut a new `vX.Y.Z`/`server-vX.Y.Z` release for every small/incidental change (docs, metadata, minor fixes) — that's release-flooding, which the user has explicitly said they don't want. Instead run `./make_nightly.sh` (repo root), which builds the client add-on with a `nightly-YYYYMMDDHHMMSS` version (via `buildVars.py`'s `NIGHTLY_VERSION` env override, never touching the committed `addon_version`) and force-updates a single rolling `nightly` tag/GitHub pre-release — mirrors the pattern from the user's `danijel1124/Disco-A11y` repo (one rolling tag deleted and recreated each time, not versioned). Safe by construction: `server/update_check.py`'s `check_for_client_update` only ever auto-detects/pushes the latest *official* (non-prerelease, non-draft) release, so a nightly can never get silently auto-pushed to production clients via `addon_release.json` — use `set_addon_release.py` by hand if a nightly build genuinely needs to go out for testing. Cut a real numbered release only when deliberately promoting accumulated changes, not reflexively.

## Working conventions

- Don't delete existing GUI functionality (e.g. in `server_admin_gui.py`) when extending it — add to it.
- The admin token must never be hardcoded; it's read from `remote.ini` / the client's multi-server token store, or from `server/data/admin.token` on the server side.
