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

    # Session discovery & control handoff (non-admin) - see server.py's
    # do_list_sessions/handle_control_gesture/Channel.toggle_controller.
    list_sessions = "list_sessions"        # request: who else is online & controllable
    session_list = "session_list"          # response: [{key, client_count, has_controller}]
    control_changed = "control_changed"    # broadcast to masters: {controller: <user_id or None>}
    control_denied = "control_denied"      # a non-controller master's input was not relayed


SERVER_PORT = 6837
URL_PREFIX = 'nvdaremote://'


