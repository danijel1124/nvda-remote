"""In-memory and on-disk server state for the NVDA Remote relay.

Channel (which clients are in a session, who controls it, quarantine status)
and ServerState (the whitelist, seen-keys, admin token, and release-pointer
files, plus the top-level channel registry that creates Channels) are split
out of server.py because they change for persistence/session-state reasons,
not protocol-dispatch reasons - server.py's Handler/User/RemoteServerFactory
are about wire-level message handling, this is about what's known/remembered
regardless of any particular connection. See root CLAUDE.md's "one file, one
reason to change" convention.

Channel and ServerState move together (not ServerState alone) because
they're mutually coupled: ServerState.find_or_create_channel() constructs
Channel objects, and Channel reads back self.server_state.is_key_authorized/
clock/etc. Channel itself only duck-types against its connected clients
(.connection_type/.protocol.protocol_version/.user_id/.send()/.as_dict()) -
it never needs server.py's Handler/User class definitions, so there's no
circular import between this module and server.py.
"""
from twisted.internet import reactor
from twisted.internet.task import LoopingCall
from twisted.python import log

from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
import re
import secrets

QUARANTINE_MSG_INTERVAL = 5.0
CONTROL_FREE_MSG_INTERVAL = 30.0

DATA_DIR = "data"
AUTH_KEYS_FILE = os.path.join(DATA_DIR, "authorized_keys.json")
SEEN_KEYS_FILE = os.path.join(DATA_DIR, "seen_keys.json")
ADMIN_TOKEN_FILE = os.path.join(DATA_DIR, "admin.token")
# {"version": "3.2", "url": "https://.../remote-3.2.nvda-addon"} - written by
# the release process, read fresh on every connection (see
# ServerState.get_addon_release) rather than cached at startup, so announcing
# a new client release never requires restarting the server. Absent/empty is
# the default "nothing to push" state.
ADDON_RELEASE_FILE = os.path.join(DATA_DIR, "addon_release.json")
# Same shape as ADDON_RELEASE_FILE, but for the rolling nightly build (see
# make_nightly.sh / update_check.py's check_for_client_beta_update) - only
# ever pushed to a connection that opted in via allow_beta_updates. Stable
# clients never read this file at all.
ADDON_BETA_RELEASE_FILE = os.path.join(DATA_DIR, "addon_beta_release.json")

# Where an admin-requested diagnostic log upload (see PendingLogRequest,
# save_diagnostic_log) gets written. Deliberately under DATA_DIR (the same
# Docker volume as everything else persistent) so it survives a container
# restart/redeploy and is trivial to read directly off disk.
DIAGNOSTIC_LOG_DIR = os.path.join(DATA_DIR, "diagnostic_logs")
# Hard cap on what gets written to disk, independent of whatever cap the
# uploading client itself enforces (client/addon/.../diagnostics.py's
# LOG_TAIL_MAX_BYTES) - a second, server-side backstop against a buggy or
# malicious client filling up the data volume. Not a wire-protocol guard:
# Handler.MAX_LENGTH (20MB/line) already bounds the inbound line before it
# ever reaches save_diagnostic_log, this only bounds what ends up on disk.
MAX_DIAGNOSTIC_LOG_BYTES = 1024 * 1024  # 1 MiB


def save_diagnostic_log(data_dir, key, content):
	"""Writes an admin-requested diagnostic log upload to
	<data_dir>/diagnostic_logs/<safe key>_<UTC timestamp>.log and returns
	that path, relative to data_dir. `key` is a session name the server
	already tracks as an existing channel (not attacker-controlled
	free text), but is still passed through a conservative filename-safe
	filter here rather than used directly, since it becomes a path
	component."""
	encoded = content.encode('utf-8')
	if len(encoded) > MAX_DIAGNOSTIC_LOG_BYTES:
		# Byte-accurate tail cap (matching the "BYTES" in the constant's
		# name and the docstring above) - slicing the str by len() would
		# undercount anything with non-ASCII text (e.g. German speech
		# output in NVDA's log) since those characters are 2-4 bytes each
		# in UTF-8. errors='ignore' drops a possibly-truncated multi-byte
		# sequence at the cut point rather than raising.
		content = encoded[-MAX_DIAGNOSTIC_LOG_BYTES:].decode('utf-8', errors='ignore')
	safe_key = re.sub(r'[^A-Za-z0-9_.-]', '_', key)[:100] or "unknown"
	timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
	filename = f"{safe_key}_{timestamp}.log"
	directory = os.path.join(data_dir, "diagnostic_logs")
	os.makedirs(directory, exist_ok=True)
	path = os.path.join(directory, filename)
	with open(path, 'w', encoding='utf-8') as f:
		f.write(content)
	return os.path.join("diagnostic_logs", filename)


class PendingLogRequest:
	"""Tracks one in-flight admin-initiated diagnostic log request for a
	Channel: who asked (an admin User), who's being asked (the channel's
	slave User), and the pending timeout DelayedCall.

	While a Channel's pending_log_request is set, Handler.lineReceived
	denies ALL master input on that channel - not just non-controllers, the
	actual controller too (see the check next to GATE_EXEMPT_MASTER_TYPES).
	This has to be a genuine, unconditional gate rather than trusting the
	slave's own consent dialog to be modal: this fork's remote key control
	(localMachine.sendKey -> input.send_key) uses real Win32 SendInput, so
	an already-connected controller could otherwise just synthesize an
	"OK" onto their own consent prompt. The gate must be set *before* the
	request is ever relayed to the slave (see do_admin_request_logs) - if
	relayed first, a `key` message already in flight from the controller
	could still land after the dialog is showing but before the gate closes.
	"""
	def __init__(self, admin, slave, timeout_call=None):
		self.admin = admin
		self.slave = slave
		self.timeout_call = timeout_call

	def cancel_timeout(self):
		if self.timeout_call is not None and self.timeout_call.active():
			self.timeout_call.cancel()
		self.timeout_call = None


class Channel(object):
	def __init__(self, key, server_state=None):
		self.clients = OrderedDict()
		self.key = key
		self.server_state = server_state
		self.quarantine_loop = None
		# The single client (a 'master') currently allowed to send control/input
		# messages. None means the channel is up for grabs - see toggle_controller.
		self.controller = None
		self.control_free_loop = None
		# See PendingLogRequest's docstring - a diagnostic log request
		# in-flight for this channel, or None.
		self.pending_log_request = None
		log.msg(f"Channel created for key: {self.key}")
		self.check_authorization()

	def get_slave(self):
		"""The channel's slave connection - the NVDA instance whose hostname
		this session name is pinned to (see the repo-wide session model) -
		or None if only masters are currently connected, or nobody is. At
		most one slave is ever expected per channel in this fork's
		hostname-pinned model."""
		for client in self.clients.values():
			if client.connection_type == 'slave':
				return client
		return None

	def check_authorization(self):
		self.is_authorized = self.server_state.is_key_authorized(self.key)
		log.msg(f"Checking auth for {self.key}: {self.is_authorized}")
		if self.is_authorized:
			self.stop_quarantine()
		else:
			self.start_quarantine()

	def start_quarantine(self):
		if self.quarantine_loop is None or not self.quarantine_loop.running:
			self.quarantine_loop = LoopingCall(self.send_quarantine_msg)
			clock = getattr(self.server_state, 'clock', None)
			if clock is not None:
				self.quarantine_loop.clock = clock
			self.quarantine_loop.start(QUARANTINE_MSG_INTERVAL)

	def stop_quarantine(self):
		if self.quarantine_loop and self.quarantine_loop.running:
			self.quarantine_loop.stop()
		self.quarantine_loop = None

	def send_quarantine_msg(self):
		if self.clients:
			log.msg(f"Sending quarantine message to channel {self.key}")
		for client in self.clients.values():
			client.send(
				type='speak',
				sequence=['Nicht autorisiert. Warten auf Freigabe.']
			)

	def add_client(self, client):
		# The first master to join a channel with no controller takes control
		# automatically. This keeps every pre-existing single-master connection
		# (i.e. every client that predates take-over/observer support) working
		# exactly as before, with no protocol changes required on its part.
		if client.connection_type == 'master' and self.controller is None:
			self.controller = client
		# Deliberately NOT adding a `controller` field here: channel_joined is a
		# message type every already-deployed client recognizes and has a fixed
		# handler for (no **kwargs catch-all); an unexpected extra field risks
		# breaking that handler on every client we can't update. control_changed
		# is a message type old clients don't know at all, which fails cleanly
		# at the type lookup and is safely ignored - so that's used instead,
		# both here (for a joining observer) and via broadcast_control_changed.
		if client.protocol.protocol_version == 1:
			ids = [c.user_id for c in self.clients.values()]
			msg = dict(type='channel_joined', channel=self.key, user_ids=ids, origin=client.user_id)
		else:
			clients = [i.as_dict() for i in self.clients.values()]
			msg = dict(type='channel_joined', channel=self.key, origin=client.user_id, clients=clients)

		client.send(**msg)
		for existing_client in self.clients.values():
			if existing_client.protocol.protocol_version == 1:
				existing_client.send(type='client_joined', user_id=client.user_id)
			else:
				existing_client.send(type='client_joined', client=client.as_dict())
		self.clients[client.user_id] = client
		if self.controller is client:
			self.broadcast_control_changed()
		elif client.connection_type == 'master':
			# A joining observer: tell them immediately who's in control instead
			# of leaving them to find out from the 30s reminder or a denial.
			controller_id = self.controller.user_id if self.controller else None
			client.send(type='control_changed', controller=controller_id)
		self.update_control_free_loop()

	def remove_connection(self, con):
		if con.user_id in self.clients:
			del self.clients[con.user_id]
		controller_left = self.controller is con
		if controller_left:
			self.controller = None
		# Whichever side of a pending diagnostic-log request just left - the
		# requesting admin (no one left to report the outcome to) or the
		# slave being asked (no one left to answer) - the gate must not
		# outlive the connection it depends on, or every master on this
		# channel stays locked out forever.
		pending = self.pending_log_request
		if pending is not None and con in (pending.admin, pending.slave):
			pending.cancel_timeout()
			self.pending_log_request = None
		for client in self.clients.values():
			if client.protocol.protocol_version == 1:
				client.send(type='client_left', user_id=con.user_id)
			else:
				client.send(type='client_left', client=con.as_dict())
		if not self.clients:
			self.stop_quarantine()
			self.stop_control_free_loop()
			self.server_state.remove_channel(self.key)
			return
		if controller_left:
			self.broadcast_control_changed()
		self.update_control_free_loop()

	def ping_clients(self):
		self.send_to_clients({'type': 'ping'})

	def send_to_clients(self, obj, exclude=None, origin=None, only_masters=False):
		if not self.is_authorized:
			return
		for client in self.clients.values():
			if client is exclude:
				continue
			if only_masters and client.connection_type != 'master':
				continue
			client.send(origin=origin, **obj)

	# --- Control handoff (take-over/release, observer mode) ---

	def toggle_controller(self, user):
		"""Handle a non-relayed F10 `key` press from a master: take control if the
		channel currently has none, release it if this user is the controller,
		or deny it if someone else is already controlling."""
		if self.controller is None:
			self.controller = user
		elif self.controller is user:
			self.controller = None
		else:
			# An explicit, discrete take-over attempt while someone else already
			# controls - unlike a stream of denied keystrokes, this fires once
			# per physical F10 press, so it must not share the input-denial
			# throttle: that's the exact feedback the user asked for by name.
			user.send(type='control_denied')
			return
		self.broadcast_control_changed()
		self.update_control_free_loop()

	def broadcast_control_changed(self):
		# Only masters act on this (observers waiting to take over, the new
		# controller confirming their take-over); slaves have no handler for it
		# and - crucially - neither do any already-deployed pre-take-over
		# clients, who would otherwise log an unhandled-message error on every
		# occurrence, including the 30s reminder, for machines we can't update.
		controller_id = self.controller.user_id if self.controller else None
		self.send_to_clients({'type': 'control_changed', 'controller': controller_id}, only_masters=True)

	def has_listening_masters(self):
		return any(
			c.connection_type == 'master' and c is not self.controller
			for c in self.clients.values()
		)

	def update_control_free_loop(self):
		if self.controller is None and self.has_listening_masters():
			self.start_control_free_loop()
		else:
			self.stop_control_free_loop()

	def start_control_free_loop(self):
		if self.control_free_loop is None or not self.control_free_loop.running:
			self.control_free_loop = LoopingCall(self.broadcast_control_changed)
			clock = getattr(self.server_state, 'clock', None)
			if clock is not None:
				self.control_free_loop.clock = clock
			# now=False: the change that led here (controller becoming None) was
			# already broadcast by the caller; don't send a duplicate immediately.
			self.control_free_loop.start(CONTROL_FREE_MSG_INTERVAL, now=False)

	def stop_control_free_loop(self):
		if self.control_free_loop and self.control_free_loop.running:
			self.control_free_loop.stop()
		self.control_free_loop = None


class ServerState(object):
	def __init__(self, clock=None):
		self.channels = {}
		self.generated_keys = set()
		self.generated_ips = {}
		self.motd = None
		self.authorized_keys = set()
		self.seen_keys = set()
		self.admin_token = ""
		# Injectable scheduler for LoopingCalls (quarantine/control-free reminders).
		# Defaults to the real reactor; tests pass a twisted.internet.task.Clock
		# so periodic messages can be advanced deterministically without waiting.
		self.clock = clock if clock is not None else reactor
		self.init_data_dir()
		self.load_keys()
		self.load_seen_keys()
		self.load_or_generate_admin_token()

	def init_data_dir(self):
		if not os.path.exists(DATA_DIR):
			os.makedirs(DATA_DIR)

	def load_keys(self):
		if os.path.exists(AUTH_KEYS_FILE):
			try:
				with open(AUTH_KEYS_FILE, 'r') as f:
					self.authorized_keys = set(json.load(f))
					log.msg(f"Loaded {len(self.authorized_keys)} authorized keys.")
			except Exception as e:
				log.err(f"Failed to load keys: {e}")

	def save_keys(self):
		try:
			with open(AUTH_KEYS_FILE, 'w') as f:
				json.dump(list(self.authorized_keys), f)
		except Exception as e:
			log.err(f"Failed to save keys: {e}")

	def load_seen_keys(self):
		if os.path.exists(SEEN_KEYS_FILE):
			try:
				with open(SEEN_KEYS_FILE, 'r') as f:
					self.seen_keys = set(json.load(f))
			except:
				pass
		# Always include authorized keys in seen keys
		self.seen_keys.update(self.authorized_keys)

	def save_seen_keys(self):
		try:
			with open(SEEN_KEYS_FILE, 'w') as f:
				json.dump(list(self.seen_keys), f)
			log.msg(f"Saved {len(self.seen_keys)} seen keys to {SEEN_KEYS_FILE}")
		except Exception as e:
			log.err(f"Failed to save seen keys: {e}")

	def is_key_authorized(self, key):
		authorized = key in self.authorized_keys
		log.msg(f"Is key '{key}' authorized? {authorized}")
		return authorized

	def authorize_key(self, key):
		self.authorized_keys.add(key)
		self.seen_keys.add(key)
		self.save_keys()
		self.save_seen_keys()
		log.msg(f"Key authorized: {key}")

	def deauthorize_key(self, key):
		if key in self.authorized_keys:
			self.authorized_keys.remove(key)
			self.save_keys()
			log.msg(f"Key deauthorized: {key}")
		if key in self.seen_keys:
			self.seen_keys.remove(key)
			self.save_seen_keys()
			log.msg(f"Key removed from seen list: {key}")

	def get_addon_release(self):
		"""Reads ADDON_RELEASE_FILE fresh on every call (see the comment on
		that constant) and returns (version, url), or (None, None) if there's
		nothing to push. Never raises - this runs from Handler.connectionMade,
		before anything else about the connecting client is known, so a
		missing/malformed/half-written file must degrade to "push nothing"
		rather than break every new connection (same precedent as
		load_seen_keys' bare except)."""
		return self._read_release_file(ADDON_RELEASE_FILE)

	def get_addon_beta_release(self):
		"""Same as get_addon_release, but for ADDON_BETA_RELEASE_FILE (the
		rolling nightly build) - only ever consulted for a connection that
		opted into allow_beta_updates."""
		return self._read_release_file(ADDON_BETA_RELEASE_FILE)

	def _read_release_file(self, path):
		try:
			if not os.path.exists(path):
				return None, None
			with open(path, 'r') as f:
				data = json.load(f)
			version = data.get('version')
			url = data.get('url')
			if not version or not url:
				return None, None
			return version, url
		except Exception as e:
			log.err(f"Failed to read release info from {path}: {e}")
			return None, None

	def load_or_generate_admin_token(self):
		if os.path.exists(ADMIN_TOKEN_FILE):
			with open(ADMIN_TOKEN_FILE, 'r') as f:
				self.admin_token = f.read().strip()
		if not self.admin_token:
			self.admin_token = secrets.token_urlsafe(32)
			with open(ADMIN_TOKEN_FILE, 'w') as f:
				f.write(self.admin_token)
			log.msg(f"Generated new admin token in {ADMIN_TOKEN_FILE}")

	def check_admin_token(self, token):
		if not token or not self.admin_token: return False
		return secrets.compare_digest(token, self.admin_token)

	def remove_channel(self, channel):
		del self.channels[channel]

	def find_or_create_channel(self, name):
		log.msg(f"find_or_create_channel called for: {name}")
		if name not in self.seen_keys:
			log.msg(f"New key seen: {name}")
			self.seen_keys.add(name)
			self.save_seen_keys()

		if name in self.channels:
			channel = self.channels[name]
		else:
			channel = Channel(name, self)
			self.channels[name] = channel
		return channel
