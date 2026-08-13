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
        "client_version": { "type": "string", "description": "Added in v3.2.2. Optional self-reported add-on version, dotted-numeric e.g. \"3.2.2\". Older clients omit it. Admin-visibility only (surfaces in the admin_list_channels response) - never echoed back into channel_joined/client_joined, which other, non-admin clients also parse." }
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
