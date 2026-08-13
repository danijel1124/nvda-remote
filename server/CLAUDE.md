# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Server-specific guidance. See also `../CLAUDE.md` for the repo-wide architecture (whitelist/session model shared with the client).

## Commands

Run and build (via Docker Compose, from this directory):
```bash
# SSL cert/key/chain must exist first, in ./certificate/ (cert, key, chain — PEM files)
docker compose up --build -d
docker compose logs -f
```

Run directly without Docker (e.g. for local debugging):
```bash
pip install -r requirements.txt
python server.py -c certificate/cert -k certificate/key -C certificate/chain [-p 6837] [-m motd_file] [-i ::]
```

There's no linter configured for this sub-project. There is a test suite (`tests/`, `twisted.trial`-based) — see Testing below.

## Testing

```bash
pip install -r requirements.txt   # needs Twisted; the repo's own venv/ may not have it
python -m twisted.trial tests
```
Uses `twisted.internet.task.Clock` (injected via `ServerState(clock=...)`) instead of the real reactor, so quarantine/control-free timers can be advanced deterministically without real sleeps, and a `FakeTransport` (subclassing `twisted.test.proto_helpers.StringTransportWithDisconnection`, adding a no-op `setTcpNoDelay`) in place of real sockets.

## Architecture

Single-file Twisted app (`server.py`). Key pieces:

- **`Handler`** (a `LineReceiver`, one instance per TCP connection): parses newline-delimited JSON lines and dispatches by `type` field to `do_<type>` methods (e.g. `do_join`, `do_ping`, `do_generate_key`, `do_list_sessions`). Messages with `type` starting with `admin_` are routed through `handle_admin_command` and require `self.user.is_admin`. Any message type not handled explicitly (and not admin-scoped) that arrives from a user already in a channel is instead relayed as-is to the other channel members (`user.channel.send_to_clients`) — the server itself doesn't need to understand remote-control message types like `speak`/`key`/`display`. Before that generic relay path, raw `key` messages carrying F10/Alt+F10 are intercepted by `handle_control_gesture` for the controller take-over/release gesture (see below), and any other master input is gated: dropped (with a throttled `control_denied` reply) unless the sender is the channel's current `controller` or the message type is in `GATE_EXEMPT_MASTER_TYPES` (housekeeping like `set_braille_info`, not real "steering").
- **`User`**: per-connection state (channel membership, `connection_type` = master/slave, admin flag, and the controller-gesture bookkeeping: `held_alt_msg`, `suppress_f10_up`/`suppress_alt_up`, `last_control_denied_at` for throttling). `generate_key()` produces a random legacy pairing code (rate-limited per source IP).
- **`Channel`**: one per session name. Holds joined clients, tracks/broadcasts `client_joined`/`client_left`, and drives the quarantine loop (`send_quarantine_msg` every `QUARANTINE_MSG_INTERVAL` = 5s) while `is_authorized` is false. `send_to_clients` is a no-op if the channel isn't authorized — this is the actual enforcement point that keeps unauthorized traffic from being relayed. Also owns the **controller/observer model**: `self.controller` (a `User` or `None`) — the first master to join an uncontrolled channel is auto-assigned as controller (so a lone master/slave pair, today's only production topology, behaves exactly as before with zero client changes); `toggle_controller(user)` handles take/release/deny for the F10/Alt+F10 gesture; `broadcast_control_changed()` sends `control_changed` to masters only (never slaves, and never folded into `channel_joined`, so old clients that don't understand it just ignore it); while nobody controls and at least one non-controller master is listening, `start_control_free_loop`/`update_control_free_loop` repeat a reminder every `CONTROL_FREE_MSG_INTERVAL` = 30s, mirroring the quarantine-reminder pattern.
- **`ServerState`**: process-wide singleton holding `channels` (active), `authorized_keys` (whitelist, persisted to `data/authorized_keys.json`), `seen_keys` (everything ever seen, persisted to `data/seen_keys.json`), and the admin token (`data/admin.token`, auto-generated on first run via `secrets.token_urlsafe`). All three JSON files live under `DATA_DIR` (`data/`, mounted as a Docker volume). Takes an optional `clock` (defaults to the real `reactor`) threaded through the quarantine and control-free timers, so tests can inject a fake one.
- **Admin API** (requires `auth_admin` with the token first): `do_admin_list_channels` (lists whitelist ∪ online ∪ ever-seen, each annotated with online/authorized/client_count), `do_admin_approve_channel`, `do_admin_remove_channel`. **Non-admin session discovery**: `do_list_sessions` — any already-joined, already-authorized client may ask (no admin token needed) which *other* authorized, online, currently-controllable (has a slave connected) sessions exist, via `list_sessions` → `session_list` (`[{key, client_count, has_controller}]`); this deliberately excludes the requester's own channel and anything offline/unauthorized/slave-less. Writes (approve/block) stay admin-only — only reads were opened up.
- **Protocol versioning**: `protocol_version` message sets `Handler.protocol_version`; this changes the shape of a few messages (`channel_joined`/`client_joined`/`client_left` carry richer per-client dicts, via `User.as_dict()`, from v2 onward) and whether `origin` is echoed back on relayed messages.

## Notes for changes

- `Channel.remove_connection` tears down (and deregisters from `ServerState`) a channel once its last client leaves — channels are not persistent objects, only the whitelist/seen-keys entries are. It also clears `controller`/stops the control-free loop if the leaving client was the controller.
- Any new client-facing capability should be added as a new top-level `do_<type>` on `Handler` or a new method on `ServerState`/`Channel`, following the existing pattern — not folded into the admin-only path unless it should actually require the admin token (see `do_list_sessions` for the "open read, admin-only write" split).
- **Backward compatibility is load-bearing**: some already-deployed clients can't be updated in lockstep with the server (see the deferred per-user-visibility note in `../CLAUDE.md`). Never add a required field to an existing message type's payload (e.g. `channel_joined` must never gain a `controller` field — old clients have fixed, non-`**kwargs` handlers and would break). New information belongs on an entirely new message type that an unrecognized client silently ignores (`control_changed`, `control_denied`, `list_sessions`/`session_list` are all examples of this).
