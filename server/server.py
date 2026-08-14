from twisted.internet.protocol import Protocol, Factory, defer
from twisted.python.modules import getModule
from twisted.python import log
from twisted.internet import ssl, reactor, threads
from twisted.protocols.basic import LineReceiver
from twisted.internet.task import LoopingCall
from twisted.python import usage
from collections import OrderedDict
import json
import os
import sys
from OpenSSL import crypto
import io
import time
import random
import string
import secrets
import update_check

log.startLogging(sys.stdout)

# Versioned independently from the client add-on (see buildVars.py's
# addon_version) - the two are separate sub-projects sharing one repo and
# one wire protocol, not a single lockstep release train. This is the
# server's first formal version number; it was never versioned before,
# despite carrying substantial features (whitelist/quarantine, admin API,
# controller/observer model, add-on self-update push) - 1.0.0 marks that
# first tagged/released baseline, not a "rewrite" or a reset of history.
SERVER_VERSION = "1.2.0"

PING_INTERVAL = 300
INITIAL_TIMEOUT = 30
GENERATED_KEY_EXPIRATION_TIME = 60*60*24
QUARANTINE_MSG_INTERVAL = 5.0
CONTROL_FREE_MSG_INTERVAL = 30.0
# A held-down key produces a down+up `key` message pair per keystroke; without
# throttling, an observer typing while not in control would get one
# control_denied per edge - effectively continuous. At most one every 3s.
CONTROL_DENIED_THROTTLE = 3.0
# Virtual-key codes (Windows). Plain F10 from a master who isn't the controller is
# a take-over gesture; Alt+F10 from the controller releases control. Either way the
# gesture itself is swallowed, never relayed as a real keystroke - see
# Handler.lineReceived and Channel.toggle_controller. Plain F10 *from the controller*
# is relayed normally, so it stays available as an ordinary remote keystroke.
VK_F10 = 0x79
VK_ALT_CODES = frozenset((0x12, 0xA4, 0xA5))  # VK_MENU, VK_LMENU, VK_RMENU

# Master->slave messages exempt from the controller gate: automatic braille
# handshake/housekeeping (sent e.g. whenever a master connects or its braille
# display changes), not something the user does to "steer" the remote machine.
# Deliberately NOT exempt: key, braille_input, set_clipboard_text, send_SAS -
# every actual input/control message stays controller-only. Gating these too
# would fire on connect for every observer (not just when they act), stealing
# the control_denied throttle slot from the real "you're not in control" cue.
GATE_EXEMPT_MASTER_TYPES = frozenset(('set_display_size', 'set_braille_info'))

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

# How often (a base tick, not the actual check interval) the server looks at
# data/server_config.json to decide whether a scheduled self-update check is
# due (see update_check.is_check_due/get_configured_interval_hours). Ticking
# hourly and checking "is it due yet" internally - rather than restarting a
# LoopingCall whenever the configured interval changes - means the interval
# is adjustable via a plain file write, no server restart needed, same as
# ADDON_RELEASE_FILE above.
UPDATE_CHECK_TICK_INTERVAL = 3600.0


def _scheduled_update_check():
	"""Called every UPDATE_CHECK_TICK_INTERVAL by a LoopingCall in main().
	Only actually hits GitHub when update_check.is_check_due() says it's
	time (per the configured interval, default 24h - see
	update_check.DEFAULT_INTERVAL_HOURS). run_scheduled_checks() covers
	both the server's own self-update check and auto-detecting/applying
	the latest official client release to data/addon_release.json - one
	configured interval governs both. The real HTTP calls are blocking,
	so this must never run directly on the reactor thread - deferToThread
	hands it to Twisted's thread pool instead, so a slow/hung GitHub
	request can never stall relaying for connected clients.
	"""
	if update_check.is_check_due(DATA_DIR):
		threads.deferToThread(update_check.run_scheduled_checks, SERVER_VERSION, DATA_DIR, log.msg)


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
		log.msg(f"Channel created for key: {self.key}")
		self.check_authorization()

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

class Handler(LineReceiver):
	delimiter = b'\n'
	connection_id = 0
	MAX_LENGTH = 20*1048576

	def __init__(self):
		self.connection_id = Handler.connection_id + 1
		Handler.connection_id += 1
		self.protocol_version = 1

	def connectionMade(self):
		log.msg("Connection %d from %s" % (self.connection_id, self.transport.getPeer()))
		self.transport.setTcpNoDelay(True)
		self.bytes_sent = 0
		self.bytes_received = 0
		self.user = User(protocol=self)
		self.cleanup_timer = self.user.server_state.clock.callLater(INITIAL_TIMEOUT, self.cleanup)
		self.user.send_motd()
		self.user.send_addon_update()

	def connectionLost(self, reason):
		log.msg("Connection %d lost, bytes sent: %d received: %d" % (self.connection_id, self.bytes_sent, self.bytes_received))
		self.user.connection_lost()
		if self.cleanup_timer is not None and not self.cleanup_timer.cancelled:
			self.cleanup_timer.cancel()

	def lineReceived(self, line):
		self.bytes_received += len(line)
		try:
			parsed = json.loads(line)
			if not isinstance(parsed, dict):
				raise ValueError
		except ValueError:
			log.msg("Unable to parse %r" % line)
			self.transport.loseConnection()
			return
		if 'type' not in parsed:
			log.msg("Invalid object received: %r" % parsed)
			return
		parsed.pop('origin', None)
		
		if parsed['type'].startswith('admin_'):
			self.handle_admin_command(parsed)
			return
		if parsed['type'] == 'auth_admin':
			self.do_auth_admin(parsed)
			return

		if parsed['type'] in ('ping', 'pong'):
			method_name = "do_" + parsed['type']
			if hasattr(self, method_name):
				getattr(self, method_name)(parsed)
			return

		if self.user.channel is not None:
			channel = self.user.channel
			msg_type = parsed['type']
			# list_sessions and get_server_info work while already joined (the
			# normal case, since every client auto-joins its own channel as a
			# slave on connect) - handle them here rather than falling
			# through to the generic relay below.
			if msg_type == 'list_sessions':
				self.do_list_sessions(parsed)
				return
			if msg_type == 'get_server_info':
				self.do_get_server_info(parsed)
				return
			is_master = self.user.connection_type == 'master'
			if is_master and msg_type == 'key' and self.handle_control_gesture(channel, parsed):
				return
			if (
				is_master
				and msg_type not in GATE_EXEMPT_MASTER_TYPES
				and channel.controller is not self.user
			):
				self.user.send_control_denied()
				return
			channel.send_to_clients(parsed, exclude=self.user, origin=self.user.user_id)
			return
		elif not hasattr(self, "do_"+parsed['type']):
			log.msg("No function for type %s" % parsed['type'])
			return
		getattr(self, "do_"+parsed['type'])(parsed)

	def handle_control_gesture(self, channel, parsed):
		"""Intercepts the F10 take-over / Alt+F10 release gesture out of a
		master's `key` stream before it reaches the normal relay/gating below.

		Plain F10 from a master who isn't the controller takes control (or is
		denied if someone else already has it); Alt+F10 from the controller
		releases it. Either way the gesture is swallowed - both the down and
		the matching up edge - so it never reaches the controlled machine as a
		real keystroke. Plain F10 from the controller (no Alt held) is left
		alone and falls through to a normal relay, so it stays available as an
		ordinary remote keystroke.

		Returns True if this event was fully handled and must not be relayed
		or gated normally, False if normal processing should continue.
		"""
		user = self.user
		vk_code = parsed.get('vk_code')
		pressed = parsed.get('pressed', True)

		if vk_code in VK_ALT_CODES:
			if pressed:
				user.held_alt_msg = dict(parsed)
			elif user.suppress_alt_up:
				# The tail end of an Alt+F10 release: we already sent a
				# synthetic Alt-up proactively, don't relay/deny this one too.
				user.suppress_alt_up = False
				return True
			else:
				user.held_alt_msg = None
			return False

		if vk_code != VK_F10:
			return False

		if not pressed:
			if user.suppress_f10_up:
				user.suppress_f10_up = False
				return True
			return False  # up-edge of a real F10 keystroke - relay normally

		is_controller = channel.controller is user
		if is_controller and not user.held_alt_msg:
			return False  # plain F10 from the controller - a real keystroke

		user.suppress_f10_up = True
		if is_controller:
			# Alt+F10: release control. Send a synthetic matching Alt key-up so
			# the controlled machine never sees a stuck modifier key - its real
			# Alt-down was already relayed before we knew F10 would follow.
			synth_up = dict(user.held_alt_msg, pressed=False)
			channel.send_to_clients(synth_up, exclude=user, origin=user.user_id)
			user.held_alt_msg = None
			user.suppress_alt_up = True
		else:
			# Taking over from observer status: any Alt we were tracking was
			# held while our own key events were being denied, so the slave
			# never actually saw an Alt-down - discard it rather than let it
			# leak into our new controller role (which could otherwise send a
			# bogus synthetic Alt-up, or misfire on our next Alt+F10 release,
			# using a message that was never really relayed).
			user.held_alt_msg = None
		channel.toggle_controller(user)
		return True

	def do_ping(self, obj):
		self.send(type='pong')

	def do_pong(self, obj):
		pass

	def do_get_server_info(self, obj):
		# v1.1.0: request/response, not an unconditional push at
		# connectionMade - a client too old to know the 'server_info' type
		# would hit RemoteMessageType(obj["type"]) raising ValueError in its
		# own transport.py's parse(), logged as log.error, which NVDA turns
		# into an audible error.wav on every single connect/reconnect (see
		# client/CLAUDE.md's error.wav feedback-loop lesson - same root
		# cause, different trigger). Only a client that already knows the
		# type would ever send this request, so no old client is affected;
		# see get_server_info's docstring in protocol.py.
		self.user.send_server_info()

	def do_list_sessions(self, obj):
		"""Non-admin session discovery: any client already joined to its own
		authorized channel may ask which *other* authorized sessions are online
		and controllable (i.e. currently have a slave connected), to populate
		'Control another computer'. Unlike admin_list_channels this deliberately
		does not require admin auth, and only shows online+authorized+controllable
		sessions - no whitelist management info, no offline/unauthorized keys."""
		own_channel = self.user.channel
		if own_channel is None or not own_channel.is_authorized:
			self.send(type='error', error='not_authorized')
			return
		state = self.user.server_state
		sessions = []
		for key, channel in state.channels.items():
			if key == own_channel.key or not channel.is_authorized:
				continue
			if not any(c.connection_type == 'slave' for c in channel.clients.values()):
				continue
			sessions.append({
				'key': key,
				'client_count': len(channel.clients),
				'has_controller': channel.controller is not None,
			})
		self.send(type='session_list', sessions=sessions)

	def handle_admin_command(self, parsed):
		if not self.user.is_admin:
			self.send(type='error', error='access_denied')
			return
		method_name = "do_" + parsed['type']
		if hasattr(self, method_name):
			getattr(self, method_name)(parsed)
		else:
			self.send(type='error', error='unknown_admin_command')

	def do_auth_admin(self, obj):
		token = obj.get('token')
		if self.user.server_state.check_admin_token(token):
			self.user.is_admin = True
			self.send(type='auth_admin_response', success=True)
			log.msg(f"Admin authenticated: Connection {self.connection_id}")
			self.do_admin_list_channels({})
		else:
			self.send(type='auth_admin_response', success=False)
			log.msg(f"Failed admin auth attempt: Connection {self.connection_id}")

	def do_admin_check_for_updates(self, obj):
		"""Admin-only, v1.1.0: lets an admin trigger update_check's GitHub
		checks (both halves - server self-check and client-release
		auto-detect/apply) right now from the admin GUI, instead of only
		via the CLI (check_server_update.py) or waiting for the daily
		scheduled check. Deliberately bypasses is_check_due() - an
		explicit "check now" click must never be silently skipped because
		the timer isn't due yet, same reasoning as the CLI script.

		Blocking HTTP calls happen in update_check.run_scheduled_checks,
		so this is routed through threads.deferToThread like the
		scheduled check - a slow/hung GitHub request must not stall
		relaying for every connected client just because one admin asked
		for a check.
		"""
		log.msg(f"Admin-triggered update check requested: Connection {self.connection_id}")
		d = threads.deferToThread(update_check.run_scheduled_checks, SERVER_VERSION, DATA_DIR, log.msg)
		d.addCallback(self._send_update_check_response)
		d.addErrback(lambda failure: self._send_update_check_response(
			{'server': {'error': str(failure.value)}, 'client': {'error': str(failure.value)}}
		))

	def _send_update_check_response(self, results):
		try:
			self.send(
				type='admin_update_check_response',
				server=results.get('server'),
				client=results.get('client'),
			)
		except Exception:
			# The admin's connection may have dropped while the check (a
			# real network round-trip) was still running - nothing to do,
			# there's no one left to tell.
			pass

	def do_admin_list_channels(self, obj):
		channels_info = []
		state = self.user.server_state
		all_keys = set(state.authorized_keys) | set(state.channels.keys()) | set(state.seen_keys)
		for key in all_keys:
			is_online = key in state.channels
			is_authorized = state.is_key_authorized(key)
			client_count = len(state.channels[key].clients) if is_online else 0
			# Self-reported by each client's 'join' message - None for
			# clients too old to send it (pre-3.2.2), not an error.
			versions = (
				[c.client_version for c in state.channels[key].clients.values()]
				if is_online else []
			)
			channels_info.append({
				'key': key,
				'client_count': client_count,
				'authorized': is_authorized,
				'online': bool(is_online),
				'versions': versions,
			})
		log.msg(f"Sending list: {channels_info}")
		self.send(type='admin_channel_list', channels=channels_info)

	def do_admin_approve_channel(self, obj):
		key = obj.get('key')
		if key:
			self.user.server_state.authorize_key(key)
			self.send(type='admin_response', command='approve', success=True, key=key)
			if key in self.user.server_state.channels:
				self.user.server_state.channels[key].check_authorization()

	def do_admin_remove_channel(self, obj):
		key = obj.get('key')
		if key:
			self.user.server_state.deauthorize_key(key)
			self.send(type='admin_response', command='remove', success=True, key=key)
			if key in self.user.server_state.channels:
				self.user.server_state.channels[key].check_authorization()

	def do_join(self, obj):
		log.msg(f"Connection {self.connection_id} requested join to channel: {obj.get('channel')}")
		if 'channel' not in obj or not obj['channel']:
			self.send(type='error', error='invalid_parameters')
			return
		self.user.join(
			obj['channel'],
			connection_type=obj.get('connection_type'),
			client_version=obj.get('client_version'),
			allow_beta_updates=obj.get('allow_beta_updates', False),
		)
		if self.user.allow_beta_updates:
			# The connectionMade-time send_addon_update() ran before join,
			# when allow_beta_updates wasn't known yet, so it could only
			# have pushed the stable channel - re-push now that it's known,
			# this time picking the beta channel. See send_addon_update's
			# docstring.
			self.user.send_addon_update()
		self.cleanup_timer.cancel()

	def do_protocol_version(self, obj):
		if 'version' not in obj:
			return
		self.protocol_version = obj['version']

	def do_generate_key(self, obj):
		self.user.generate_key()

	def send(self, **msg):
		origin = msg.pop('origin', None)
		if self.protocol_version > 1 and origin:
			msg['origin'] = origin
		obj = json.dumps(msg).encode('ascii')
		self.bytes_sent += len(obj)
		self.sendLine(obj)

	def cleanup(self):
		log.msg("Connection %d timed out" % self.connection_id)
		self.transport.abortConnection()
		self.cleanup_timer = None

class User(object):
	user_id = 0
	def __init__(self, protocol):
		self.protocol = protocol
		self.channel = None
		self.server_state = self.protocol.factory.server_state
		self.connection_type = None
		# Self-reported by the client in its 'join' message (client_version,
		# optional - older clients don't send it). Admin-visibility only, see
		# do_admin_list_channels - never relayed to other clients via
		# as_dict()/channel_joined/client_joined, since those are consumed by
		# real remote-control peers (not just the admin GUI) and this session
		# already established that new fields on existing peer-facing
		# messages risk breaking already-deployed clients with fixed
		# handlers.
		self.client_version = None
		# Self-reported by the client in its 'join' message (optional bool,
		# off by default - older clients don't send it, which is the same
		# as sending False). Set from settings_panel.py's "Allow beta
		# updates" checkbox - True means send_addon_update pushes the
		# rolling nightly build to this connection instead of the stable
		# one. See ADDON_BETA_RELEASE_FILE below.
		self.allow_beta_updates = False
		self.user_id = User.user_id + 1
		User.user_id += 1
		self.is_admin = False
		# Per-connection state for the F10/Alt+F10 control gesture (see
		# Handler.handle_control_gesture) - key events carry one key each, no
		# modifier field, so Alt has to be tracked across messages.
		self.held_alt_msg = None  # the last Alt keydown `key` message, or None if up
		self.suppress_f10_up = False
		self.suppress_alt_up = False
		self.last_control_denied_at = None

	def as_dict(self):
		return dict(id=self.user_id, connection_type=self.connection_type)

	def send_control_denied(self):
		"""Like send(type='control_denied'), but throttled: an observer holding
		a key down or typing generates a down+up `key` message per keystroke,
		every one of which is denied - without throttling this would be sent
		continuously instead of as a one-off "you're not in control" cue."""
		now = self.server_state.clock.seconds()
		last = self.last_control_denied_at
		if last is not None and (now - last) < CONTROL_DENIED_THROTTLE:
			return
		self.last_control_denied_at = now
		self.send(type='control_denied')

	def generate_key(self):
		ip = self.protocol.transport.getPeer().host
		if ip in self.server_state.generated_ips and time.time()-self.server_state.generated_ips[ip] < 1:
			self.send(type="error", message="too many keys")
			self.protocol.transport.loseConnection()
			return
		key = "".join([random.choice(string.digits) for i in range(7)])
		while key in self.server_state.generated_keys or key in self.server_state.channels.keys():
			key = "".join([random.choice(string.digits) for i in range(7)])
		self.server_state.generated_keys.add(key)
		self.server_state.generated_ips[ip] = time.time()
		reactor.callLater(GENERATED_KEY_EXPIRATION_TIME, lambda: self.server_state.generated_keys.remove(key))
		if key:
			self.send(type="generate_key", key=key)
		return key

	def connection_lost(self):
		if self.channel is not None:
			self.channel.remove_connection(self)

	def join(self, channel, connection_type, client_version=None, allow_beta_updates=False):
		if self.channel:
			self.send(type="error", error="already_joined")
			return
		self.connection_type = connection_type
		self.client_version = client_version
		self.allow_beta_updates = bool(allow_beta_updates)
		self.channel = self.server_state.find_or_create_channel(channel)
		self.channel.add_client(self)

	def send(self, **obj):
		self.protocol.send(**obj)

	def send_motd(self):
		if self.server_state.motd is not None:
			self.send(type='motd', motd=self.server_state.motd)

	def send_addon_update(self):
		# Sent to every connection unconditionally, same as send_motd (before
		# join/authorization) - a quarantined/unauthorized client is exactly
		# the one that can't be reached any other way to tell it to update.
		# One consequence: the addon_release.json url is reachable by any
		# host that can open a TCP connection to the relay, not just
		# authorized ones - treat its contents as effectively public.
		#
		# Called twice for a beta-opted-in client: once from
		# Handler.connectionMade (before join, so allow_beta_updates isn't
		# known yet - always picks the stable channel here), and again from
		# Handler.do_join once allow_beta_updates is actually known, this
		# time picking the beta channel if opted in. Harmless - client-side
		# gating (addon_update.py's last_handled_version) already tolerates
		# a repeated push of the same or an older version.
		if self.allow_beta_updates:
			version, url = self.server_state.get_addon_beta_release()
			if not (version and url):
				# No nightly build available yet - fall back to stable
				# rather than push nothing to an opted-in client.
				version, url = self.server_state.get_addon_release()
		else:
			version, url = self.server_state.get_addon_release()
		if version and url:
			self.send(type='addon_update', version=version, url=url)

	def send_server_info(self):
		# Sent in response to a client's get_server_info request only (v1.1.0)
		# - deliberately NOT unconditional at connectionMade like send_motd/
		# send_addon_update: an old client that doesn't know the 'server_info'
		# type would log.error on receiving it (RemoteMessageType(...) raises
		# ValueError), which NVDA turns into an audible error.wav on every
		# connect/reconnect. Request/response means only a client that
		# already knows the type (because it's new enough to send the
		# request) ever receives a reply - version is not sensitive, so any
		# connected user (not just admins) may ask, no gating needed.
		# Includes the last known self-update-check result (None if the
		# server hasn't completed one yet) so a client can also show
		# whether a newer server release exists, without a separate
		# admin-gated round trip.
		self.send(
			type='server_info',
			version=SERVER_VERSION,
			update_check=update_check.read_last_check(DATA_DIR),
		)

class RemoteServerFactory(Factory):
	def __init__(self, server_state):
		self.server_state = server_state
	def ping_connected_clients(self):
		for channel in self.server_state.channels.values():
			channel.ping_clients()

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

class Options(usage.Options):
	optParameters = [
		["certificate", "c", "cert", "SSL certificate"],
		["privkey", "k", "privkey", "SSL private key"],
		["chain", "C", "chain", "SSL chain"],
		["motd", "m", "motd", "MOTD"],
		["network-interface", "i", "::", "Interface to listen on"],
		["port", "p", "6837", "Server port"],
	]

def main():
	log.msg(f"NVDA Remote relay server {SERVER_VERSION} starting")
	config = Options()
	config.parseOptions()
	privkey = open(config['privkey']).read()
	certData = open(config['certificate']).read()
	chain = open(config['chain']).read()
	privkey = crypto.load_privatekey(crypto.FILETYPE_PEM, privkey)
	certificate = crypto.load_certificate(crypto.FILETYPE_PEM, certData)
	chain = crypto.load_certificate(crypto.FILETYPE_PEM, chain)
	context_factory = ssl.CertificateOptions(privateKey=privkey, certificate=certificate, extraCertChain=[chain])
	state = ServerState()
	if config['motd'] and os.path.exists(config['motd']):
		with io.open(config['motd'], encoding='utf-8') as fp:
			state.motd = fp.read().strip()
	else:
		state.motd = None
	f = RemoteServerFactory(state)
	l = LoopingCall(f.ping_connected_clients)
	l.start(PING_INTERVAL)
	# now=True so a fresh deployment (no data/server_update_check.json yet)
	# checks once on startup rather than waiting a full tick; on a restart
	# with a recent check already on record, is_check_due() makes this a
	# no-op until the configured interval has actually elapsed.
	update_check_loop = LoopingCall(_scheduled_update_check)
	update_check_loop.start(UPDATE_CHECK_TICK_INTERVAL, now=True)
	f.protocol = Handler
	reactor.listenSSL(int(config['port']), f, context_factory, interface=config['network-interface'])
	reactor.run()
	return defer.Deferred()

if __name__ == '__main__':
	res = main()
