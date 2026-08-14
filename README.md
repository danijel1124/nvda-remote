# NVDA Remote (Fork)

A fork of [NVDA Remote](https://nvdaremote.com) that replaces its "anyone with a shared key can pair" model with an access-controlled one: a managed relay server with a whitelist and an admin GUI, plus a modified NVDA add-on that acts as both the remote-control client and the server's admin interface.

## Motivation

Stock NVDA Remote lets any two clients that happen to share a random key connect to each other through the relay — there's no concept of "who is allowed to control what". That's fine for ad-hoc support sessions, but it's not what you want for a relay you run yourself for a fixed set of known machines: you want to know which machines are allowed to use your server, be notified when an unknown one tries, and approve or block it explicitly.

This fork builds that access-control layer on top of NVDA Remote's existing protocol, plus a handful of features that grew out of actually running the server day to day:

- Session names are pinned to the connecting machine's hostname rather than user-typed, and the server only relays traffic for names an admin has explicitly authorized (`server/data/authorized_keys.json`) — everything else is quarantined and announced until approved.
- Several people can be connected to the same machine as "master" at once — the first becomes the controller, the rest are read-only observers who can take over with a keystroke, instead of only one master connection being possible at all.
- Both sides can update themselves without manual redeployment: the server pushes new add-on releases to connecting clients, and separately checks GitHub on its own schedule for newer releases of itself and of the client.

See [Architecture](#architecture) below for how these pieces fit together, and `client/protocol.md` for the wire format.

## Overview

The repository holds two independent sub-projects that share a protocol but nothing else (no shared build system, no shared dependencies):

| | |
|---|---|
| **`server/`** | A [Twisted](https://twisted.org/)-based relay server, deployed as a Docker container. See [`server/README.md`](server/README.md). |
| **`client/`** | A modified NVDA screen-reader add-on (based on the "Remote" add-on) that is both the remote-control client and the server's admin GUI. See [`client/readme.md`](client/readme.md). |

They talk JSON-over-TCP/TLS, documented in [`client/protocol.md`](client/protocol.md).

## Features

User-visible behavior beyond stock NVDA Remote:

- **Whitelist + quarantine.** Unknown session names are blocked and get a spoken "not authorized" notice every 5 seconds until an admin approves them.
- **Admin GUI**, built into the add-on (*NVDA menu → Remote → Server Administration*): approve/block sessions, manage admin tokens, see the server's own version and whether an update is available, and trigger an immediate GitHub update check by hand.
- **Controller / observer model.** More than one person can connect as master to the same machine at once. The first to join controls it; later joiners are silent observers. An observer takes over with **F10** while nobody is in control; the controller gives it up with **Alt+F10**.
- **Server-pushed client updates.** The server tells every connecting client about the latest release; the add-on downloads and installs it automatically (never downgrading, never restarting NVDA without asking) and offers a one-click restart once it's ready.
- **Optional beta channel.** An opt-in checkbox in the add-on's settings lets a client receive the rolling nightly build instead of stable releases, for testing changes before they're promoted.
- **Server self-update checks.** The server itself checks GitHub daily (interval configurable) — and on demand — for newer releases of its own code and of the official client, purely as a check (it never auto-applies its own updates, only logs/records what it found and refreshes the pointer to the latest client release).

## Getting Started

- To run the server: see [`server/README.md`](server/README.md) (Docker Compose, certificates, admin token).
- To build the client add-on: see [`client/readme.md`](client/readme.md) (scons build).

## Architecture

- **Session model:** a session name identifies a channel on the relay. The client fixes it to `socket.gethostname()` rather than letting the user type one; connecting is always "join as this machine", and controlling a *different*, already-online machine is a separate action (*Remote menu → Control another computer*) that asks the server which other sessions are online and controllable.
- **Versioning:** the client add-on (`vX.Y.Z` tags) and the server (`server-vX.Y.Z` tags) are versioned and released independently, in the same repository but distinct tag namespaces — a client release never implies a server release or vice versa.
- **Release cadence:** small/incidental changes accumulate into a rolling `nightly` pre-release (`make_nightly.sh`) rather than each triggering a new numbered version; numbered releases are cut deliberately, when there's an accumulated batch of changes worth promoting to everyone.

For implementation-level detail (message formats, threading, why specific things are built the way they are) see `CLAUDE.md`, `server/CLAUDE.md` and `client/CLAUDE.md` — these were written as working notes for AI coding agents operating on this codebase, but they're also the most complete design documentation that exists for this project.

## Development

This project is developed by [danijel1124](https://github.com/danijel1124). Most of it was, and still is, hand-built and hand-directed — architecture decisions, requirements, and review all come from the project owner. Earlier on, Gemini was used for orientation in the code (explaining what was there), not for writing it.

Since August 13, 2026, a substantial part of the implementation work has been done with [Claude Code](https://claude.com/claude-code), Anthropic's agentic coding CLI — this time actually writing the code itself under direction and review, not just explaining it. That's the main reason the pace of new features and fixes picked up noticeably from that point on.

## Contributing / Codebase Map

The codebase has grown a lot in a short time, across a lot of files. This section exists to make "which file does what" quick to answer without having to read everything — update it when adding or repurposing a file.

### Server (`server/`)

| File | Purpose |
|---|---|
| `server.py` | Protocol dispatch: `Handler`/`User`/`RemoteServerFactory` — wire-level message handling, admin API, controller/observer control-handoff, self-update-push endpoints. |
| `state.py` | Session/persisted state: `Channel` (who's in a session, who controls it, quarantine) and `ServerState` (whitelist, seen-keys, admin token, release-pointer files, the channel registry). Split from `server.py` because it changes for persistence/session-state reasons, not protocol-dispatch ones. |
| `update_check.py` | Polls GitHub for the server's own newer releases, the latest *official* client release, and the latest beta/nightly client build. |
| `check_server_update.py` | CLI: run an update check immediately, without waiting for the daily schedule. |
| `set_addon_release.py` | CLI: manually override which client version/URL the server pushes (normally kept current automatically). |
| `set_update_check_interval.py` | CLI: change how often the server checks GitHub on its own. |
| `Dockerfile`, `docker-compose.yml` | How the server is built and deployed. |
| `data/` | Persistent runtime state (whitelist, admin token, release pointers) — not in git. |
| `tests/` | Server test suite (`twisted.trial`). |

### Client (`client/addon/globalPlugins/remoteClient/`)

| File | Purpose |
|---|---|
| `__init__.py` | NVDA global plugin entry point; wires everything together on NVDA startup. |
| `client.py` | Top-level orchestration: connection lifecycle for master/slave, config-driven auto-connect, local input hooking. |
| `admin_client.py` | Admin-protocol client (`AdminClientMixin`, mixed into `RemoteClient`): auth, listing/approving/removing sessions, triggering a remote update check. Split from `client.py` because it changes for admin-feature reasons and touches almost none of the master/slave connection state. |
| `transport.py` | Wire protocol I/O — JSON message framing over TCP/TLS, dispatch to registered inbound handlers. |
| `session.py` | Per-connection session logic (master vs. slave), handling of routed messages. |
| `protocol.py` | The shared `RemoteMessageType` enum and other protocol constants. |
| `server.py` | NVDA Remote's own built-in *peer-to-peer* relay, for hosting a direct session without the central relay server — **not** the same thing as `server/server.py` in this repo. |
| `bridge.py` | Bridges between the relay transport and the local peer-to-peer `server.py`. |
| `menu.py` | The add-on's NVDA menu items (Connect, Control another computer, Server Administration, …). |
| `dialogs.py` | Connect / "control another computer" dialog windows. |
| `connection_info.py` | Small value objects describing a target connection. |
| `configuration.py` | `remote.ini` config schema (`configobj`/`configspec`), migrations, `get_config()`. |
| `settings_panel.py` | The add-on's page in NVDA's Settings dialog. |
| `server_admin_gui.py` | The admin GUI (approve/block sessions, tokens, server version, manual update check). |
| `addon_update.py` | Client-side self-update: downloading/installing server-pushed updates, nightly-aware version comparison, restart offer. |
| `localMachine.py` | Executes commands received from a remote peer on this NVDA instance. |
| `secureDesktop.py` | Handles NVDA's secure-desktop transition while a remote session is active. |
| `serializer.py` | Message (de)serialization helpers. |
| `url_handler.py` | Handles `nvdaremote://` links for one-click connect. |
| `nvda_patcher.py` | Monkey-patches to NVDA core needed to support remoting. |
| `input.py` | Captures/replays local keyboard and braille input for remote control. |
| `cues.py`, `beep_sequence.py` | Audio-cue/beep handling. |
| `callback_manager.py` | Small pub/sub-style callback registration helper. |
| `keyboard_hook.py` | Low-level keyboard hook used to capture remote input. |
| `socket_utils.py` | Small networking helpers. |
| `alwaysCallAfter.py` | Decorator ensuring a function runs on the wx GUI thread. |

### Root

| File | Purpose |
|---|---|
| `make_nightly.sh` | Builds and publishes the rolling `nightly` pre-release of the client add-on. |
| `CLAUDE.md`, `server/CLAUDE.md`, `client/CLAUDE.md` | Deep-dive design notes (why things are built the way they are, message formats, gotchas) — this README stays at the "what and why", those go into the "how". |
