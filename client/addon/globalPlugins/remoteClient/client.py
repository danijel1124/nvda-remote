import ctypes
import ctypes.wintypes
import logging
import os
import sys
import threading
from typing import Callable, Optional, Set, Tuple

import addonHandler
import api
import braille
import configobj
import core
import globalVars
import gui
import queueHandler
import speech
import ui
import wx
from config import isInstalledCopy
from keyboardHandler import KeyboardInputGesture
from logHandler import log
from utils.security import isRunningOnSecureDesktop

from . import configuration, cues, serializer, url_handler
from .alwaysCallAfter import alwaysCallAfter
from .connection_info import ConnectionInfo, ConnectionMode
from .localMachine import LocalMachine
from .menu import RemoteMenu
from .protocol import RemoteMessageType
from .secureDesktop import SecureDesktopHandler
from .session import MasterSession, SlaveSession
from .settings_panel import RemoteSettingsPanel
from .transport import RelayTransport

try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad."
	)
from winUser import WM_QUIT  # provided by NVDA

from . import dialogs, keyboard_hook
from .socket_utils import addressToHostPort, hostPortToAddress

logging.getLogger("keyboard_hook").addHandler(logging.StreamHandler(sys.stdout))

# Type aliases
KeyModifier = Tuple[int, bool]  # (vk_code, extended)
Address = Tuple[str, int]  # (hostname, port)


class RemoteClient:
	localScripts: Set[Callable]
	localMachine: LocalMachine
	masterSession: Optional[MasterSession]
	slaveSession: Optional[SlaveSession]
	keyModifiers: Set[KeyModifier]
	hostPendingModifiers: Set[KeyModifier]
	connecting: bool
	masterTransport: Optional[RelayTransport]
	slaveTransport: Optional[RelayTransport]
	hookThread: Optional[threading.Thread]
	sendingKeys: bool
	admin_ui: Optional[object] = None

	def __init__(
		self,
	):
		log.info("Initializing NVDA Remote client")
		self.keyModifiers = set()
		self.hostPendingModifiers = set()
		self.localScripts = set()
		self.localMachine = LocalMachine()
		self.slaveSession = None
		self.masterSession = None
		self.menu: RemoteMenu = RemoteMenu(self)
		self.connecting = False
		self.URLHandlerWindow = url_handler.URLHandlerWindow(
			callback=self.verifyAndConnect
		)
		url_handler.register_url_handler()
		self.masterTransport = None
		self.slaveTransport = None
		self._admin_transport = None
		# Whether the pinned admin transport is currently admin-authenticated,
		# so a silent reconnect (RelayTransport/ConnectorThread retries on drop
		# without tearing down the session) can be followed by an automatic
		# re-auth instead of a silent access_denied on the next admin action.
		self._admin_authenticated = False
		self._auto_reauth_pending = False
		self._awaitingSessionList = False
		self._awaitingUpdateCheck = False
		self.hookThread = None
		self.sendingKeys = False
		try:
			configuration.get_config()
		except configobj.ParseError:
			os.remove(
				os.path.abspath(
					os.path.join(
						globalVars.appArgs.configPath, configuration.CONFIG_FILE_NAME
					)
				)
			)
			log.error("Configuration file corrupted and reset")
			queueHandler.queueFunction(
				queueHandler.eventQueue,
				wx.CallAfter,
				wx.MessageBox,
				_("Your NVDA Remote configuration was corrupted and has been reset."),
				_("NVDA Remote Configuration Error"),
				wx.OK | wx.ICON_EXCLAMATION,
			)
		if not globalVars.appArgs.secure:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(
				RemoteSettingsPanel
			)
		self.sdHandler = SecureDesktopHandler()
		if isRunningOnSecureDesktop():
			connection = self.sdHandler.initializeSecureDesktop()
			if connection:
				self.connectAsSlave(connection)
				self.slaveSession.transport.connectedEvent.wait(
					self.sdHandler.SD_CONNECT_BLOCK_TIMEOUT
				)
		core.postNvdaStartup.register(self.performAutoconnect)

	def performAutoconnect(self):
		controlServerConfig = configuration.get_config()["controlserver"]
		if (
			not controlServerConfig["autoconnect"]
			or self.masterSession
			or self.slaveSession
		):
			log.debug("Autoconnect disabled or already connected")
			return
		key = controlServerConfig["key"]
		# self_hosted check removed or ignored
		address = addressToHostPort(controlServerConfig["host"])
		hostname, port = address
		# Always slave: controlling another machine is now a separate, later,
		# explicit action (Remote menu -> Control another computer), never
		# something to auto-restore on startup. controlServerConfig
		# ["connection_type"] is leftover from before this redesign (still in
		# the configspec, so minify_config won't drop it, and an old remote.ini
		# could have it at 1) - deliberately ignored here rather than migrated,
		# since nothing reads it anywhere else.
		conInfo = ConnectionInfo(mode=ConnectionMode.SLAVE, hostname=hostname, port=port, key=key)
		self.connect(conInfo)

	def terminate(self):
		self.sdHandler.terminate()
		self.disconnect()
		self.localMachine.terminate()
		self.localMachine = None
		self.menu.terminate()
		self.menu = None
		if not isInstalledCopy():
			url_handler.unregister_url_handler()
		self.URLHandlerWindow.destroy()
		self.URLHandlerWindow = None
		if not globalVars.appArgs.secure:
			gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(
				RemoteSettingsPanel
			)

	def toggleMute(self):
		self.localMachine.isMuted = not self.localMachine.isMuted
		self.menu.muteItem.Check(self.localMachine.isMuted)
		# Translators: Report when using gestures to mute or unmute the speech coming from the remote computer.
		status = (
			_("Mute speech and sounds from the remote computer")
			if self.localMachine.isMuted
			else _("Unmute speech and sounds from the remote computer")
		)
		ui.message(status)

	def pushClipboard(self):
		# Prefer the machine being controlled (master) over our own control-server
		# connection (slave) - matches copyLink's precedence below. With both
		# connected at once (control-another-computer), "push clipboard" without
		# further qualification should mean "to the machine I'm working on".
		connector = self.masterTransport or self.slaveTransport
		if not getattr(connector, "connected", False):
			ui.message(_("Not connected."))
			return
		try:
			connector.send(RemoteMessageType.set_clipboard_text, text=api.getClipData())
			cues.clipboard_pushed()
			ui.message(_("Clipboard pushed"))
		except TypeError:
			log.exception("Unable to push clipboard")

	def copyLink(self):
		session = self.masterSession or self.slaveSession
		url = session.getConnectionInfo().getURLToConnect()
		api.copyToClip(str(url))

	def sendSAS(self):
		# Only meaningful while controlling another machine (master). Being
		# slave-only is now the default state, so this must not assume
		# masterTransport exists - it would otherwise be a reachable
		# AttributeError for every user who hasn't also connected as master.
		if not getattr(self.masterTransport, "connected", False):
			ui.message(_("Not controlling a remote computer."))
			return
		self.masterTransport.send(RemoteMessageType.send_SAS)

	def connect(self, connectionInfo: ConnectionInfo):
		log.info(
			f"Initiating connection as {connectionInfo.mode.name} to {connectionInfo.hostname}:{connectionInfo.port}"
		)
		if connectionInfo.mode == ConnectionMode.MASTER:
			self.connectAsMaster(connectionInfo)
		elif connectionInfo.mode == ConnectionMode.SLAVE:
			self.connectAsSlave(connectionInfo)

	def disconnect(self):
		if self.masterSession is None and self.slaveSession is None:
			log.debug("Disconnect called but no active sessions")
			return
		log.info("Disconnecting from remote session")
		if self.masterSession is not None:
			self.disconnectAsMaster()
		if self.slaveSession is not None:
			self.disconnectAsSlave()
		cues.disconnected()

	def disconnectAsMaster(self):
		if self._admin_transport is self.masterSession.transport:
			self._admin_transport = None
			self._admin_authenticated = False
			self._awaitingUpdateCheck = False
		self.masterSession.close()
		self.masterSession = None
		self.masterTransport = None

	def disconnectAsSlave(self):
		if self._admin_transport is self.slaveSession.transport:
			self._admin_transport = None
			self._admin_authenticated = False
			self._awaitingUpdateCheck = False
		self.slaveSession.close()
		self.slaveSession = None
		self.slaveTransport = None
		self.sdHandler.slaveSession = None

	@alwaysCallAfter
	def onConnectAsMasterFailed(self):
		if self.masterTransport.successfulConnects == 0:
			log.error(f"Failed to connect to {self.masterTransport.address}")
			self.disconnectAsMaster()
			# Translators: Title of the connection error dialog.
			gui.messageBox(
				parent=gui.mainFrame,
				caption=_("Error Connecting"),
				# Translators: Message shown when cannot connect to the remote computer.
				message=_("Unable to connect to the remote computer"),
				style=wx.OK | wx.ICON_WARNING,
			)

	def doConnect(self, evt=None):
		if evt is not None:
			evt.Skip()
		previousConnections = configuration.get_config()["connections"][
			"last_connected"
		]
		hostnames = list(reversed(previousConnections))
		# Translators: Title of the connect dialog.
		dlg = dialogs.DirectConnectDialog(
			parent=gui.mainFrame, id=wx.ID_ANY, title=_("Connect"), hostnames=hostnames
		)

		def handleDialogCompletion(dlgResult):
			if dlgResult != wx.ID_OK:
				return
			connectionInfo = dlg.getConnectionInfo()
			self.connect(connectionInfo=connectionInfo)
		gui.runScriptModalDialog(dlg, callback=handleDialogCompletion)

	def showControlAnotherComputer(self, evt=None):
		if evt is not None:
			evt.Skip()
		if not self.isConnectedAsSlave():
			gui.messageBox(
				_("Connect to a control server first."), _("Error"), wx.OK | wx.ICON_ERROR
			)
			return
		if self.isConnectedAsMaster():
			gui.messageBox(
				_("Already controlling a remote computer. Disconnect from it first."),
				_("Error"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		self._awaitingSessionList = True
		# Must go out on the control-server (slave) connection specifically -
		# see the comment on this registration in connectAsSlave.
		self.slaveTransport.send(RemoteMessageType.list_sessions)

	@alwaysCallAfter
	def handle_session_list(self, sessions=None):
		# Inbound messages are dispatched from RelayTransport's reader thread
		# (see onConnectedAsSlave/onDisconnectedAsSlave above for the same
		# pattern) - runScriptModalDialog must run on the wx main thread.
		if not getattr(self, "_awaitingSessionList", False):
			return
		self._awaitingSessionList = False
		dlg = dialogs.ControlAnotherComputerDialog(gui.mainFrame, sessions or [])

		def handleDialogCompletion(dlgResult):
			if dlgResult != wx.ID_OK:
				return
			key = dlg.getSelectedKey()
			if key:
				self.connectToTarget(key)
		gui.runScriptModalDialog(dlg, callback=handleDialogCompletion)

	@alwaysCallAfter
	def handle_error(self, error=None, message=None):
		# The only case this currently needs to cover: do_list_sessions replies
		# with type=error/error=not_authorized instead of session_list when our
		# own channel is quarantined. Without this, _awaitingSessionList would
		# stay True forever and the menu click would silently do nothing.
		# Other error values (e.g. admin_* failures, which arrive on this same
		# transport since _get_active_transport prefers slave too) must be
		# left alone here - swallowing them would both show a wrong message
		# and clear the flag, dropping the real session_list that follows.
		if getattr(self, "_awaitingSessionList", False) and error == "not_authorized":
			self._awaitingSessionList = False
			gui.messageBox(
				_("Could not list other computers: this computer is not yet authorized on the control server."),
				_("Error"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		# admin_check_for_updates against a server too old to know that
		# command (pre-1.1.0) comes back as a plain error/unknown_admin_command
		# instead of admin_update_check_response - without this, the "Check
		# for updates now" button (disabled while the request is in flight)
		# would stay disabled forever, since only
		# show_update_check_results/handle_update_check_response re-enables it.
		if getattr(self, "_awaitingUpdateCheck", False):
			self._awaitingUpdateCheck = False
			if self.admin_ui:
				failure = {'error': error or 'unknown error'}
				self.admin_ui.show_update_check_results(failure, failure)

	def connectToTarget(self, key: str):
		if self.isConnectedAsMaster() or not self.isConnectedAsSlave():
			return
		slaveInfo = self.slaveSession.getConnectionInfo()
		connectionInfo = ConnectionInfo(
			hostname=slaveInfo.hostname,
			port=slaveInfo.port,
			mode=ConnectionMode.MASTER,
			key=key,
			# getConnectionInfo() above doesn't carry this (it never sets
			# insecure on the ConnectionInfo it builds), so read the live flag
			# off the transport itself - it starts at whatever was passed in
			# and flips True mid-connect on a trusted-fingerprint auto-retry
			# (transport.py's run(), ~line 334). Without this, a self-signed
			# control server that the slave connection already accepted would
			# make this master connect fail strict verification and land in
			# onMasterCertificateFailed -> handleCertificateFailure, whose
			# disconnect() tears down *both* sessions - dropping the control-
			# server connection the user still needs.
			insecure=self.slaveTransport.insecure,
		)
		# Chosen explicitly from a list the user just asked to see - no need
		# for verifyAndConnect's "are you sure?" confirmation on top of that.
		self.connectAsMaster(connectionInfo)

	def connectAsMaster(self, connectionInfo: ConnectionInfo):
		transport = RelayTransport.create(
			connection_info=connectionInfo, serializer=serializer.JSONSerializer()
		)
		self.masterSession = MasterSession(
			transport=transport, localMachine=self.localMachine
		)
		transport.transportCertificateAuthenticationFailed.register(
			self.onMasterCertificateFailed
		)
		transport.transportConnected.register(self.onConnectedAsMaster)
		transport.transportConnectionFailed.register(self.onConnectAsMasterFailed)
		transport.transportClosing.register(self.onDisconnectingAsMaster)
		transport.transportDisconnected.register(self.onDisconnectedAsMaster)
		# Once per transport object, not per (re)connect - onConnectedAsMaster
		# below refires on every silent ConnectorThread reconnect, and
		# re-registering the same bound methods on every one of those would
		# either stack up duplicate handlers or rely on unclear dedupe
		# semantics in NVDA's extension points.
		self.register_admin_handlers(transport)
		transport.reconnectorThread.start()
		self.masterTransport = transport
		self.menu.handleConnecting(connectionInfo.mode)

	def onConnectedAsMaster(self):
		log.info("Successfully connected as master")
		configuration.write_connection_to_config(self.masterSession.getConnectionInfo())
		self.menu.handleConnected(ConnectionMode.MASTER, True)
		self._maybe_reauth_admin(self.masterSession.transport)

		# We might have already created a hook thread before if we're restoring an
		# interrupted connection. We must not create another.
		if not self.hookThread:
			self.hookThread = threading.Thread(target=self.hook)
			self.hookThread.daemon = True
			self.hookThread.start()
		# Translators: Presented when connected to the remote computer.
		ui.message(_("Connected!"))
		cues.connected()


	def onDisconnectingAsMaster(self):
		log.info("Master session disconnecting")
		if self.menu:
			self.menu.handleConnected(ConnectionMode.MASTER, False)
		if self.localMachine:
			self.localMachine.isMuted = False
		self.sendingKeys = False
		if self.hookThread is not None:
			ctypes.windll.user32.PostThreadMessageW(
				self.hookThread.ident, WM_QUIT, 0, 0
			)
			self.hookThread.join()
			self.hookThread = None
		self.keyModifiers = set()


	def onDisconnectedAsMaster(self):
		log.info("Master session disconnected")
		# Translators: Presented when connection to a remote computer was interupted.
		ui.message(_("Connection interrupted"))

	def connectAsSlave(self, connectionInfo: ConnectionInfo):
		transport = RelayTransport.create(
			connection_info=connectionInfo, serializer=serializer.JSONSerializer()
		)
		self.slaveSession = SlaveSession(
			transport=transport, localMachine=self.localMachine
		)
		self.sdHandler.slaveSession = self.slaveSession
		self.slaveTransport = transport
		transport.transportCertificateAuthenticationFailed.register(
			self.onSlaveCertificateFailed
		)
		transport.transportConnected.register(self.onConnectedAsSlave)
		transport.transportDisconnected.register(self.onDisconnectedAsSlave)
		# Once per transport object - see the matching comment in connectAsMaster.
		self.register_admin_handlers(transport)
		# list_sessions/session_list only make sense on the slave (control-
		# server) connection: the server scopes the answer to *this*
		# connection's own channel, and a request sent over a master
		# connection instead would get scoped to the target's channel -
		# excluding the target itself and including our own machine. See
		# showControlAnotherComputer, which enforces sending through this
		# transport specifically rather than "whichever is active".
		transport.registerInbound(RemoteMessageType.session_list, self.handle_session_list)
		# Covers list_sessions' failure reply (own channel not authorized yet) -
		# see handle_error.
		transport.registerInbound(RemoteMessageType.error, self.handle_error)
		transport.reconnectorThread.start()
		self.menu.handleConnecting(connectionInfo.mode)


	@alwaysCallAfter
	def onConnectedAsSlave(self):
		log.info("Control connector connected")
		cues.control_server_connected()
		self._maybe_reauth_admin(self.slaveSession.transport)

		# Translators: Presented in direct (client to server) remote connection when the controlled computer is ready.
		speech.speakMessage(_("Connected to control server"))
		self.menu.handleConnected(ConnectionMode.SLAVE, True)
		configuration.write_connection_to_config(self.slaveSession.getConnectionInfo())


	@alwaysCallAfter
	def onDisconnectedAsSlave(self):
		log.info("Control connector disconnected")
		# cues.control_server_disconnected()
		self.menu.handleConnected(ConnectionMode.SLAVE, False)

	### certificate handling
	
	def handleCertificateFailure(self, transport: RelayTransport):
		log.warning(f"Certificate validation failed for {transport.address}")
		self.lastFailAddress = transport.address
		self.lastFailKey = transport.channel
		self.disconnect()
		try:
			certHash = transport.lastFailFingerprint

			wnd = dialogs.CertificateUnauthorizedDialog(None, fingerprint=certHash)
			a = wnd.ShowModal()
			if a == wx.ID_YES:
				config = configuration.get_config()
				config["trusted_certs"][hostPortToAddress(self.lastFailAddress)] = (
					certHash
				)
				config.write()
			if a == wx.ID_YES or a == wx.ID_NO:
				return True
		except Exception as ex:
			log.error(ex)
		return False

	@alwaysCallAfter
	def onMasterCertificateFailed(self):
		if self.handleCertificateFailure(self.masterSession.transport):
			connectionInfo = ConnectionInfo(
				mode=ConnectionMode.MASTER,
				hostname=self.lastFailAddress[0],
				port=self.lastFailAddress[1],
				key=self.lastFailKey,
				insecure=True,
			)
			self.connectAsMaster(connectionInfo=connectionInfo)

	@alwaysCallAfter
	def onSlaveCertificateFailed(self):
		if self.handleCertificateFailure(self.slaveSession.transport):
			connectionInfo = ConnectionInfo(
				mode=ConnectionMode.SLAVE,
				hostname=self.lastFailAddress[0],
				port=self.lastFailAddress[1],
				key=self.lastFailKey,
				insecure=True,
			)
			self.connectAsSlave(connectionInfo=connectionInfo)

	def hook(self):
		log.debug("Hook thread start")
		keyhook = keyboard_hook.KeyboardHook()
		keyhook.register_callback(self.hook_callback)
		msg = ctypes.wintypes.MSG()
		while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
			pass
		log.debug("Hook thread end")
		keyhook.free()

	def hook_callback(self, **kwargs):
		if not self.sendingKeys:
			return False
		keyCode = (kwargs["vk_code"], kwargs["extended"])
		gesture = KeyboardInputGesture(
			self.keyModifiers, keyCode[0], kwargs["scan_code"], keyCode[1]
		)
		if not kwargs["pressed"] and keyCode in self.hostPendingModifiers:
			self.hostPendingModifiers.discard(keyCode)
			return False
		gesture = KeyboardInputGesture(
			self.keyModifiers, keyCode[0], kwargs["scan_code"], keyCode[1]
		)
		if gesture.isModifier:
			if kwargs["pressed"]:
				self.keyModifiers.add(keyCode)
			else:
				self.keyModifiers.discard(keyCode)
		elif kwargs["pressed"]:
			script = gesture.script
			if script in self.localScripts:
				wx.CallAfter(script, gesture)
				return True
		self.masterTransport.send(RemoteMessageType.key, **kwargs)
		return True  # Don't pass it on

	def toggleRemoteKeyControl(self, gesture: KeyboardInputGesture):
		if not self.masterTransport:
			gesture.send()
			return
		self.sendingKeys = not self.sendingKeys
		log.info(f"Remote key control {'enabled' if self.sendingKeys else 'disabled'}")
		self.setReceivingBraille(self.sendingKeys)
		if self.sendingKeys:
			self.hostPendingModifiers = gesture.modifiers
			# Whether this actually controls anything is decided server-side
			# (self.masterSession.isController). Announcing "Controlling remote
			# machine" regardless would be misleading for an observer: their
			# keys get silently dropped until a throttled control_denied cue
			# eventually arrives, up to a few seconds later.
			if self.masterSession and self.masterSession.isController:
				# Translators: Presented when sending keyboard keys from the controlling computer to the controlled computer.
				ui.message(_("Controlling remote machine."))
			else:
				# Translators: Presented when F11 is toggled on while not actually in control of the remote machine.
				ui.message(_("Sending keys, but not in control. Press F10 to take control."))
		else:
			self.releaseKeys()
			# Translators: Presented when keyboard control is back to the controlling computer.
			ui.message(_("Controlling local machine."))

	def releaseKeys(self):
		# release all pressed keys in the guest.
		for k in self.keyModifiers:
			self.masterTransport.send(
				RemoteMessageType.key, vk_code=k[0], extended=k[1], pressed=False
			)
		self.keyModifiers = set()

	def setReceivingBraille(self, state):
		if state and self.masterSession.patchCallbacksAdded and braille.handler.enabled:
			self.masterSession.patcher.registerBrailleInput()
			self.localMachine.receivingBraille = True
		elif not state:
			self.masterSession.patcher.unregisterBrailleInput()
			self.localMachine.receivingBraille = False

	@alwaysCallAfter
	def verifyAndConnect(self, conInfo: ConnectionInfo):
		# Master and slave are independent connections that can coexist
		# (control-server baseline + optionally controlling another machine),
		# so this must only block a *second* connection of the *same* role,
		# not any connection at all - otherwise "control another computer"
		# could never work while already connected as slave.
		if self.connecting:
			gui.messageBox(
				_(
					"NVDA Remote is already connecting. Please wait."
				),
				_("NVDA Remote Already Connected"),
				wx.OK | wx.ICON_WARNING,
			)
			return
		alreadyConnectedInThisRole = (
			self.isConnectedAsMaster() if conInfo.mode == ConnectionMode.MASTER
			else self.isConnectedAsSlave()
		)
		if alreadyConnectedInThisRole:
			message = (
				_("Already controlling a remote computer. Disconnect from it before opening a new connection.")
				if conInfo.mode == ConnectionMode.MASTER
				else _("Already connected to a control server. Disconnect before opening a new connection.")
			)
			gui.messageBox(
				message,
				_("NVDA Remote Already Connected"),
				wx.OK | wx.ICON_WARNING,
			)
			return
		self.connecting = True
		serverAddr = conInfo.getAddress()
		key = conInfo.key
		if conInfo.mode == ConnectionMode.MASTER:
			question = _(
				"Do you wish to control the machine on server {server} with key {key}?"
			).format(server=serverAddr, key=key)
		elif conInfo.mode == ConnectionMode.SLAVE:
			question = _(
				"Do you wish to allow this machine to be controlled on server {server} with key {key}?"
			).format(server=serverAddr, key=key)
		if (
			gui.messageBox(
				question,
				_("NVDA Remote Connection Request"),
				wx.YES | wx.NO | wx.NO_DEFAULT | wx.ICON_WARNING,
			)
			!= wx.YES
		):
			self.connecting = False
			return
		self.connect(conInfo)
		self.connecting = False

	def isConnectedAsSlave(self):
		return bool(self.slaveTransport and self.slaveTransport.connected)

	def isConnectedAsMaster(self):
		return bool(self.masterTransport and self.masterTransport.connected)

	def isConnected(self):
		# "Any connection at all" - correct for checks that genuinely don't
		# care which role (e.g. "is the admin GUI usable at all"). For
		# anything that means "already connected as X specifically", use
		# isConnectedAsSlave()/isConnectedAsMaster() instead - master and
		# slave are independent connections that can coexist.
		return self.isConnectedAsSlave() or self.isConnectedAsMaster()

	def registerLocalScript(self, script):
		self.localScripts.add(script)

	def unregisterLocalScript(self, script):
		self.localScripts.discard(script)

	# Admin Logic
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
			self.send_admin_list_req() # Refresh list

	def handle_update_check_response(self, server=None, client=None):
		self._awaitingUpdateCheck = False
		if self.admin_ui:
			self.admin_ui.show_update_check_results(server, client)
