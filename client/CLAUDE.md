# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Client-specific guidance. See also `../CLAUDE.md` for the repo-wide architecture (whitelist/session model shared with the server).

## Commands

Build the add-on (needs `scons` and `markdown` installed, e.g. in the repo's `venv`):
```bash
scons
```
Produces `remote-<version>.nvda-addon` (currently `remote-3.2.1.nvda-addon`) in this directory. `scons` is also the default SConstruct target — CI (`../.github/workflows/main.yml`, at the *repo root*, not under `client/` — GitHub Actions only discovers workflows at the repository root, so it must live there even though this sub-project used to be the whole repo) runs `pip install scons markdown && scons` (via `working-directory: client`) on every push/PR to `master`/`main`, and on any tag push additionally creates a GitHub Release with the built `.nvda-addon` attached.

Other scons targets:
```bash
scons pot        # extract translatable strings to build/*.pot
scons mergePot    # merge extracted strings into existing locale .po files
```

Linting: a flake8 config exists at `flake8.ini` (NVDA add-on template config: tabs, max-line-length 110, max-complexity 15) but is not wired into scons or CI — run manually, e.g. `flake8 --append-config=flake8.ini addon/globalPlugins/remoteClient`.

`tests/` holds logic-level regression tests for `menu.py`/`dialogs.py`/`connection_info.py`/`addon_update.py` (item-enablement state machine, session picker, config round-trip, self-update version-gating), run with the *system* `python3` (needs `configobj`, not NVDA-only):
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
- **`addon_update.py`** (v3.2) — self-update, pushed by the server via `addon_update` (registered in `RemoteSession.__init__`, so it fires on both master and slave connections). `last_handled_version`/`last_handled_failed` in config are the *primary* gate (checked before comparing against the installed version), not just a dedup convenience: `addonHandler.getCodeAddon().version` still reports the old version until NVDA is restarted to complete a pending install, so gating on the installed version alone would re-download and reinstall the same update on every `ConnectorThread` reconnect. Downloads (plain `urllib`, normal cert validation — deliberately not inheriting the relay transport's `insecure`/trust-fingerprint state) and installs (`addonHandler.AddonBundle`/`installAddonBundle`, the two public entry points — no private `gui.addonGui` helpers, since none of this can be import-tested outside NVDA) happen on a background thread; NVDA is never auto-restarted, only announced — a screen-reader user losing NVDA mid-task without warning is worse than a delayed update. Never auto-downgrades: only a strictly newer version triggers anything. `installAddonBundle` only extracts the new version to a pending-install path — it does *not* remove a same-ID existing install on its own (confirmed by reading NVDA core's source), so the old add-on's `requestRemove()` must be called too, same as `gui.addonGui.installAddon` does.

## Notes for changes

- Never delete existing GUI functionality (e.g. in `server_admin_gui.py`) when extending it — add to it, keep all features working.
- After changing anything under `addon/globalPlugins/remoteClient/`, rebuild with `scons` before considering the change done.
- **`transport.transportClosing`/`transportDisconnected` are two different events** (deliberate close vs. unexpected drop) and code needs to react to both, not just the one that's easiest to trigger in manual testing. Two real bugs so far had the same shape: a handler existed but was registered only on `transportClosing`, so an unexpected drop silently skipped cleanup. One (`MasterSession._resetControlState`) was caught in review; the other (`SlaveSession.handleTransportDisconnected`) shipped and was found via a real user's NVDA log — it left `nvwave.decide_playWaveFile` (the outbound wave-relay hook) registered after a drop, so NVDA's own `error.wav` (played for any ERROR-level log entry) kept getting relayed through the disconnected transport, which logged its own ERROR, triggering another `error.wav`, forever - 483 occurrences in one captured log. Fixed in 3.2.1 by registering `handleTransportDisconnected` on `transportDisconnected` (it wasn't registered anywhere at all) and downgrading `transport.py`'s "attempted to send while not connected" from `log.error` to `log.warning` as defense in depth, since ERROR-level logging is uniquely dangerous here (it's audible and self-relayable), not just noisy.
- `server.py`'s (the secure-desktop bridge, not the top-level relay) `ssl.wrap_socket()` was removed in Python 3.12 - newer NVDA builds crashed with `AttributeError` on every secure-desktop transition. Use `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` + `load_cert_chain` + `context.wrap_socket(sock, server_side=True)` instead.
