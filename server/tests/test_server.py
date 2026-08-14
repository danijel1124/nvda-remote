"""Tests for the relay server (server.py).

Run with: trial tests   (twisted.trial ships with Twisted, no extra dependency)
or:        python -m twisted.trial tests

Each test gets an isolated CWD (so ServerState's data/*.json files don't touch
the real ./data) and a fake twisted.internet.task.Clock, so quarantine/control-
free reminder loops can be advanced deterministically without real waiting.
"""
import json
import os
from unittest import mock

from twisted.internet import defer
from twisted.internet.task import Clock
from twisted.test.proto_helpers import StringTransportWithDisconnection
from twisted.trial import unittest

import server as server_module
from server import (
	CONTROL_DENIED_THROTTLE,
	CONTROL_FREE_MSG_INTERVAL,
	QUARANTINE_MSG_INTERVAL,
	VK_F10,
	RemoteServerFactory,
	ServerState,
)

VK_LMENU = 0xA4


class FakeTransport(StringTransportWithDisconnection):
	"""StringTransportWithDisconnection plus the couple of real-socket calls
	Handler.connectionMade makes that the base fake doesn't implement."""

	def setTcpNoDelay(self, enabled):
		pass


def _read(transport):
	"""Pop and JSON-decode every complete line currently buffered, clearing it."""
	data = transport.value()
	transport.clear()
	if not data:
		return []
	return [json.loads(line) for line in data.split(b'\n') if line]


def _types(messages):
	return [m['type'] for m in messages]


class RelayServerTestCase(unittest.TestCase):
	def setUp(self):
		tmpdir = self.mktemp()
		os.makedirs(tmpdir)
		self._origcwd = os.getcwd()
		os.chdir(tmpdir)
		self.clock = Clock()
		self.state = ServerState(clock=self.clock)
		self.factory = RemoteServerFactory(self.state)
		self.factory.protocol = server_module.Handler

	def tearDown(self):
		os.chdir(self._origcwd)

	# --- helpers ---

	def connect(self):
		protocol = self.factory.buildProtocol(('127.0.0.1', 0))
		transport = FakeTransport()
		transport.protocol = protocol
		protocol.makeConnection(transport)
		_read(transport)  # discard motd
		return protocol, transport

	def send(self, protocol, **kwargs):
		protocol.dataReceived((json.dumps(kwargs) + '\n').encode('ascii'))

	def join(self, key, mode, authorize=True):
		if authorize:
			self.state.authorize_key(key)
		protocol, transport = self.connect()
		self.send(protocol, type='protocol_version', version=2)
		self.send(protocol, type='join', channel=key, connection_type=mode)
		_read(transport)  # discard channel_joined (+ control_changed if master)
		return protocol, transport

	def press_key(self, protocol, vk_code, pressed=True):
		self.send(protocol, type='key', vk_code=vk_code, scan_code=1, extended=False, pressed=pressed)

	def tap_f10(self, protocol):
		self.press_key(protocol, VK_F10, pressed=True)
		self.press_key(protocol, VK_F10, pressed=False)

	# --- whitelist / quarantine (regression) ---

	def test_unauthorized_channel_does_not_relay(self):
		p1, t1 = self.join('pcA', 'slave', authorize=False)
		p2, t2 = self.join('pcA', 'master', authorize=False)
		_read(t1)
		self.send(p2, type='speak', sequence=['hi'])
		self.assertEqual(_read(t1), [])

	def test_quarantine_message_sent_periodically(self):
		p1, t1 = self.join('pcA', 'slave', authorize=False)
		_read(t1)
		self.clock.advance(QUARANTINE_MSG_INTERVAL)
		msgs = _read(t1)
		self.assertEqual(len(msgs), 1)
		self.assertEqual(msgs[0]['type'], 'speak')

	def test_authorizing_unblocks_relay_and_stops_quarantine(self):
		p1, t1 = self.join('pcA', 'slave', authorize=False)
		p2, t2 = self.join('pcA', 'master', authorize=False)
		self.state.authorize_key('pcA')
		self.user_channel_of(p1).check_authorization()
		_read(t1)
		self.press_key(p2, 65)  # plain 'A', not F10 - controller (auto-assigned) speaks
		msgs = _read(t1)
		self.assertEqual(_types(msgs), ['key'])
		# quarantine loop must not fire anymore
		self.clock.advance(QUARANTINE_MSG_INTERVAL * 2)
		self.assertEqual(_read(t1), [])

	def user_channel_of(self, protocol):
		return protocol.user.channel

	# --- backward compatibility: sole master is auto-controller ---

	def test_single_master_relay_is_unaffected_by_controller_logic(self):
		slave, ts = self.join('pcA', 'slave')
		master, tm = self.join('pcA', 'master')
		_read(ts)
		self.press_key(master, 65)
		msgs = _read(ts)
		self.assertEqual(_types(msgs), ['key'])
		self.assertEqual(msgs[0]['vk_code'], 65)

	def test_channel_joined_never_carries_a_controller_field(self):
		# channel_joined is a message type every already-deployed client
		# recognizes with a fixed (no **kwargs) handler - an unexpected extra
		# field there risks breaking that handler on every client we can't
		# update. Controller info must only ever travel via control_changed,
		# a message type old clients don't know and safely ignore.
		self.state.authorize_key('pcA')
		slave, ts = self.connect()
		self.send(slave, type='protocol_version', version=2)
		self.send(slave, type='join', channel='pcA', connection_type='slave')
		joined = _read(ts)[0]
		self.assertNotIn('controller', joined)

		master, tm = self.connect()
		self.send(master, type='protocol_version', version=2)
		self.send(master, type='join', channel='pcA', connection_type='master')
		msgs = _read(tm)
		self.assertNotIn('controller', msgs[0])
		self.assertEqual(msgs[0]['type'], 'channel_joined')
		# the sole master still learns it became controller, just via a
		# separate control_changed broadcast.
		self.assertEqual(
			[m for m in msgs if m['type'] == 'control_changed'],
			[{'type': 'control_changed', 'controller': master.user.user_id}],
		)

	def test_joining_observer_is_told_who_already_controls(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')  # becomes controller

		master2, tm2 = self.connect()
		self.send(master2, type='protocol_version', version=2)
		self.send(master2, type='join', channel='pcA', connection_type='master')
		msgs = _read(tm2)
		changed = [m for m in msgs if m['type'] == 'control_changed']
		self.assertEqual(changed, [{'type': 'control_changed', 'controller': master1.user.user_id}])

	# --- list_sessions ---

	def test_list_sessions_requires_being_joined_and_authorized(self):
		p, t = self.connect()
		self.send(p, type='list_sessions')
		msgs = _read(t)
		self.assertEqual(msgs, [{'type': 'error', 'error': 'not_authorized'}])

	def test_list_sessions_excludes_self_unauthorized_and_slaveless_channels(self):
		self.state.authorize_key('pcA')
		self.state.authorize_key('pcB')
		self.state.authorize_key('pcC')
		self.state.authorize_key('pcD')
		me, tme = self.join('pcA', 'slave')
		other_slave, _ = self.join('pcB', 'slave')
		# pcC is authorized but has no slave connected (master-only) - excluded
		self.join('pcC', 'master')
		# pcD is never joined at all - naturally excluded (not online)
		_read(tme)
		self.send(me, type='list_sessions')
		msgs = _read(tme)
		self.assertEqual(len(msgs), 1)
		sessions = msgs[0]['sessions']
		self.assertEqual([s['key'] for s in sessions], ['pcB'])
		self.assertEqual(sessions[0]['client_count'], 1)
		self.assertFalse(sessions[0]['has_controller'])

	def test_list_sessions_reports_has_controller(self):
		self.state.authorize_key('pcA')
		self.state.authorize_key('pcB')
		me, tme = self.join('pcA', 'slave')
		self.join('pcB', 'slave')
		self.join('pcB', 'master')  # auto-becomes controller
		_read(tme)
		self.send(me, type='list_sessions')
		sessions = _read(tme)[0]['sessions']
		self.assertTrue(sessions[0]['has_controller'])

	# --- admin listing stays admin-only (regression) ---

	def test_admin_list_channels_still_requires_admin_auth(self):
		p, t = self.join('pcA', 'slave')
		self.send(p, type='admin_list_channels')
		self.assertEqual(_read(t), [{'type': 'error', 'error': 'access_denied'}])

	def test_admin_auth_and_list(self):
		self.state.authorize_key('pcA')
		p, t = self.join('pcA', 'slave')
		self.send(p, type='auth_admin', token=self.state.admin_token)
		msgs = _read(t)
		self.assertEqual(msgs[0], {'type': 'auth_admin_response', 'success': True})
		self.assertEqual(msgs[1]['type'], 'admin_channel_list')
		keys = {c['key']: c for c in msgs[1]['channels']}
		self.assertTrue(keys['pcA']['online'])
		self.assertTrue(keys['pcA']['authorized'])

	# --- client_version self-report (admin visibility) ---

	def test_admin_list_reports_self_reported_client_version(self):
		self.state.authorize_key('pcA')
		p, t = self.connect()
		self.send(p, type='protocol_version', version=2)
		self.send(p, type='join', channel='pcA', connection_type='slave', client_version='3.2.1')
		_read(t)
		self.send(p, type='auth_admin', token=self.state.admin_token)
		msgs = _read(t)
		channels = {c['key']: c for c in msgs[1]['channels']}
		self.assertEqual(channels['pcA']['versions'], ['3.2.1'])

	def test_admin_list_shows_none_for_client_too_old_to_report_version(self):
		# join() without client_version - exactly what a pre-3.2.2 client sends.
		p, t = self.join('pcA', 'slave')
		self.send(p, type='auth_admin', token=self.state.admin_token)
		msgs = _read(t)
		channels = {c['key']: c for c in msgs[1]['channels']}
		self.assertEqual(channels['pcA']['versions'], [None])

	def test_admin_list_reports_one_version_per_connected_client(self):
		self.state.authorize_key('pcA')
		slave, ts = self.connect()
		self.send(slave, type='protocol_version', version=2)
		self.send(slave, type='join', channel='pcA', connection_type='slave', client_version='3.2.1')
		master, tm = self.connect()
		self.send(master, type='protocol_version', version=2)
		self.send(master, type='join', channel='pcA', connection_type='master', client_version='3.2.2')
		_read(ts)
		self.send(slave, type='auth_admin', token=self.state.admin_token)
		msgs = _read(ts)
		channels = {c['key']: c for c in msgs[1]['channels']}
		self.assertEqual(sorted(channels['pcA']['versions']), ['3.2.1', '3.2.2'])

	def test_client_version_is_never_relayed_to_other_clients(self):
		# It's admin-visibility only (do_admin_list_channels) - must not leak
		# into channel_joined/client_joined, which real peer clients (not
		# just the admin GUI) parse.
		self.state.authorize_key('pcA')
		slave, ts = self.join('pcA', 'slave')
		master, tm = self.connect()
		self.send(master, type='protocol_version', version=2)
		self.send(master, type='join', channel='pcA', connection_type='master', client_version='3.2.1')
		joined = [m for m in _read(ts) if m['type'] == 'client_joined'][0]
		self.assertNotIn('client_version', joined)
		self.assertNotIn('client_version', joined.get('client', {}))

	# --- controller gating: observers can't send input ---

	def test_second_master_is_denied_input_first_master_still_works(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(ts)
		_read(tm2)

		self.press_key(master2, 65)
		self.assertEqual(_read(ts), [])  # not relayed to slave
		self.assertEqual(_types(_read(tm2)), ['control_denied'])

		self.press_key(master1, 66)
		msgs = _read(ts)
		self.assertEqual(_types(msgs), ['key'])
		self.assertEqual(msgs[0]['vk_code'], 66)

	def test_braille_handshake_is_exempt_from_the_controller_gate(self):
		# set_display_size/set_braille_info are automatic housekeeping a master
		# sends whenever it connects or its display changes - not something the
		# user did to "steer" the remote. An observer sending them must not be
		# denied (and, unlike a real denial, must not eat the throttle slot
		# that a genuine "you're not in control" cue for actual input needs).
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(ts)
		_read(tm2)

		self.send(master2, type='set_braille_info', name='noBraille', numCells=0)
		self.send(master2, type='set_display_size', sizes=[0])
		self.assertEqual(_types(_read(ts)), ['set_braille_info', 'set_display_size'])
		self.assertEqual(_read(tm2), [])  # no control_denied for either

		# the throttle slot is still free for a real denial right after
		self.press_key(master2, 65)
		self.assertEqual(_types(_read(tm2)), ['control_denied'])

	def test_control_denied_is_throttled_not_sent_per_keystroke(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(tm2)

		# a held-down key, or fast typing, produces many down+up edges - all
		# denied, but the user must only be told about it once, not per edge.
		for vk in (65, 66, 67, 68):
			self.press_key(master2, vk, pressed=True)
			self.press_key(master2, vk, pressed=False)
		self.assertEqual(_types(_read(tm2)), ['control_denied'])

		# still within the throttle window: silence
		self.clock.advance(CONTROL_DENIED_THROTTLE - 1)
		self.press_key(master2, 65)
		self.assertEqual(_read(tm2), [])

		# throttle window elapsed: told again
		self.clock.advance(2)
		self.press_key(master2, 65)
		self.assertEqual(_types(_read(tm2)), ['control_denied'])

	# --- observer payoff: output is already broadcast to everyone ---

	def test_slave_output_reaches_both_controller_and_observers(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(tm1)
		_read(tm2)

		self.send(slave, type='speak', sequence=['hello'])
		self.assertEqual(_types(_read(tm1)), ['speak'])
		self.assertEqual(_types(_read(tm2)), ['speak'])

	def test_control_changed_is_not_sent_to_slaves(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(ts)

		self.tap_f10(master2)  # denied, but exercises the broadcast path
		master1.connectionLost(reason=None)  # controller leaves -> broadcast
		self.assertEqual([m for m in _read(ts) if m['type'] == 'control_changed'], [])

	# --- F10 take-over / Alt+F10 release ---

	def test_f10_takeover_when_free(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(ts)
		_read(tm1)
		_read(tm2)

		# controller releases via Alt+F10
		self.press_key(master1, VK_LMENU, pressed=True)
		_read(ts)  # relayed Alt-down, not relevant here
		self.tap_f10(master1)
		changed = [m for m in _read(tm2) if m['type'] == 'control_changed']
		self.assertEqual(changed, [{'type': 'control_changed', 'controller': None}])

		# now master2 (a plain listener) takes over with plain F10
		self.tap_f10(master2)
		changed = [m for m in _read(tm2) if m['type'] == 'control_changed']
		self.assertEqual(changed, [{'type': 'control_changed', 'controller': master2.user.user_id}])

		# F10 itself must never reach the slave as a literal keystroke
		slave_msgs = _read(ts)
		self.assertNotIn('key', [m.get('type') for m in slave_msgs if m.get('vk_code') == VK_F10])

		# master2 can now actually control
		self.press_key(master2, 67)
		msgs = [m for m in _read(ts) if m['type'] == 'key' and m.get('vk_code') == 67]
		self.assertEqual(len(msgs), 1)

	def test_f10_release_synthesizes_matching_alt_up(self):
		slave, ts = self.join('pcA', 'slave')
		master, tm = self.join('pcA', 'master')
		_read(ts)

		self.press_key(master, VK_LMENU, pressed=True)
		alt_down = _read(ts)
		self.assertEqual(_types(alt_down), ['key'])
		self.assertTrue(alt_down[0]['pressed'])

		self.tap_f10(master)
		relayed = _read(ts)
		# The controller relinquished control: server must have synthesized a
		# matching Alt key-up so the remote machine's Alt key isn't left stuck.
		alt_ups = [m for m in relayed if m['type'] == 'key' and m['vk_code'] == VK_LMENU and not m['pressed']]
		self.assertEqual(len(alt_ups), 1)
		# The F10 itself was never relayed.
		self.assertEqual([m for m in relayed if m.get('vk_code') == VK_F10], [])

		_read(tm)  # discard the control_changed broadcast from the release itself

		# the real, physical Alt key-up that follows must not be relayed again
		# (already synthesized) nor trigger a control_denied for the ex-controller.
		self.press_key(master, VK_LMENU, pressed=False)
		self.assertEqual(_read(ts), [])
		self.assertEqual(_read(tm), [])

	def test_plain_f10_from_controller_relays_as_a_real_keystroke(self):
		slave, ts = self.join('pcA', 'slave')
		master, tm = self.join('pcA', 'master')
		_read(ts)
		self.tap_f10(master)  # no Alt held: this is just a normal keystroke
		msgs = _read(ts)
		self.assertEqual([m['vk_code'] for m in msgs], [VK_F10, VK_F10])
		self.assertEqual([m['pressed'] for m in msgs], [True, False])

	def test_f10_cannot_steal_control_from_another_controller(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(tm1)
		_read(tm2)

		self.tap_f10(master2)  # master1 is still controller
		self.assertEqual(_types(_read(tm2)), ['control_denied'])
		self.assertEqual(_read(tm1), [])  # no control_changed - nothing changed

		# master1 is still in control
		_read(ts)
		self.press_key(master1, 65)
		self.assertEqual(_types(_read(ts)), ['key'])

	def test_steal_denial_is_not_throttled_by_prior_input_denial(self):
		# A discrete F10 take-over attempt must always get its own feedback,
		# even if the input-denial throttle is currently "armed" from an
		# unrelated denied keystroke moments earlier - it fires once per
		# physical press, not continuously, so there's no flood to protect
		# against, and swallowing it would hide exactly the cue the user
		# wanted ("dass er grade nicht steuert").
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(tm2)

		self.press_key(master2, 65)  # arms the throttle
		self.assertEqual(_types(_read(tm2)), ['control_denied'])

		self.tap_f10(master2)  # immediate steal attempt, well within 3s
		self.assertEqual(_types(_read(tm2)), ['control_denied'])

	def test_takeover_discards_stale_observer_alt_state(self):
		# master2 (observer) holds Alt while master1 still controls, so the
		# Alt-down is denied and never reaches the slave - but it must not
		# leave stale "Alt held" bookkeeping behind once master2 later becomes
		# controller: that bookkeeping must not resurface as a misfired
		# release (or a phantom synthetic Alt-up) on their next plain F10.
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')

		self.press_key(master2, VK_LMENU, pressed=True)  # denied, never relayed
		_read(ts)
		_read(tm2)

		# master1 releases control via its own, real, unrelated Alt+F10.
		self.press_key(master1, VK_LMENU, pressed=True)
		self.tap_f10(master1)
		_read(ts)
		_read(tm2)

		# master2 takes over with plain F10 - must not consult the stale Alt
		# state above (which belongs to master2, not to this decision anyway).
		self.tap_f10(master2)
		changed = [m for m in _read(tm2) if m['type'] == 'control_changed']
		self.assertEqual(changed, [{'type': 'control_changed', 'controller': master2.user.user_id}])

		# Without discarding the stale state, this next plain F10 (no Alt
		# actually held right now) would be misread as "Alt is still held" and
		# incorrectly swallowed as a release, complete with a bogus synthetic
		# Alt-up sent to the slave. It must instead be a real keystroke.
		self.tap_f10(master2)
		msgs = _read(ts)
		self.assertEqual([m['vk_code'] for m in msgs], [VK_F10, VK_F10])
		self.assertEqual([m['pressed'] for m in msgs], [True, False])
		# control must still be with master2 - no accidental release happened.
		self.assertEqual([m for m in _read(tm2) if m['type'] == 'control_changed'], [])

	# --- controller disconnect / periodic "no controller" reminder ---

	def test_controller_disconnect_resets_controller_and_notifies(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(tm2)

		master1.connectionLost(reason=None)
		changed = [m for m in _read(tm2) if m['type'] == 'control_changed']
		self.assertEqual(changed, [{'type': 'control_changed', 'controller': None}])

	def test_control_free_reminder_repeats_every_30s_while_listener_present(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master2, tm2 = self.join('pcA', 'master')
		_read(tm2)

		master1.connectionLost(reason=None)
		_read(tm2)  # discard the immediate control_changed

		self.clock.advance(CONTROL_FREE_MSG_INTERVAL)
		self.assertEqual(_types(_read(tm2)), ['control_changed'])
		self.clock.advance(CONTROL_FREE_MSG_INTERVAL)
		self.assertEqual(_types(_read(tm2)), ['control_changed'])

		# once master2 takes over, the reminder must stop
		self.tap_f10(master2)
		_read(tm2)
		self.clock.advance(CONTROL_FREE_MSG_INTERVAL * 3)
		self.assertEqual(_read(tm2), [])

	def test_control_free_reminder_does_not_run_with_no_listeners(self):
		slave, ts = self.join('pcA', 'slave')
		master1, tm1 = self.join('pcA', 'master')
		master1.connectionLost(reason=None)
		_read(ts)
		self.clock.advance(CONTROL_FREE_MSG_INTERVAL * 2)
		self.assertEqual(_read(ts), [])

	# --- addon self-update push (addon_update) ---

	def _write_addon_release(self, data):
		os.makedirs('data', exist_ok=True)
		with open(os.path.join('data', 'addon_release.json'), 'w') as f:
			if isinstance(data, str):
				f.write(data)
			else:
				json.dump(data, f)

	def test_no_addon_update_sent_by_default(self):
		# connect() already discards whatever comes with the connection
		# (motd + addon_update, if any) - reconnect and check explicitly here
		# instead of relying on that discard being empty. server_info is
		# request/response only (v1.1.0, NOT unconditional like motd - see
		# get_server_info's docstring for why), so nothing extra here.
		protocol = self.factory.buildProtocol(('127.0.0.1', 0))
		transport = FakeTransport()
		transport.protocol = protocol
		protocol.makeConnection(transport)
		self.assertEqual(_types(_read(transport)), [])

	def test_addon_update_sent_with_release_file_present(self):
		self._write_addon_release({'version': '3.2', 'url': 'https://example.org/remote-3.2.nvda-addon'})
		protocol = self.factory.buildProtocol(('127.0.0.1', 0))
		transport = FakeTransport()
		transport.protocol = protocol
		protocol.makeConnection(transport)
		msgs = _read(transport)
		updates = [m for m in msgs if m['type'] == 'addon_update']
		self.assertEqual(len(updates), 1)
		self.assertEqual(updates[0]['version'], '3.2')
		self.assertEqual(updates[0]['url'], 'https://example.org/remote-3.2.nvda-addon')

	def test_addon_update_re_read_live_without_restart(self):
		"""get_addon_release() must not cache at startup - the whole point is
		announcing a new release without restarting the server."""
		protocol, transport = self.connect()  # no file yet - nothing sent
		self._write_addon_release({'version': '3.3', 'url': 'https://example.org/remote-3.3.nvda-addon'})
		protocol2 = self.factory.buildProtocol(('127.0.0.1', 0))
		transport2 = FakeTransport()
		transport2.protocol = protocol2
		protocol2.makeConnection(transport2)
		updates = [m for m in _read(transport2) if m['type'] == 'addon_update']
		self.assertEqual(len(updates), 1)
		self.assertEqual(updates[0]['version'], '3.3')

	def test_malformed_addon_release_file_does_not_crash_connection(self):
		self._write_addon_release("not valid json{{{")
		protocol = self.factory.buildProtocol(('127.0.0.1', 0))
		transport = FakeTransport()
		transport.protocol = protocol
		protocol.makeConnection(transport)  # must not raise
		self.assertEqual(_types(_read(transport)), [])
		# connection must still be otherwise usable
		self.send(protocol, type='protocol_version', version=2)
		self.assertFalse(transport.disconnecting)

	def test_addon_release_file_missing_fields_sends_nothing(self):
		self._write_addon_release({'version': '3.2'})  # no url
		protocol = self.factory.buildProtocol(('127.0.0.1', 0))
		transport = FakeTransport()
		transport.protocol = protocol
		protocol.makeConnection(transport)
		self.assertEqual(_types(_read(transport)), [])

	# --- server_info (v1.1.0, request/response, not an unconditional push -
	# see get_server_info's docstring: an old client that doesn't recognize
	# the type would log.error -> audible error.wav on every connect) ---

	def test_server_info_not_sent_unprompted_on_connect(self):
		"""Regression guard for the actual bug this design avoids: nothing
		with type server_info may ever arrive before the client asks for
		it, since a pre-3.2.3 client has no 'server_info' in its
		RemoteMessageType enum and would error.wav on it."""
		protocol, transport = self._connect_raw()
		self.assertEqual(_types(_read(transport)), [])

	def test_get_server_info_returns_current_version(self):
		p, t = self._connect_raw()
		_read(t)
		self.send(p, type='get_server_info')
		info = [m for m in _read(t) if m['type'] == 'server_info'][0]
		self.assertEqual(info['version'], server_module.SERVER_VERSION)

	def test_get_server_info_works_before_join(self):
		p, t = self._connect_raw()
		_read(t)
		self.send(p, type='get_server_info')  # no protocol_version/join sent first
		self.assertEqual(_types(_read(t)), ['server_info'])

	def test_get_server_info_works_after_join(self):
		p, t = self.join('pcA', 'slave')
		self.send(p, type='get_server_info')
		self.assertEqual(_types(_read(t)), ['server_info'])

	def test_server_info_update_check_is_none_by_default(self):
		p, t = self._connect_raw()
		_read(t)
		self.send(p, type='get_server_info')
		info = [m for m in _read(t) if m['type'] == 'server_info'][0]
		self.assertIsNone(info['update_check'])

	def test_server_info_includes_last_known_update_check(self):
		os.makedirs('data', exist_ok=True)
		with open(os.path.join('data', 'server_update_check.json'), 'w') as f:
			json.dump({'checked_at': 123.0, 'current_version': '1.0.0',
						'latest_version': '1.1.0', 'update_available': True,
						'url': 'https://example.org/server-v1.1.0', 'error': None}, f)
		p, t = self._connect_raw()
		_read(t)
		self.send(p, type='get_server_info')
		info = [m for m in _read(t) if m['type'] == 'server_info'][0]
		self.assertEqual(info['update_check']['latest_version'], '1.1.0')

	def _connect_raw(self):
		"""Like connect(), but without discarding the initial messages -
		needed to inspect what's sent right at connectionMade."""
		protocol = self.factory.buildProtocol(('127.0.0.1', 0))
		transport = FakeTransport()
		transport.protocol = protocol
		protocol.makeConnection(transport)
		return protocol, transport

	def test_server_info_not_folded_into_auth_admin_response(self):
		# Regression guard: this must stay a separate message type, not a
		# new field on auth_admin_response - see do_admin_check_for_updates'
		# docstring for why (fixed, non-**kwargs client handler signature).
		p, t = self.connect()
		self.send(p, type='auth_admin', token=self.state.admin_token)
		auth_response = [m for m in _read(t) if m['type'] == 'auth_admin_response'][0]
		self.assertNotIn('version', auth_response)

	# --- admin-triggered remote update check ---

	def test_admin_check_for_updates_requires_admin(self):
		p, t = self.connect()
		self.send(p, type='admin_check_for_updates')
		msgs = _read(t)
		self.assertEqual(msgs[0], {'type': 'error', 'error': 'access_denied'})

	def test_admin_check_for_updates_triggers_check_and_responds(self):
		p, t = self.connect()
		self.send(p, type='auth_admin', token=self.state.admin_token)
		_read(t)
		fake_result = {
			'server': {'current_version': '1.1.0', 'latest_version': '1.1.0', 'update_available': False, 'url': None, 'error': None},
			'client': {'latest_version': '3.2.2', 'updated': False, 'url': None, 'error': None},
		}
		with mock.patch('server.threads.deferToThread', side_effect=lambda f, *a, **kw: defer.succeed(f(*a, **kw))):
			with mock.patch('server.update_check.run_scheduled_checks', return_value=fake_result) as mock_run:
				self.send(p, type='admin_check_for_updates')
				msgs = _read(t)
		mock_run.assert_called_once()
		response = [m for m in msgs if m['type'] == 'admin_update_check_response'][0]
		self.assertEqual(response['server']['latest_version'], '1.1.0')
		self.assertEqual(response['client']['latest_version'], '3.2.2')

	def test_admin_check_for_updates_bypasses_due_gate(self):
		# An explicit "check now" must never be silently skipped because
		# the daily timer isn't due yet - unlike the scheduled path, this
		# doesn't consult is_check_due() at all.
		p, t = self.connect()
		self.send(p, type='auth_admin', token=self.state.admin_token)
		_read(t)
		with mock.patch('server.threads.deferToThread', side_effect=lambda f, *a, **kw: defer.succeed(f(*a, **kw))):
			with mock.patch('server.update_check.is_check_due') as mock_is_due:
				with mock.patch('server.update_check.run_scheduled_checks', return_value={'server': {}, 'client': {}}) as mock_run:
					self.send(p, type='admin_check_for_updates')
					_read(t)
		mock_run.assert_called_once()

	# --- opt-in beta (nightly) update channel ---

	def _write_addon_beta_release(self, data):
		os.makedirs('data', exist_ok=True)
		with open(os.path.join('data', 'addon_beta_release.json'), 'w') as f:
			json.dump(data, f)

	def test_stable_client_never_gets_the_nightly_build(self):
		self._write_addon_release({'version': '3.2.3.1', 'url': 'https://example.org/remote-3.2.3.1.nvda-addon'})
		self._write_addon_beta_release({'version': 'nightly-20260813203214', 'url': 'https://example.org/remote-nightly-20260813203214.nvda-addon'})
		self.state.authorize_key('pcA')
		p, t = self._connect_raw()
		self.send(p, type='protocol_version', version=2)
		self.send(p, type='join', channel='pcA', connection_type='slave')  # allow_beta_updates omitted, like a real 3.2.3.1 client
		updates = [m for m in _read(t) if m['type'] == 'addon_update']
		self.assertEqual(len(updates), 1)  # the connectionMade-time push only - join doesn't re-push for a non-beta client
		self.assertEqual(updates[0]['version'], '3.2.3.1')

	def test_beta_opted_in_client_gets_the_nightly_build(self):
		self._write_addon_release({'version': '3.2.3.1', 'url': 'https://example.org/remote-3.2.3.1.nvda-addon'})
		self._write_addon_beta_release({'version': 'nightly-20260813203214', 'url': 'https://example.org/remote-nightly-20260813203214.nvda-addon'})
		self.state.authorize_key('pcA')
		p, t = self._connect_raw()
		self.send(p, type='protocol_version', version=2)
		self.send(p, type='join', channel='pcA', connection_type='slave', allow_beta_updates=True)
		updates = [m for m in _read(t) if m['type'] == 'addon_update']
		# Two: the connectionMade-time push (stable, allow_beta_updates not
		# known yet) and the do_join re-push (beta, now that it is known).
		self.assertEqual(len(updates), 2)
		self.assertEqual(updates[0]['version'], '3.2.3.1')
		self.assertEqual(updates[1]['version'], 'nightly-20260813203214')

	def test_beta_opted_in_client_falls_back_to_stable_when_no_nightly_available(self):
		self._write_addon_release({'version': '3.2.3.1', 'url': 'https://example.org/remote-3.2.3.1.nvda-addon'})
		# No addon_beta_release.json at all - never checked yet, or the
		# nightly release has no usable asset right now.
		self.state.authorize_key('pcA')
		p, t = self._connect_raw()
		self.send(p, type='protocol_version', version=2)
		self.send(p, type='join', channel='pcA', connection_type='slave', allow_beta_updates=True)
		updates = [m for m in _read(t) if m['type'] == 'addon_update']
		self.assertTrue(all(u['version'] == '3.2.3.1' for u in updates))

	def test_allow_beta_updates_defaults_to_false_when_omitted(self):
		self.state.authorize_key('pcA')
		p, t = self._connect_raw()
		self.send(p, type='protocol_version', version=2)
		self.send(p, type='join', channel='pcA', connection_type='slave')
		msgs = _read(t)
		joined = [m for m in msgs if m['type'] == 'channel_joined']
		self.assertTrue(joined)  # join succeeded despite the field being absent
