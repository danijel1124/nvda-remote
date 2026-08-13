"""NVDA Remote session management and message routing.

Implements the session layer for NVDA Remote, handling message routing,
connection roles, and NVDA feature coordination between instances.

Core Operation:
-------------
1. Transport layer delivers typed messages (RemoteMessageType)
2. Session routes messages to registered handlers
3. Handlers execute on wx main thread via CallAfter
4. Results flow back through transport layer

Connection Roles:
--------------
Master (Controlling)
	- Captures and forwards input
	- Receives remote output (speech/braille)
	- Manages connection state
	- Patches input handling

Slave (Controlled) 
	- Executes received commands
	- Forwards output to master(s)
	- Tracks connected masters
	- Patches output handling

Key Components:
------------
RemoteSession
	Base session managing shared functionality:
	- Message handler registration
	- Connection validation
	- Version compatibility
	- MOTD handling

MasterSession
	Controls remote instance:
	- Input capture/forwarding
	- Remote output reception
	- Connection management
	- Master-specific patches

SlaveSession
	Controlled by remote instance:
	- Command execution
	- Output forwarding
	- Multi-master support
	- Slave-specific patches

Thread Safety:
------------
All message handlers execute on wx main thread via CallAfter
to ensure thread-safe NVDA operations.

See Also:
	transport.py: Network communication
	local_machine.py: NVDA interface
	nvda_patcher.py: Feature patches
"""

import hashlib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any, Callable

from logHandler import log


import addonHandler
import braille
import gui
import nvwave
import speech
import tones
import ui
from speech.extensions import speechCanceled

from . import addon_update, configuration, connection_info, cues, nvda_patcher

from .localMachine import LocalMachine
from .protocol import RemoteMessageType
from .transport import RelayTransport


addonHandler.initTranslation()


EXCLUDED_SPEECH_COMMANDS = (
	speech.commands.BaseCallbackCommand,
	# _CancellableSpeechCommands are not designed to be reported and are used internally by NVDA. (#230)
	speech.commands._CancellableSpeechCommand,
)


class RemoteSession:
	"""Base class for a session that runs on either the master or slave machine.

	This abstract base class defines the core functionality shared between master and slave
	sessions. It handles basic session management tasks like:

	- Handling version mismatch notifications
	- Message of the day handling 
	- Connection info management
	- Transport registration

	"""

	transport: RelayTransport  # The transport layer handling network communication
	localMachine: LocalMachine  # Interface to control the local NVDA instance
	# Session mode - either 'master' or 'slave'
	mode: Optional[connection_info.ConnectionMode] = None
	# Patcher instance for NVDA modifications
	patcher: Optional[nvda_patcher.NVDAPatcher]
	patchCallbacksAdded: bool  # Whether callbacks are currently registered

	def __init__(
			self, localMachine: LocalMachine, transport: RelayTransport
	) -> None:
		log.info("Initializing Remote Session")
		self.localMachine = localMachine
		self.patcher = None
		self.patchCallbacksAdded = False
		self.transport = transport
		self.transport.registerInbound(
			RemoteMessageType.version_mismatch, self.handleVersionMismatch
		)
		self.transport.registerInbound(RemoteMessageType.motd, self.handleMOTD)
		self.transport.registerInbound(
			RemoteMessageType.addon_update, addon_update.handleAddonUpdate
		)
		self.transport.registerInbound(
			RemoteMessageType.server_info, self.handleServerInfo
		)
		self.serverVersion: Optional[str] = None
		self.serverUpdateCheck: Optional[dict] = None
		self.transport.registerInbound(
			RemoteMessageType.speak, self.localMachine.speak
		)
		self.transport.registerInbound(
			RemoteMessageType.set_clipboard_text, self.localMachine.setClipboardText
		)
		self.transport.registerInbound(
			RemoteMessageType.client_joined, self.handleClientConnected
		)
		self.transport.registerInbound(
			RemoteMessageType.client_left, self.handleClientDisconnected
		)
		self.transport.registerInbound(RemoteMessageType.ping, self.handlePing)

	def handlePing(self) -> None:
		"""Handle ping from server by sending a pong response."""
		self.transport.send(RemoteMessageType.pong)

	def handleServerInfo(self, version: Optional[str] = None, update_check: Optional[dict] = None) -> None:
		"""The connected relay server's own version (and, if it has one,
		its last known self-update-check result) - sent unconditionally by
		the server, not just to admins (v3.2.3). Stored for on-demand
		display (menu.py's "Server information" item and the admin GUI's
		server-version label) rather than proactively announced - a modal
		or speech interruption on every single connect would be too
		intrusive for something this low-stakes.
		"""
		self.serverVersion = version
		self.serverUpdateCheck = update_check

	def registerCallbacks(self) -> None:
		"""Register all callback handlers for this session.

		Registers the callbacks returned by _getPatcherCallbacks() with the patcher.
		Sets patchCallbacksAdded flag when complete.
		"""
		patcher_callbacks = self._getPatcherCallbacks()
		for event, callback in patcher_callbacks:
			self.patcher.registerCallback(event, callback)
		self.patchCallbacksAdded = True

	def unregisterCallbacks(self):
		"""Unregister all callback handlers for this session.

		Unregisters the callbacks returned by _getPatcherCallbacks() from the patcher.
		Clears patchCallbacksAdded flag when complete.
		"""
		patcher_callbacks = self._getPatcherCallbacks()
		for event, callback in patcher_callbacks:
			self.patcher.unregisterCallback(event, callback)
		self.patchCallbacksAdded = False

	def handleVersionMismatch(self) -> None:
		"""Handle protocol version mismatch between client and server.
		
		log.error("Protocol version mismatch detected with relay server")

		This method is called when the transport layer detects that the client's
		protocol version is not compatible. It:
		1. Displays a localized error message to the user
		2. Closes the transport connection
		3. Prevents further communication attempts
		"""
		# translators: Message for version mismatch
		message = _("""The version of the relay server which you have connected to is not compatible with this version of the Remote Client.
Please either use a different server or upgrade your version of the addon.""")
		ui.message(message)
		self.transport.close()

	def handleMOTD(self, motd: str, force_display=False):
		"""Handle Message of the Day from relay server.
		
		log.info("Received MOTD from server (force_display=%s)", force_display)

		Displays server MOTD to user if:
		1. It hasn't been shown before (tracked by message hash), or
		2. force_display is True (for important announcements)

		The MOTD system allows server operators to communicate important
		information to users like:
		- Service announcements
		- Maintenance windows
		- Version update notifications
		- Security advisories
		Note:
				Message hashes are stored per-server in the config file to track
				which messages have already been shown to the user.
		"""
		if force_display or self.shouldDisplayMotd(motd):
			gui.messageBox(
				parent=gui.mainFrame, caption=_("Message of the Day"), message=motd
			)

	def shouldDisplayMotd(self, motd: str) -> bool:
		conf = configuration.get_config()
		connection = self.getConnectionInfo()
		address = "{host}:{port}".format(
			host=connection.hostname, port=connection.port)
		motdBytes = motd.encode("utf-8", errors="surrogatepass")
		hashed = hashlib.sha1(motdBytes).hexdigest()
		current = conf["seen_motds"].get(address, "")
		if current == hashed:
			return False
		conf["seen_motds"][address] = hashed
		conf.write()
		return True

	def handleClientConnected(self, client: Optional[Dict[str, Any]] = None) -> None:
		"""Handle new client connection.
		
		log.info("Client connected: %r", client)

		Registers the patcher and callbacks if needed, then plays connection sound.
		Called when a new remote client establishes connection.
		"""
		self.patcher.register()
		if not self.patchCallbacksAdded:
			self.registerCallbacks()
		cues.client_connected()

	def handleClientDisconnected(self, client=None):
		"""Handle client disconnection.

		Plays disconnection sound when remote client disconnects.
		"""
		cues.client_disconnected()

	def getConnectionInfo(self) -> connection_info.ConnectionInfo:
		"""Get information about the current connection.

		Returns a ConnectionInfo object containing:
		- Hostname and port of the relay server
		- Channel key for the connection
		- Session mode (master/slave)
		"""
		hostname, port = self.transport.address
		key = self.transport.channel
		return connection_info.ConnectionInfo(
			hostname=hostname, port=port, key=key, mode=self.mode
		)

	def close(self) -> None:
		"""Close the transport connection.

		Terminates the network connection and cleans up resources.
		"""
		self.transport.close()

	def __del__(self) -> None:
		"""Ensure transport is closed when object is deleted."""
		self.close()


class SlaveSession(RemoteSession):
	"""Session that runs on the controlled (slave) NVDA instance.

	This class implements the slave side of an NVDA Remote connection. It handles:

	- Receiving and executing commands from master(s)
	- Forwarding speech/braille/tones/NVWave output to master(s)
	- Managing connected master clients and their braille display sizes
	- Coordinating braille display functionality

	The slave session allows multiple master connections simultaneously and manages
	state for each connected master separately.
	"""

	# Connection mode - always 'slave'
	mode: connection_info.ConnectionMode = connection_info.ConnectionMode.SLAVE
	# Patcher instance for NVDA modifications
	patcher: nvda_patcher.NVDASlavePatcher
	# Information about connected master clients
	masters: Dict[int, Dict[str, Any]]
	masterDisplaySizes: List[int]  # Braille display sizes of connected masters

	def __init__(
			self, localMachine: LocalMachine, transport: RelayTransport
	) -> None:
		super().__init__(localMachine, transport)
		self.transport.registerInbound(
			RemoteMessageType.key, self.localMachine.sendKey)
		self.masters = defaultdict(dict)
		self.masterDisplaySizes = []
		self.transport.transportClosing.register(self.handleTransportClosing)
		# handleTransportDisconnected existed but was never wired up to
		# anything - the unexpected-drop path (as opposed to a deliberate
		# close) never ran its cleanup at all, which is what let the beep-
		# storm feedback loop happen (see that method's docstring). Same
		# transportClosing-only-registration gap as MasterSession's
		# _resetControlState had before it was fixed to register on both.
		self.transport.transportDisconnected.register(self.handleTransportDisconnected)
		self.patcher = nvda_patcher.NVDASlavePatcher()
		self.transport.registerInbound(
			RemoteMessageType.channel_joined, self.handleChannelJoined
		)
		self.transport.registerInbound(
			RemoteMessageType.set_braille_info, self.handleBrailleInfo
		)
		self.transport.registerInbound(
			RemoteMessageType.set_display_size, self.setDisplaySize
		)
		braille.filter_displaySize.register(
			self.localMachine.handleFilterDisplaySize)
		self.transport.registerInbound(
			RemoteMessageType.braille_input, self.localMachine.brailleInput
		)
		self.transport.registerInbound(
			RemoteMessageType.send_SAS, self.localMachine.sendSAS
		)

	def registerCallbacks(self) -> None:
		super().registerCallbacks()
		self.transport.registerOutbound(
			tones.decide_beep, RemoteMessageType.tone)
		self.transport.registerOutbound(
			speechCanceled, RemoteMessageType.cancel)
		self.transport.registerOutbound(
			nvwave.decide_playWaveFile, RemoteMessageType.wave
		)
		braille.pre_writeCells.register(self.display)

	def unregisterCallbacks(self) -> None:
		super().unregisterCallbacks()
		self.transport.unregisterOutbound(RemoteMessageType.tone)
		self.transport.unregisterOutbound(RemoteMessageType.cancel)
		self.transport.unregisterOutbound(RemoteMessageType.wave)

	def handleClientConnected(self, client: Dict[str, Any]) -> None:
		super().handleClientConnected(client)
		if client["connection_type"] == "master":
			self.masters[client["id"]]["active"] = True

	def handleChannelJoined(
			self,
			channel: Optional[str] = None,
			clients: Optional[List[Dict[str, Any]]] = None,
			origin: Optional[int] = None,
	) -> None:
		if clients is None:
			clients = []
		for client in clients:
			self.handleClientConnected(client)

	def handleTransportClosing(self) -> None:
		"""Handle cleanup when transport connection is closing.

		Unregisters the patcher and removes any registered callbacks
		to ensure clean shutdown of remote features.
		"""
		log.info("Transport closing, unregistering slave session patcher")
		self.patcher.unregister()
		if self.patchCallbacksAdded:
			self.unregisterCallbacks()

	def handleTransportDisconnected(self) -> None:
		"""Handle disconnection from the transport layer.

		Called when the transport connection is lost unexpectedly (as
		opposed to handleTransportClosing, for a deliberate close). This
		method:
		1. Plays a connection sound cue
		2. Removes any NVDA patches
		3. Unregisters the outbound wave/tone/cancel relay callbacks

		Step 3 matters as more than just symmetry with handleTransportClosing:
		without it, nvwave.decide_playWaveFile stays hooked after an
		unexpected drop, so every locally-played wave file (including NVDA's
		own error.wav, played automatically for any ERROR-level log entry)
		keeps getting relayed via transport.send() - which is disconnected,
		so that logs its own "Attempted to send message while not connected"
		ERROR, which triggers another local error.wav, which gets relayed
		again... an unbounded feedback loop of error beeps until the
		transport reconnects. Confirmed from a real user's NVDA log.
		"""
		log.info("Transport disconnected from slave session")
		cues.client_connected()
		self.patcher.unregister()
		if self.patchCallbacksAdded:
			self.unregisterCallbacks()

	def handleClientDisconnected(self, client: Optional[Dict[str, Any]] = None) -> None:
		super().handleClientDisconnected(client)
		if client["connection_type"] == "master":
			log.info("Master client disconnected: %r", client)
			del self.masters[client["id"]]
		if not self.masters:
			self.patcher.unregister()

	def setDisplaySize(self, sizes=None):
		self.masterDisplaySizes = (
			sizes
			if sizes
			else [info.get("braille_numCells", 0) for info in self.masters.values()]
		)
		log.debug("Setting slave display size to: %r", self.masterDisplaySizes)
		self.localMachine.setBrailleDisplay_size(self.masterDisplaySizes)

	def handleBrailleInfo(
			self,
			name: Optional[str] = None,
			numCells: int = 0,
			origin: Optional[int] = None,
	) -> None:
		if not self.masters.get(origin):
			return
		self.masters[origin]["braille_name"] = name
		self.masters[origin]["braille_numCells"] = numCells
		self.setDisplaySize()

	def _getPatcherCallbacks(self) -> List[Tuple[str, Callable[..., Any]]]:
		"""Get callbacks to register with the patcher.

		Returns:
				Sequence of (event_name, callback_function) pairs for:
				- Speech output
				- Speech pausing
				- Display size updates
		"""
		return (
			("speak", self.speak),
			("pause_speech", self.pauseSpeech),
			("set_display", self.setDisplaySize),
		)

	def _filterUnsupportedSpeechCommands(self, speechSequence: List[Any]) -> List[Any]:
		"""Remove unsupported speech commands from a sequence.

		Filters out commands that cannot be properly serialized or executed remotely,
		like callback commands and cancellable commands.

		Returns:
				Filtered sequence containing only supported speech commands
		"""
		return list([
			item for item in speechSequence
			if not isinstance(item, EXCLUDED_SPEECH_COMMANDS)
		])

	def speak(self, speechSequence: List[Any], priority: Optional[str]) -> None:
		"""Forward speech output to connected master instances.

		Filters the speech sequence for supported commands and sends it
		to master instances for speaking.
		"""
		self.transport.send(RemoteMessageType.speak,
							sequence=self._filterUnsupportedSpeechCommands(
								speechSequence),
							priority=priority
							)

	def pauseSpeech(self, switch: bool) -> None:
		"""Toggle speech pause state on master instances.
		"""
		self.transport.send(type=RemoteMessageType.pause_speech, switch=switch)

	def display(self, cells: List[int]) -> None:
		"""Forward braille display content to master instances.

		Only sends braille data if there are connected masters with braille displays.
		"""
		# Only send braille data when there are controlling machines with a braille display
		if self.hasBrailleMasters():
			self.transport.send(type=RemoteMessageType.display, cells=cells)

	def hasBrailleMasters(self) -> bool:
		"""Check if any connected masters have braille displays.

		Returns:
				True if at least one master has a braille display with cells > 0
		"""
		return bool([i for i in self.masterDisplaySizes if i > 0])


class MasterSession(RemoteSession):
	"""Session that runs on the controlling (master) NVDA instance.

	This class implements the master side of an NVDA Remote connection. It handles:

	- Sending control commands to slaves
	- Receiving and playing speech/braille from slaves
	- Playing basic notification sounds from slaves
	- Managing connected slave clients  
	- Synchronizing braille display information
	- Patching NVDA for remote input handling

	The master session takes input from the local NVDA instance and forwards
	appropriate commands to control the remote slave instance.
	"""
	mode: connection_info.ConnectionMode = connection_info.ConnectionMode.MASTER
	patcher: nvda_patcher.NVDAMasterPatcher
	slaves: Dict[int, Dict[str, Any]]  # Information about connected slave
	# Our own server-assigned id within this channel - the server's
	# `channel_joined` response is the only place this is ever sent to us
	# (as `origin`). Needed to tell "I'm the controller" apart from "someone
	# else is" when a `control_changed: {controller: <id>}` message arrives.
	own_user_id: Optional[int]

	def __init__(
			self, localMachine: LocalMachine, transport: RelayTransport
	) -> None:
		super().__init__(localMachine, transport)
		self.slaves = defaultdict(dict)
		self.own_user_id = None
		# Whether *we* are the one allowed to send input on this channel right
		# now (server-enforced; this is only ever a display of what the server
		# already decided - see control_changed/control_denied below).
		self.isController = False
		self._hasSeenControlChanged = False
		self.patcher = nvda_patcher.NVDAMasterPatcher()
		self.transport.registerInbound(
			RemoteMessageType.control_changed, self.handleControlChanged
		)
		self.transport.registerInbound(
			RemoteMessageType.control_denied, self.handleControlDenied
		)
		# The server sees a reconnect as a brand-new connection (new user_id,
		# no controller state carried over) - a stale isController=True from
		# before the drop must not survive into the gap before the fresh
		# channel_joined/control_changed re-establish the truth. transportClosing
		# only fires on a deliberate close() (transport.py ~557); an unexpected
		# drop goes through transportDisconnected instead (~365) - register on
		# both, registering twice is harmless.
		self.transport.transportClosing.register(self._resetControlState)
		self.transport.transportDisconnected.register(self._resetControlState)
		self.transport.registerInbound(
			RemoteMessageType.cancel, self.localMachine.cancelSpeech
		)
		self.transport.registerInbound(
			RemoteMessageType.pause_speech, self.localMachine.pauseSpeech
		)
		self.transport.registerInbound(
			RemoteMessageType.tone, self.localMachine.beep)
		self.transport.registerInbound(
			RemoteMessageType.wave, self.localMachine.playWave
		)
		self.transport.registerInbound(
			RemoteMessageType.display, self.localMachine.display
		)
		self.transport.registerInbound(
			RemoteMessageType.nvda_not_connected, self.handleNVDANotConnected
		)
		self.transport.registerInbound(
			RemoteMessageType.channel_joined, self.handleChannel_joined
		)
		self.transport.registerInbound(
			RemoteMessageType.set_braille_info, self.sendBrailleInfo
		)

	def handleControlChanged(self, controller: Optional[int] = None) -> None:
		"""The server's authoritative word on who currently controls this
		channel - sent once on join, on every actual change, and repeated
		periodically while nobody controls and we're listening (see
		server.py's CONTROL_FREE_MSG_INTERVAL)."""
		wasController = self.isController
		self.isController = controller is not None and controller == self.own_user_id
		# Suppress only the case where the first control_changed just confirms
		# what joining already implied: solo master -> auto-controller, the
		# only flow that exists in production today, redundant with
		# onConnectedAsMaster's own "Connected!". An observer's first
		# control_changed ("someone else already controls") carries genuinely
		# new information - there's no periodic reminder for that case (the
		# 30s loop only runs while controller is None), so staying silent here
		# would leave them with no cue at all until a denial, up to 3s later.
		isFirst = not self._hasSeenControlChanged
		self._hasSeenControlChanged = True
		if isFirst and self.isController:
			return
		if self.isController and not wasController:
			# Translators: Presented when this machine gains control after
			# another participant released it or after taking over.
			ui.message(_("You are now controlling the remote machine."))
		elif wasController and not self.isController:
			# Translators: Presented when control was taken away/given up.
			ui.message(_("You are no longer controlling the remote machine."))
		elif not self.isController and controller is None:
			# F10 only reaches the server while sendingKeys is on (F11
			# toggles that - see client.py's toggleRemoteKeyControl/
			# hook_callback). This fires from a server push regardless of
			# local F11 state and repeats every 30s (server.py's
			# CONTROL_FREE_MSG_INTERVAL) - a listener waiting for a handoff has
			# typically already pressed F11 once, so telling them to press it
			# again would toggle it back off. Keep the wording state-neutral
			# (correct whether F11 is currently on or off) instead of naming a
			# concrete key sequence that depends on state this class can't see.
			# Translators: Presented (repeatedly, while listening) when nobody
			# is currently controlling the remote machine.
			ui.message(_("Nobody is currently controlling the remote machine. While sending keys to it, press F10 to take control."))
		elif not self.isController and controller is not None:
			# Covers both a freshly-joined observer learning who already
			# controls, and a later handoff to someone else while we keep
			# listening - either way, someone other than us is now in control.
			# Translators: Presented when someone else (not us) is controlling the remote machine.
			ui.message(_("Someone else is currently controlling the remote machine."))

	def handleControlDenied(self) -> None:
		# Translators: Presented when trying to send input while not in
		# control (an observer/listener on a channel someone else controls).
		ui.message(_("You are not currently controlling the remote machine."))

	def _resetControlState(self) -> None:
		self.isController = False
		self._hasSeenControlChanged = False

	def handleNVDANotConnected(self) -> None:
		log.warning("Attempted to connect to remote NVDA that is not available")
		speech.cancelSpeech()
		ui.message(_("Remote NVDA not connected."))

	def handleChannel_joined(
			self,
			channel: Optional[str] = None,
			clients: Optional[List[Dict[str, Any]]] = None,
			origin: Optional[int] = None,
	) -> None:
		# channel_joined's `origin` is the server's id for *this* connection -
		# the only place we're ever told our own id, needed to tell ourselves
		# apart from other masters in control_changed messages.
		if origin is not None:
			self.own_user_id = origin
		if clients is None:
			clients = []
		for client in clients:
			self.handleClientConnected(client)

	def handleClientConnected(self, client=None):
		super().handleClientConnected(client)
		self.sendBrailleInfo()

	def handleClientDisconnected(self, client=None):
		"""Handle client disconnection.

		Unregisters the patcher and removes any registered callbacks.
		Also calls parent class disconnection handler.
		"""
		super().handleClientDisconnected(client)
		self.patcher.unregister()
		if self.patchCallbacksAdded:
			self.unregisterCallbacks()

	def sendBrailleInfo(
			self, display: Optional[Any] = None, displaySize: Optional[int] = None
	) -> None:
		if display is None:
			display = braille.handler.display
		if displaySize is None:
			displaySize = braille.handler.displaySize
		log.debug("Sending braille info to slave - display: %s, size: %d", 
				 display.name if display else "None", 
				 displaySize if displaySize else 0)
		self.transport.send(
			type="set_braille_info", name=display.name, numCells=displaySize
		)

	def brailleInput(self, **kwargs) -> None:
		self.transport.send(type=RemoteMessageType.braille_input, **kwargs)

	def _getPatcherCallbacks(self) -> List[Tuple[str, Callable[..., Any]]]:
		"""Get callbacks to register with the patcher.

		Returns:
				Sequence of (event_name, callback_function) pairs for:
				- Braille input handling
				- Display info updates
		"""
		return (
			("braille_input", self.brailleInput),
			("set_display", self.sendBrailleInfo),
		)
