# NVDA Remote Protocol Documentation

## Overview

The NVDA Remote protocol facilitates communication between two NVDA instances, enabling remote assistance and collaboration. It uses a client-server model where either client can act as the controlling (master) or controlled (slave) machine.

## Connection Establishment

1. Clients connect to a relay server using a TCP connection over SSL/TLS.
2. Clients authenticate by joining a shared channel.
3. The relay server facilitates message passing between connected clients.

## Message Format

Messages are serialized as JSON objects with a 'type' field indicating the message type. Each message is terminated with a newline character ('\n').

## Protocol Version Negotiation

1. Upon connection, the client sends a `protocol_version` message.
2. If versions are incompatible, an error is sent and the connection is closed.

## Message Types

Below is a detailed specification of each message type using JSONSchema:

### Connection Setup

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "protocol_version": {
      "type": "object",
      "properties": {
        "type": { "const": "protocol_version" },
        "version": { "type": "integer" }
      },
      "required": ["type", "version"]
    },
    "join": {
      "type": "object",
      "properties": {
        "type": { "const": "join" },
        "channel": { "type": "string" },
        "connection_type": { "enum": ["master", "slave"] },
        "client_version": { "type": "string", "description": "Added in v3.2.2. Optional self-reported add-on version, dotted-numeric e.g. \"3.2.2\". Older clients omit it. Admin-visibility only (surfaces in the admin_list_channels response) - never echoed back into channel_joined/client_joined, which other, non-admin clients also parse." },
        "allow_beta_updates": { "type": "boolean", "description": "Added in v3.2.4/server v1.2.0. Optional, defaults to false server-side if omitted (older clients). Set from settings_panel.py's 'Allow beta updates' checkbox - true means the server's addon_update push for this connection picks the rolling nightly build (data/addon_beta_release.json) instead of the latest stable release, falling back to stable if no nightly build is available yet. Never echoed back to peers." }
      },
      "required": ["type", "channel", "connection_type"]
    },
    "channel_joined": {
      "type": "object",
      "properties": {
        "type": { "const": "channel_joined" },
        "channel": { "type": "string" },
        "clients": { 
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "id": { "type": "integer" },
              "connection_type": { "enum": ["master", "slave"] }
            },
            "required": ["id", "connection_type"]
          }
        }
      },
      "required": ["type", "channel", "clients"]
    },
    "client_joined": {
      "type": "object",
      "properties": {
        "type": { "const": "client_joined" },
        "client": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" },
            "connection_type": { "enum": ["master", "slave"] }
          },
          "required": ["id", "connection_type"]
        }
      },
      "required": ["type", "client"]
    },
    "client_left": {
      "type": "object",
      "properties": {
        "type": { "const": "client_left" },
        "client": { "type": "integer" }
      },
      "required": ["type", "client"]
    }
  }
}
```

### Control Messages

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "key": {
      "type": "object",
      "properties": {
        "type": { "const": "key" },
        "vk_code": { "type": "integer" },
        "scan_code": { "type": "integer" },
        "extended": { "type": "boolean" },
        "pressed": { "type": "boolean" }
      },
      "required": ["type", "vk_code", "scan_code", "extended", "pressed"]
    },
    "speak": {
      "type": "object",
      "properties": {
        "type": { "const": "speak" },
        "sequence": { 
          "type": "array",
          "items": {
            "oneOf": [
              { "type": "string" },
              { 
                "type": "array",
                "items": [
                  { "type": "string" },
                  { "type": "object" }
                ],
                "minItems": 2,
                "maxItems": 2
              }
            ]
          }
        },
        "priority": { "type": "string" }
      },
      "required": ["type", "sequence", "priority"]
    },
    "cancel": {
      "type": "object",
      "properties": {
        "type": { "const": "cancel" }
      },
      "required": ["type"]
    },
    "pause_speech": {
      "type": "object",
      "properties": {
        "type": { "const": "pause_speech" },
        "switch": { "type": "boolean" }
      },
      "required": ["type", "switch"]
    },
    "tone": {
      "type": "object",
      "properties": {
        "type": { "const": "tone" },
        "hz": { "type": "number" },
        "length": { "type": "number" },
        "left": { "type": "number" },
        "right": { "type": "number" }
      },
      "required": ["type", "hz", "length", "left", "right"]
    },
    "wave": {
      "type": "object",
      "properties": {
        "type": { "const": "wave" },
        "fileName": { "type": "string" },
        "asynchronous": { "type": "boolean" }
      },
      "required": ["type", "fileName"]
    },
    "display": {
      "type": "object",
      "properties": {
        "type": { "const": "display" },
        "cells": { "type": "array", "items": { "type": "integer" } }
      },
      "required": ["type", "cells"]
    },
    "braille_input": {
      "type": "object",
      "properties": {
        "type": { "const": "braille_input" },
        "dots": { "type": "integer" },
        "space": { "type": "boolean" },
        "routingIndex": { "type": "integer" }
      },
      "required": ["type"]
    },
    "set_clipboard_text": {
      "type": "object",
      "properties": {
        "type": { "const": "set_clipboard_text" },
        "text": { "type": "string" }
      },
      "required": ["type", "text"]
    },
    "send_SAS": {
      "type": "object",
      "properties": {
        "type": { "const": "send_SAS" }
      },
      "required": ["type"]
    }
  }
}
```

### Braille Support

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "set_braille_info": {
      "type": "object",
      "properties": {
        "type": { "const": "set_braille_info" },
        "name": { "type": "string" },
        "numCells": { "type": "integer" }
      },
      "required": ["type", "name", "numCells"]
    },
    "set_display_size": {
      "type": "object",
      "properties": {
        "type": { "const": "set_display_size" },
        "sizes": { "type": "array", "items": { "type": "integer" } }
      },
      "required": ["type", "sizes"]
    }
  }
}
```

### Miscellaneous

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "ping": {
      "type": "object",
      "properties": {
        "type": { "const": "ping" }
      },
      "required": ["type"]
    },
    "pong": {
      "type": "object",
      "properties": {
        "type": { "const": "pong" }
      },
      "required": ["type"]
    },
    "error": {
      "type": "object",
      "properties": {
        "type": { "const": "error" },
        "error": { "type": "string", "description": "Machine-readable error code, e.g. 'not_authorized', 'access_denied', 'already_joined'." },
        "message": { "type": "string", "description": "Human-readable error text. Servers send either this or 'error' depending on the failure - clients should tolerate both being absent." }
      },
      "required": ["type"]
    }
  }
}
```

### Session Discovery & Control Handoff

Added in v3.2 (client) / alongside it (server). Every message here is new, so an older client or server that doesn't recognize a type simply ignores it - none of these are folded into pre-existing message types (`channel_joined` in particular never gains fields), keeping this backward-compatible with already-deployed peers on either side.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "list_sessions": {
      "type": "object",
      "description": "Client to server, master or slave, sent on an already-joined and already-authorized connection: ask which *other* sessions on this server are online, authorized, and currently controllable (have a slave connected). The requester's own channel is excluded from the answer. Replied to with 'session_list', or with 'error'/error='not_authorized' if the requester's own channel isn't authorized yet.",
      "properties": {
        "type": { "const": "list_sessions" }
      },
      "required": ["type"]
    },
    "session_list": {
      "type": "object",
      "description": "Server to client, in response to 'list_sessions'.",
      "properties": {
        "type": { "const": "session_list" },
        "sessions": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "key": { "type": "string" },
              "client_count": { "type": "integer" },
              "has_controller": { "type": "boolean" }
            },
            "required": ["key", "client_count", "has_controller"]
          }
        }
      },
      "required": ["type", "sessions"]
    },
    "control_changed": {
      "type": "object",
      "description": "Server to all masters on a channel (never to slaves): who currently controls it. Sent once when a master joins, on every actual change (take-over, release, disconnect of the controller), and repeated every 30s while nobody controls and at least one non-controller master is present.",
      "properties": {
        "type": { "const": "control_changed" },
        "controller": {
          "description": "The controlling master's server-assigned connection id (echoed as 'origin' on that master's own 'channel_joined'), or null/absent if nobody currently controls the channel.",
          "type": ["integer", "null"]
        }
      },
      "required": ["type"]
    },
    "control_denied": {
      "type": "object",
      "description": "Server to a master: sent instead of relaying that master's message, because they are not the channel's current controller. Sent for two distinct cases: (a) throttled (at most once per 3s), when a non-controller master's ordinary input (e.g. 'key') was silently dropped; (b) unthrottled, when a plain F10 take-over attempt was rejected because someone else already controls. A small set of housekeeping message types (e.g. 'set_braille_info') is exempt from this gating and always relayed regardless of controller status.",
      "properties": {
        "type": { "const": "control_denied" }
      },
      "required": ["type"]
    }
  }
}
```

Take-over/release reuses the existing `key` message rather than adding a new wire type for the gesture itself: a plain F10 key-down from a non-controller master takes control if nobody currently controls the channel (denied via `control_denied` if someone already does); a plain F10 from the *current* controller is left alone and relayed as an ordinary keystroke; Alt+F10 from the controller releases control (the server also synthesizes a matching Alt key-up to the slave, so the controlled machine never sees a stuck Alt modifier from the interrupted chord).

### Self-Update Push

Added in v3.2. Sent unconditionally by the server on every new connection (like `motd`, before `join`/authorization - a quarantined or already-outdated client is exactly the one that most needs to be told to update).

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "addon_update": {
      "type": "object",
      "description": "Server to client: the add-on version the server expects clients to run, and where to get it. The client compares this to its own installed version and, if strictly newer, downloads and installs it automatically (no downgrade, ever - a version equal to or older than what's installed is ignored). Installing a new bundle only takes effect after NVDA is restarted; the client announces this rather than restarting NVDA on its own.",
      "properties": {
        "type": { "const": "addon_update" },
        "version": { "type": "string", "description": "Dotted-numeric, e.g. \"3.2\"." },
        "url": { "type": "string", "description": "Direct download URL for the .nvda-addon file." }
      },
      "required": ["type", "version", "url"]
    }
  }
}
```

### Server Version Info

Added in v1.1.0 (server) / v3.2.3 (client). **Request/response, not an unconditional push** like `motd`/`addon_update` above - deliberately so. A client older than v3.2.3 has no `server_info` in its `RemoteMessageType` enum; if the server pushed it unconditionally, `RemoteMessageType(obj["type"])` would raise `ValueError` in that old client's `transport.py`'s `parse()`, logged as `log.error`, which NVDA turns into an audible `error.wav` on every single connect/reconnect. Since only a client that already knows the type would ever send `get_server_info` in the first place, an old client simply never asks and never receives a reply - safe either direction. Not admin-gated (the server's own version isn't sensitive), and works whether or not the client has joined a channel yet.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "get_server_info": {
      "type": "object",
      "description": "Client to server: ask for the server's version info. Sent by transport.py's onConnected, right alongside protocol_version/join, on every fresh connection.",
      "properties": {
        "type": { "const": "get_server_info" }
      },
      "required": ["type"]
    },
    "server_info": {
      "type": "object",
      "description": "Server to client, in response to get_server_info: the relay server's own version, and (if it has completed one) its last known self-update-check result. The client stores this for on-demand display (a menu item / the admin GUI) rather than announcing it proactively.",
      "properties": {
        "type": { "const": "server_info" },
        "version": { "type": "string", "description": "Dotted-numeric, e.g. \"1.1.0\"." },
        "update_check": {
          "type": ["object", "null"],
          "description": "Null if the server hasn't completed a self-update check yet. Otherwise {current_version, latest_version, update_available, url, error, checked_at} - see update_check.py's check_for_update()."
        }
      },
      "required": ["type", "version"]
    }
  }
}
```

### Admin-Triggered Update Check

Added in v1.1.0 (server) / v3.2.3 (client). Admin-only (requires `auth_admin` first) - lets an admin trigger `update_check.py`'s GitHub checks (both the server's own self-update check and the client-release auto-detect/apply check) immediately, instead of only via the server's CLI or its daily scheduled check. Deliberately bypasses the scheduled check's due-gate.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "admin_check_for_updates": {
      "type": "object",
      "description": "Client to server, admin-only: check GitHub for updates right now.",
      "properties": {
        "type": { "const": "admin_check_for_updates" }
      },
      "required": ["type"]
    },
    "admin_update_check_response": {
      "type": "object",
      "description": "Server to client, in response to admin_check_for_updates. server/client are the raw result dicts from update_check.py's check_for_update()/check_for_client_update() respectively.",
      "properties": {
        "type": { "const": "admin_update_check_response" },
        "server": { "type": "object" },
        "client": { "type": "object" }
      },
      "required": ["type", "server", "client"]
    }
  }
}
```

### Consent-Gated Diagnostic Log Retrieval

Added in v1.3.0 (server) / v3.3.0 (client - versions not yet cut as of writing this section). Admin-only, and separately consent-gated on the target session's own machine: `admin_request_logs` asks the server to request a specific online session's NVDA log for troubleshooting; the server relays `request_log_access` to that session's slave connection, which shows its own Yes/No dialog - nothing is read or sent without that explicit, physical answer.

While a request is pending for a channel, the server denies **all** master input on that channel - not just non-controllers, the current controller too, including the F10/Alt+F10 take-over gesture. This is not a redundant precaution: this fork's remote key control (`localMachine.sendKey` → `input.send_key`) uses real Win32 `SendInput`, so an ordinary modal consent dialog on the slave does not, by itself, stop an already-connected controller from synthesizing a "Yes" onto their own consent prompt. The gate closes server-side *before* `request_log_access` is ever sent to the slave (see `server/state.py`'s `PendingLogRequest`), not after - a `key` message already in flight from the controller must not be able to land after the dialog is showing but before the gate closes.

Only the last part of the log is ever sent (`diagnostics.py`'s `LOG_TAIL_MAX_BYTES`, 256KB), tail-capped client-side; the server independently caps what it will write to disk (`state.py`'s `MAX_DIAGNOSTIC_LOG_BYTES`, 1MiB) as a backstop against a modified client. The consent dialog suggests restarting NVDA first if the person wants to shrink what's included before sharing it.

A pending request times out after `LOG_REQUEST_TIMEOUT` (60s, server-side) if nobody answers, and is cleared immediately if either the requesting admin or the slave being asked disconnects - either way, the gate must never outlive the connection it depends on.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "admin_request_logs": {
      "type": "object",
      "description": "Admin to server: ask a specific session for permission to upload its NVDA log.",
      "properties": {
        "type": { "const": "admin_request_logs" },
        "key": { "type": "string", "description": "The session name to request logs from." }
      },
      "required": ["type", "key"]
    },
    "admin_log_upload_status": {
      "type": "object",
      "description": "Server to the requesting admin: the eventual (or immediate, for a rejected request) outcome.",
      "properties": {
        "type": { "const": "admin_log_upload_status" },
        "key": { "type": ["string", "null"] },
        "status": { "enum": ["saved", "denied", "timeout", "error"] },
        "detail": { "type": ["string", "null"], "description": "Saved file path (relative to the server's data dir) on 'saved'; a short reason code on 'error'." },
        "truncated": { "type": "boolean" }
      },
      "required": ["type", "key", "status"]
    },
    "request_log_access": {
      "type": "object",
      "description": "Server to the target session's slave connection: the consent request itself, shown as a Yes/No dialog.",
      "properties": {
        "type": { "const": "request_log_access" }
      },
      "required": ["type"]
    },
    "log_access_response": {
      "type": "object",
      "description": "Slave to server: the human's own answer to the consent dialog.",
      "properties": {
        "type": { "const": "log_access_response" },
        "granted": { "type": "boolean" }
      },
      "required": ["type", "granted"]
    },
    "log_upload": {
      "type": "object",
      "description": "Slave to server, only sent after granted=true: the (tail-capped) log content.",
      "properties": {
        "type": { "const": "log_upload" },
        "content": { "type": "string" },
        "truncated": { "type": "boolean" }
      },
      "required": ["type", "content", "truncated"]
    }
  }
}
```

## Security Considerations

- All connections are encrypted using SSL/TLS.
- Clients can verify the server's certificate fingerprint to prevent man-in-the-middle attacks.
- The channel key acts as a shared secret for authentication.

## Reliability (Heartbeat)

To maintain active connections and prevent timeouts by network infrastructure (firewalls, NAT), the protocol implements a heartbeat:
- **Server to Client:** The server sends a `ping` every 5 minutes.
- **Client to Server:** The client responds immediately with a `pong`.
- **Client to Server:** The client may also send a `ping`, which the server will answer with a `pong`.

## Error Handling

- Connection errors trigger automatic reconnection attempts.
- Protocol errors are communicated using the `error` message type.

This protocol documentation provides a high-level overview of the NVDA Remote functionality. For detailed implementation, refer to the source code files, particularly `transport.py`, `session.py`, and `serializer.py`.
