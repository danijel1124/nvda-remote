# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Client-specific guidance. See also `../CLAUDE.md` for the repo-wide architecture (whitelist/session model shared with the server).

## Commands

Build the add-on (needs `scons` and `markdown` installed, e.g. in the repo's `venv`):
```bash
scons
```
Produces `remote-<version>.nvda-addon` (currently `remote-3.2.nvda-addon`) in this directory. `scons` is also the default SConstruct target — CI (`.github/workflows/main.yml`) just runs `pip install scons markdown && scons`.

Other scons targets:
```bash
scons pot        # extract translatable strings to build/*.pot
scons mergePot    # merge extracted strings into existing locale .po files
```

Linting: a flake8 config exists at `flake8.ini` (NVDA add-on template config: tabs, max-line-length 110, max-complexity 15) but is not wired into scons or CI — run manually, e.g. `flake8 --append-config=flake8.ini addon/globalPlugins/remoteClient`.

`tests/` holds logic-level regression tests for `menu.py`/`dialogs.py`/`connection_info.py` (item-enablement state machine, session picker, config round-trip), run with the *system* `python3` (needs `configobj`, not NVDA-only):
```bash
python3 -m unittest discover -s tests -v
```
It works by installing minimal stand-ins for `wx`/`gui`/`addonHandler`/`logHandler`/`globalVars` in `sys.modules` and importing the target submodules directly, bypassing `remoteClient/__init__.py` (which pulls in `client.py`). `client.py` itself (and anything that imports it) cannot be unit-tested outside a real NVDA process — it imports `ctypes.wintypes`, `api`, `braille`, `core`, `keyboardHandler`, `winUser`, `utils.security`, none of which exist as installable packages — so its logic has to be verified by careful reading (and, ultimately, a live run against the control server) rather than automated tests.

Change the add-on version in `buildVars.py` (`addon_info["addon_version"]`), not in `sconstruct`.

## Architecture

All product code lives in `addon/globalPlugins/remoteClient/`, an NVDA global plugin package. `__init__.py` is the NVDA entry point: it instantiates a single `RemoteClient` and registers global gestures (`ctrl+shift+NVDA+c` = generate/copy link, `alt+NVDA+pageUp` = connect, `alt+NVDA+pageDown` = disconnect, `f11` = toggle sending keyboard input to the remote machine).

Connection roles: a session is either **master** (this machine controls a remote one) or **slave** (this machine is being controlled) — `ConnectionMode` in `connection_info.py`. `RemoteClient` (`client.py`) can hold a `masterSession`/`masterTransport` and a `slaveSession`/`slaveTransport` concurrently and independently (they're separate TCP connections); most of the day-to-day master/slave asymmetry lives in `session.py`.

Layering, roughly bottom-up:
- **`transport.py`** — `Transport`/`TCPTransport`/`RelayTransport`: raw framed JSON-over-TLS socket handling, a reconnect thread (`ConnectorThread`), and a pub/sub-style `registerInbound(RemoteMessageType, callback)` mechanism used by everything above it. `RelayTransport.create()` builds one from a `ConnectionInfo`.
- **`protocol.py`** — `RemoteMessageType` enum, the single source of truth for wire message type names (also documented in `protocol.md`); `SERVER_PORT` (6837) and the `nvdaremote://` URL prefix.
- **`session.py`** — `RemoteSession` base plus `MasterSession`/`SlaveSession`: wires transport inbound messages to actual NVDA behavior (speaking, braille, patched input) via `nvda_patcher.py` (`NVDASlavePatcher`/`NVDAMasterPatcher`, hooking NVDA's speech/braille pipeline) and `localMachine.py` (`LocalMachine`, the local audio/braille/clipboard sink used by a slave session).
- **`client.py`** — `RemoteClient`: top-level orchestrator. Owns connect/disconnect flows for both roles, the low-level keyboard hook thread (`keyboard_hook.py`, forwards keys to the remote machine while `sendingKeys` is true, toggled by F11 via `toggleRemoteKeyControl`), and admin-protocol plumbing (`send_admin_*`/`handle_admin_*`, used by `server_admin_gui.py`).
- **UI**: `menu.py` (`RemoteMenu`, the Tools → Remote submenu — item enablement is driven by `handleConnected`/`handleConnecting`, tracking master/slave connectedness separately since the two roles are independent, concurrent connections), `dialogs.py` (`DirectConnectDialog` — connects as slave only, no master/slave choice; `ControlAnotherComputerDialog` — plain `wx.ListBox` picker populated from a `list_sessions`/`session_list` round-trip, for switching to controlling another already-online machine without reconnecting; `CertificateUnauthorizedDialog`), `server_admin_gui.py` (tabbed admin GUI: token management + session/whitelist management, talks to the server's `admin_*` protocol messages), `settings_panel.py` (NVDA Settings integration).
- **`configuration.py`** — `configobj`-based `remote.ini` handling with a strict configspec. Notably: `key` (session name) is force-set to `socket.gethostname()` on load, overriding anything else; `migrate_config()` handles upgrades between config versions (including legacy admin-token migration, prompting via a dialog); `minify_config()` strips config entries not present in the configspec (wired to the menu's "Clean up configuration..." item).
- **`secureDesktop.py`** — keeps a slave session alive across NVDA's secure-desktop switch (UAC prompts, lock screen) by relaying through a `bridge.py`/local relay (`server.py` in this package — a small `LocalRelayServer`/`Client` pair, distinct from the top-level `server/` project — used only for this loopback secure-desktop bridging, not for real remote relaying).
- **`url_handler.py`** — registers the `nvdaremote://` URL protocol (Windows registry) so links can trigger `RemoteClient.verifyAndConnect`.

## Notes for changes

- Never delete existing GUI functionality (e.g. in `server_admin_gui.py`) when extending it — add to it, keep all features working.
- After changing anything under `addon/globalPlugins/remoteClient/`, rebuild with `scons` before considering the change done.
