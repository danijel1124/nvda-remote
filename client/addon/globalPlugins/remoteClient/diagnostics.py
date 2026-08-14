"""Consent-gated diagnostic log upload (admin-initiated).

The relay server's admin GUI can ask a specific connected session for
permission to read its NVDA log for troubleshooting (see server/state.py's
PendingLogRequest and server.py's do_admin_request_logs). This machine's own
explicit Yes/No decides it - never automatic - and the server independently
blocks all master/control input on this channel for the entire duration
(not just while the dialog is up), because remote key control in this fork
uses real Win32 SendInput (input.py's send_key, via localMachine.sendKey):
an ordinary modal dialog does not, by itself, stop an already-connected
controller from synthesizing an "OK" onto its own consent prompt. That
server-side gate is the actual protection; the modality here is only about
not doing two things at once on this machine.

Wired into SlaveSession only - a machine's own NVDA log is only ever asked
for on the connection pinned to its own hostname (the "home"/slave
connection), never on a transient master (control-another-computer) one.
"""
import os

import gui
import wx
from logHandler import log

from .protocol import RemoteMessageType

# Only the tail of the log is ever sent - keeps a single upload comfortably
# under the relay's 20MB newline-delimited line limit (server.py's
# Handler.MAX_LENGTH) without needing a chunking protocol, and bounds how
# much of this session (which can include things said/typed while NVDA was
# running) leaves the machine on a single approval. The server enforces its
# own, independent cap on write (state.py's MAX_DIAGNOSTIC_LOG_BYTES) as a
# backstop against a modified/buggy client - this one is what actually keeps
# the upload small in the first place.
LOG_TAIL_MAX_BYTES = 256 * 1024


class SlaveDiagnostics:
	"""Owns this machine's (slave) side of the consent-gated diagnostic log
	flow. One instance per SlaveSession."""

	def __init__(self, transport):
		self.transport = transport
		transport.registerInbound(
			RemoteMessageType.request_log_access, self.handleRequestLogAccess
		)

	def handleRequestLogAccess(self, **kwargs):
		# Runs on the wx main thread - transport.py's parse() wraps every
		# inbound handler in wx.CallAfter - so the blocking gui.messageBox
		# below is the normal pattern used elsewhere in this addon (e.g.
		# handleMOTD), not a special case.
		msg = _(
			"The relay server's administrator is requesting to read this "
			"computer's NVDA log, to help diagnose a problem. Only the most "
			"recent part of the log would be sent.\n\n"
			"Tip: if you'd like to reduce how much is included - for "
			"example to avoid sharing something you said or typed earlier "
			"in this session - restart NVDA first, then answer this "
			"request again when it reappears.\n\n"
			"Allow access?"
		)
		result = gui.messageBox(
			msg, _("NVDA Remote: Diagnostic Log Request"), wx.YES_NO | wx.ICON_QUESTION
		)
		granted = (result == wx.YES)
		self.transport.send(RemoteMessageType.log_access_response, granted=granted)
		if not granted:
			return
		content, truncated = self._readLog()
		self.transport.send(RemoteMessageType.log_upload, content=content, truncated=truncated)

	def _readLog(self):
		path = _findNvdaLogPath()
		if not path:
			return "(NVDA log file could not be located.)", False
		try:
			with open(path, 'rb') as f:
				f.seek(0, os.SEEK_END)
				size = f.tell()
				truncated = size > LOG_TAIL_MAX_BYTES
				f.seek(max(0, size - LOG_TAIL_MAX_BYTES))
				data = f.read()
			return data.decode('utf-8', errors='replace'), truncated
		except OSError:
			log.exception("NVDA Remote: failed to read NVDA log for diagnostic upload")
			return "(Failed to read the NVDA log file.)", False


def _findNvdaLogPath():
	"""Resolve wherever NVDA is actually writing its log right now, via its
	own log handler - rather than guessing a path convention that could
	differ by version, install type, or user config (e.g. a custom
	-f/--log-file)."""
	for handler in getattr(log, 'handlers', []):
		base = getattr(handler, 'baseFilename', None)
		if base:
			return base
	return None
