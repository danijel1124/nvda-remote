"""Admin-protocol client for NVDA Remote.

Everything here is about talking to the relay server's admin API (auth,
listing/approving/removing sessions, triggering a remote update check) -
split out of client.py's RemoteClient because it changes for admin-feature
reasons and touches almost none of the master/slave connection-lifecycle
state that makes up the rest of that file (see root CLAUDE.md's "one file,
one reason to change" convention).

Mixed into RemoteClient (not composed as a separate object) so every
existing call site - server_admin_gui.py's `self.client.send_admin_auth(...)`
and friends, and client.py's own connect/disconnect code - keeps working
unchanged; only client.py itself needed to change, to call
_init_admin_state()/clear_admin_transport_if() at the right points.
"""
import gui
import wx

from . import configuration
from .protocol import RemoteMessageType


class AdminClientMixin:
	"""Admin-API half of RemoteClient. See module docstring."""

	def _init_admin_state(self):
		"""Called once from RemoteClient.__init__."""
		self.admin_ui = None
		self._admin_transport = None
		# Whether the pinned admin transport is currently admin-authenticated,
		# so a silent reconnect (RelayTransport/ConnectorThread retries on drop
		# without tearing down the session) can be followed by an automatic
		# re-auth instead of a silent access_denied on the next admin action.
		self._admin_authenticated = False
		self._auto_reauth_pending = False
		self._awaitingUpdateCheck = False

	def clear_admin_transport_if(self, transport):
		"""Called from disconnectAsMaster/disconnectAsSlave: if the
		disconnecting transport was the pinned admin one, forget it so the
		next admin action re-pins to whatever's still connected instead of
		talking to a dead transport."""
		if self._admin_transport is transport:
			self._admin_transport = None
			self._admin_authenticated = False
			self._awaitingUpdateCheck = False

	def set_admin_ui(self, ui):
		self.admin_ui = ui

	def onShowAdmin(self, evt):
		if not self.isConnected():
			gui.messageBox(_("Not connected to a server."), _("Error"), wx.OK | wx.ICON_ERROR)
			return
		from .server_admin_gui import ServerAdminDialog
		dlg = ServerAdminDialog(self)
		dlg.ShowModal()

	def _get_active_transport(self):
		# Pin to whichever transport admin traffic is already going out on, so
		# connecting/disconnecting as master (control-another-computer) never
		# swaps the admin channel out from under an already-authenticated
		# session mid-dialog. Admin auth is per-TCP-connection server-side, so
		# silently switching transports means every subsequent admin_* call
		# would come back access_denied with no obvious cause.
		if self._admin_transport is not None and getattr(self._admin_transport, "connected", False):
			return self._admin_transport
		# Prefer the control-server (slave) connection: it's the stable "home"
		# connection, whereas a master connection to some other machine is
		# transient and may end at any time.
		transport = None
		if self.slaveSession:
			transport = self.slaveSession.transport
		elif self.masterSession:
			transport = self.masterSession.transport
		self._admin_transport = transport
		return transport

	def send_admin_auth(self, token):
		# A user-initiated login always wants its own result shown, even if an
		# automatic reauth's response never arrived (send failed, connection
		# dropped again, ...) and left the flag set - otherwise this deliberate
		# attempt would be silently swallowed with no Login Successful/Failed.
		self._auto_reauth_pending = False
		t = self._get_active_transport()
		if t: t.send(RemoteMessageType.auth_admin, token=token)

	def send_admin_list_req(self):
		t = self._get_active_transport()
		if t: t.send(RemoteMessageType.admin_list_channels)

	def send_admin_approve(self, key):
		t = self._get_active_transport()
		if t: t.send(RemoteMessageType.admin_approve_channel, key=key)

	def send_admin_remove(self, key):
		t = self._get_active_transport()
		if t: t.send(RemoteMessageType.admin_remove_channel, key=key)

	def send_admin_check_for_updates(self):
		t = self._get_active_transport()
		if t:
			self._awaitingUpdateCheck = True
			t.send(RemoteMessageType.admin_check_for_updates)

	def send_admin_request_logs(self, key):
		"""Ask the server to request the given session's NVDA log for
		troubleshooting - consent-gated on that machine's side (it shows its
		own Yes/No dialog; nothing is read or sent without that explicit,
		physical answer). See server/state.py's PendingLogRequest for why
		remote control on that channel is blocked server-side for the
		duration, and client's admin_log_upload_status handler for the
		eventual result."""
		t = self._get_active_transport()
		if t:
			t.send(RemoteMessageType.admin_request_logs, key=key)

	def _maybe_reauth_admin(self, transport):
		"""Called whenever a transport (re)connects. RelayTransport reconnects
		silently on a dropped connection (ConnectorThread) without tearing the
		session down - but the server sees a brand-new TCP connection, with
		admin auth reset. If this is the pinned admin transport and we were
		previously admin-authenticated on it, transparently re-auth using the
		same stored token rather than leaving every subsequent admin_* call to
		silently come back access_denied after the next network hiccup."""
		if transport is not self._admin_transport or not self._admin_authenticated:
			return
		address = f"{transport.address[0]}:{transport.address[1]}"
		token = configuration.get_admin_token_for_address(address)
		if token:
			# Tell handle_auth_response this auth_admin was triggered by us
			# reconnecting, not by the user clicking Login, so it doesn't pop an
			# unsolicited "Login Successful" box if the admin dialog happens to
			# be open at that moment.
			self._auto_reauth_pending = True
			transport.send(RemoteMessageType.auth_admin, token=token)

	def register_admin_handlers(self, transport):
		transport.registerInbound(RemoteMessageType.auth_admin_response, self.handle_auth_response)
		transport.registerInbound(RemoteMessageType.admin_channel_list, self.handle_channel_list)
		transport.registerInbound(RemoteMessageType.admin_response, self.handle_admin_response)
		transport.registerInbound(RemoteMessageType.admin_update_check_response, self.handle_update_check_response)
		transport.registerInbound(RemoteMessageType.admin_log_upload_status, self.handle_log_upload_status)

	def handle_auth_response(self, success=False):
		self._admin_authenticated = success
		wasAutoReauth = self._auto_reauth_pending
		self._auto_reauth_pending = False
		if wasAutoReauth:
			# Silent: keep the session usable and, if the dialog happens to be
			# open, its list fresh - but no modal the user didn't ask for.
			if success and self.admin_ui:
				self.send_admin_list_req()
			return
		if self.admin_ui:
			msg = _("Login Successful") if success else _("Login Failed")
			self.admin_ui.show_status(msg)
			if success:
				self.send_admin_list_req()

	def handle_channel_list(self, channels=None):
		if self.admin_ui and channels is not None:
			self.admin_ui.update_list(channels)

	def handle_admin_response(self, command=None, success=False, key=None):
		if success:
			self.send_admin_list_req()  # Refresh list

	def handle_update_check_response(self, server=None, client=None):
		self._awaitingUpdateCheck = False
		if self.admin_ui:
			self.admin_ui.show_update_check_results(server, client)

	def handle_log_upload_status(self, key=None, status=None, detail=None, truncated=False):
		if self.admin_ui:
			self.admin_ui.show_log_upload_status(key, status, detail, truncated)
