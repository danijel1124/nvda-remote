"""Logic-level regression tests for diagnostics.py (consent-gated diagnostic
log upload, admin-initiated).

Reuses the wx/gui/logHandler/globalVars stubs and the bypass-remoteClient/
__init__.py import trick from test_menu_and_dialogs.py - see that file's
module docstring for why. diagnostics.py has no NVDA-only dependencies
beyond those (unlike session.py, which pulls in braille/nvwave/speech/tones
and can't be import-tested outside NVDA at all), so it's tested directly
here instead of through a real SlaveSession.

Run with the system python3:
    python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

from test_menu_and_dialogs import _install_stub_modules, PKG_DIR


def _load_diagnostics_module():
	_install_stub_modules()
	if "remoteClient" not in sys.modules:
		pkg = types.ModuleType("remoteClient")
		pkg.__path__ = [PKG_DIR]
		pkg.__package__ = "remoteClient"
		sys.modules["remoteClient"] = pkg
	import remoteClient.diagnostics as diagnostics  # noqa: F401
	return diagnostics


diagnostics = _load_diagnostics_module()


class FakeTransport:
	"""Stands in for RelayTransport: records registerInbound/send calls."""

	def __init__(self):
		self.registered = {}
		self.sent = []

	def registerInbound(self, messageType, handler):
		self.registered[messageType] = handler

	def send(self, messageType, **kwargs):
		self.sent.append((messageType, kwargs))


class FindNvdaLogPathTests(unittest.TestCase):
	def test_returns_the_first_handler_with_a_basefilename(self):
		fakeHandler = mock.Mock()
		fakeHandler.baseFilename = "C:\\Users\\user\\AppData\\Local\\Temp\\nvda.log"
		with mock.patch.object(diagnostics.log, "handlers", [fakeHandler], create=True):
			self.assertEqual(diagnostics._findNvdaLogPath(), fakeHandler.baseFilename)

	def test_skips_handlers_without_a_basefilename(self):
		streamHandler = mock.Mock(spec=[])  # no baseFilename attribute at all
		fileHandler = mock.Mock()
		fileHandler.baseFilename = "/tmp/nvda.log"
		with mock.patch.object(diagnostics.log, "handlers", [streamHandler, fileHandler], create=True):
			self.assertEqual(diagnostics._findNvdaLogPath(), "/tmp/nvda.log")

	def test_returns_none_when_nothing_matches(self):
		with mock.patch.object(diagnostics.log, "handlers", [], create=True):
			self.assertIsNone(diagnostics._findNvdaLogPath())

	def test_falls_back_to_root_logger_handlers(self):
		# This is the real-world case, confirmed against a live production
		# NVDA instance 2026-08-14: NVDA's logHandler.initialize() calls
		# log.root.addHandler(logHandler), not log.addHandler(...) - the
		# FileHandler lives on the stdlib root logger, not on NVDA's own
		# named `log` child logger, which has no handlers of its own.
		fileHandler = mock.Mock()
		fileHandler.baseFilename = "/tmp/nvda.log"
		fakeRoot = mock.Mock()
		fakeRoot.handlers = [fileHandler]
		with mock.patch.object(diagnostics.log, "handlers", [], create=True), \
			mock.patch.object(diagnostics.log, "root", fakeRoot, create=True):
			self.assertEqual(diagnostics._findNvdaLogPath(), "/tmp/nvda.log")

	def test_no_root_attribute_does_not_crash(self):
		# Defensive: if some future NVDA version's `log` object has no
		# `.root` at all, this must degrade to "not found", not raise.
		with mock.patch.object(diagnostics.log, "handlers", [], create=True):
			if hasattr(diagnostics.log, "root"):
				with mock.patch.object(diagnostics.log, "root", None, create=True):
					self.assertIsNone(diagnostics._findNvdaLogPath())
			else:
				self.assertIsNone(diagnostics._findNvdaLogPath())


class ReadLogTests(unittest.TestCase):
	def _writeTempLog(self, content: bytes):
		fd, path = tempfile.mkstemp()
		with os.fdopen(fd, "wb") as f:
			f.write(content)
		self.addCleanup(os.remove, path)
		return path

	def test_small_log_is_sent_whole_and_not_marked_truncated(self):
		path = self._writeTempLog(b"hello world")
		diag = diagnostics.SlaveDiagnostics.__new__(diagnostics.SlaveDiagnostics)
		with mock.patch.object(diagnostics, "_findNvdaLogPath", return_value=path):
			content, truncated = diag._readLog()
		self.assertEqual(content, "hello world")
		self.assertFalse(truncated)

	def test_large_log_is_tail_capped_and_marked_truncated(self):
		data = (b"x" * (diagnostics.LOG_TAIL_MAX_BYTES + 100)) + b"TAIL_MARKER"
		path = self._writeTempLog(data)
		diag = diagnostics.SlaveDiagnostics.__new__(diagnostics.SlaveDiagnostics)
		with mock.patch.object(diagnostics, "_findNvdaLogPath", return_value=path):
			content, truncated = diag._readLog()
		self.assertTrue(truncated)
		self.assertLessEqual(len(content.encode("utf-8", errors="replace")), diagnostics.LOG_TAIL_MAX_BYTES)
		self.assertTrue(content.endswith("TAIL_MARKER"))

	def test_missing_log_path_returns_a_placeholder_not_an_exception(self):
		diag = diagnostics.SlaveDiagnostics.__new__(diagnostics.SlaveDiagnostics)
		with mock.patch.object(diagnostics, "_findNvdaLogPath", return_value=None):
			content, truncated = diag._readLog()
		self.assertFalse(truncated)
		self.assertIn("could not be located", content)

	def test_unreadable_log_path_returns_a_placeholder_not_an_exception(self):
		diag = diagnostics.SlaveDiagnostics.__new__(diagnostics.SlaveDiagnostics)
		with mock.patch.object(diagnostics, "_findNvdaLogPath", return_value="/does/not/exist.log"):
			content, truncated = diag._readLog()
		self.assertFalse(truncated)
		self.assertIn("Failed to read", content)


class HandleRequestLogAccessTests(unittest.TestCase):
	def test_declining_sends_only_the_denial_never_reads_the_log(self):
		transport = FakeTransport()
		diag = diagnostics.SlaveDiagnostics(transport)
		with mock.patch.object(diagnostics.gui, "messageBox", return_value=diagnostics.wx.NO), \
			mock.patch.object(diag, "_readLog") as mockRead:
			diag.handleRequestLogAccess()
		self.assertEqual(transport.sent, [
			(diagnostics.RemoteMessageType.log_access_response, {"granted": False}),
		])
		mockRead.assert_not_called()

	def test_granting_sends_the_response_then_the_upload(self):
		transport = FakeTransport()
		diag = diagnostics.SlaveDiagnostics(transport)
		with mock.patch.object(diagnostics.gui, "messageBox", return_value=diagnostics.wx.YES), \
			mock.patch.object(diag, "_readLog", return_value=("log content", True)):
			diag.handleRequestLogAccess()
		self.assertEqual(transport.sent, [
			(diagnostics.RemoteMessageType.log_access_response, {"granted": True}),
			(diagnostics.RemoteMessageType.log_upload, {"content": "log content", "truncated": True}),
		])

	def test_registers_itself_on_the_transport_at_construction(self):
		transport = FakeTransport()
		diag = diagnostics.SlaveDiagnostics(transport)
		self.assertEqual(
			transport.registered[diagnostics.RemoteMessageType.request_log_access],
			diag.handleRequestLogAccess,
		)


if __name__ == "__main__":
	unittest.main()
