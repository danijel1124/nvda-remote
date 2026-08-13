from enum import Enum

PROTOCOL_VERSION: int = 2

class RemoteMessageType(Enum):
    # Connection and Protocol Messages
    protocol_version = "protocol_version"
    join = "join"
    channel_joined = "channel_joined"
    client_joined = "client_joined"
    client_left = "client_left"
    generate_key = "generate_key"
    
    # Control Messages
    key = "key"
    speak = "speak"
    cancel = "cancel"
    pause_speech = "pause_speech"
    tone = "tone"
    wave = "wave"
    send_SAS = "send_SAS"  # Send Secure Attention Sequence
    index = "index"
    
    # Display and Braille Messages
    display = "display"
    braille_input = "braille_input"
    set_braille_info = "set_braille_info"
    set_display_size = "set_display_size"
    
    # Clipboard Operations
    set_clipboard_text = "set_clipboard_text"
    
    # System Messages
    motd = "motd"
    version_mismatch = "version_mismatch"
    ping = "ping"
    pong = "pong"
    error = "error"
    nvda_not_connected = "nvda_not_connected" # This was added in version 2 but never implemented on the server
    
    # Admin Messages
    auth_admin = "auth_admin"
    auth_admin_response = "auth_admin_response"
    admin_list_channels = "admin_list_channels"
    admin_channel_list = "admin_channel_list"
    admin_approve_channel = "admin_approve_channel"
    admin_remove_channel = "admin_remove_channel"
    admin_response = "admin_response"
    # Admin-triggered immediate GitHub update check (v1.1.0/3.2.3) - see
    # server.py's do_admin_check_for_updates. Bypasses the daily due-gate,
    # unlike the server's own scheduled check.
    admin_check_for_updates = "admin_check_for_updates"    # request: no payload
    admin_update_check_response = "admin_update_check_response"  # response: {server: {...}, client: {...}}

    # Session discovery & control handoff (non-admin) - see server.py's
    # do_list_sessions/handle_control_gesture/Channel.toggle_controller.
    list_sessions = "list_sessions"        # request: who else is online & controllable
    session_list = "session_list"          # response: [{key, client_count, has_controller}]
    control_changed = "control_changed"    # broadcast to masters: {controller: <user_id or None>}
    control_denied = "control_denied"      # a non-controller master's input was not relayed

    # Self-update push (non-admin) - see server.py's send_addon_update and
    # addon_update.py. Sent unconditionally on every connection (like motd,
    # before join/authorization), so even a quarantined/outdated client can
    # be told to update itself.
    addon_update = "addon_update"          # {version: "3.2", url: "https://.../remote-3.2.nvda-addon"}

    # Server version info (v1.1.0/3.2.3) - see server.py's send_server_info/
    # do_get_server_info. Request/response, NOT sent unconditionally like
    # motd/addon_update above: an old client that doesn't recognize
    # 'server_info' would log.error on it (RemoteMessageType(...) raises
    # ValueError in transport.py's parse()), which NVDA turns into an
    # audible error.wav on every connect/reconnect. Sent by transport.py's
    # onConnected right alongside protocol_version/join, so only ever
    # received by a client new enough to have sent the request itself - not
    # admin-gated, the server's own version is not sensitive information.
    get_server_info = "get_server_info"    # request: no payload
    server_info = "server_info"            # response: {version: "1.1.0", update_check: {...} | None}


SERVER_PORT = 6837
URL_PREFIX = 'nvdaremote://'


