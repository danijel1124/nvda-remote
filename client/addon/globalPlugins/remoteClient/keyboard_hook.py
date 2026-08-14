from typing import List, Callable, Optional
import ctypes
from ctypes import Structure, c_int, c_long, wintypes

# Deliberately NVDA's own log (not a bare stdlib logging.getLogger('keyboard_hook')
# + StreamHandler(sys.stdout), which is where this used to go - a windowed NVDA.exe
# has no console attached to sys.stdout, so anything logged there was effectively
# discarded, hiding real exceptions from keyboard_proc's callback loop below).
from logHandler import log

HC_ACTION = 0
WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 16
LLKHF_UP = 128
KF_EXTENDED = 0x0100
LLKHF_EXTENDED = KF_EXTENDED >> 8

class KBDLLHOOKSTRUCT(Structure):
	_fields_ = [
		('vkCode', wintypes.DWORD),
		('scanCode', wintypes.DWORD),
		('flags', wintypes.DWORD),
		('time', wintypes.DWORD),
		('dwExtraInfo', wintypes.DWORD),
	]

LRESULT = c_long

LowLevelKeyboardProc = ctypes.WINFUNCTYPE(LRESULT, c_int, wintypes.LPARAM, wintypes.WPARAM)


class KeyboardHook:

	def __init__(self) -> None:
		self.callbacks: List[Callable[..., bool]] = list()
		self.proc = LowLevelKeyboardProc(self.keyboard_proc)
		# use_last_error=True on this one call (not module-wide) so
		# ctypes.get_last_error() below reflects SetWindowsHookExW's own
		# GetLastError() reliably - going through the plain ctypes.windll
		# proxy instead risks an intervening Win32 call (from the
		# interpreter itself) clobbering the thread's last-error value
		# before we get to read it.
		user32 = ctypes.WinDLL('user32', use_last_error=True)
		self.handle: Optional[int] = user32.SetWindowsHookExW(
			WH_KEYBOARD_LL,
			self.proc,
			ctypes.windll.kernel32.GetModuleHandleW(None),
			0
		)
		if not self.handle:
			# Previously silent: toggleRemoteKeyControl would still flip
			# sendingKeys and announce "Controlling remote machine.", but
			# with no hook installed keyboard_proc is never called by
			# Windows at all - no exception, no callback, nothing to log
			# from the paths above. Keys just silently keep reaching the
			# local machine. This is the loud version of that failure.
			log.error(
				"NVDA Remote: SetWindowsHookExW failed (GetLastError=%d) - "
				"remote key control will silently do nothing: NVDA will "
				"still announce 'Controlling remote machine' but keys will "
				"keep reaching the local machine." % ctypes.get_last_error()
			)
		else:
			log.info(f"NVDA Remote: keyboard hook installed (handle={self.handle})")

	def register_callback(self, callback: Callable[..., bool]) -> None:
		self.callbacks.append(callback)

	def unregister_callback(self, callback: Callable[..., bool]) -> None:
		self.callbacks.remove(callback)

	def keyboard_proc(self, code, wParam, lParam):
		if code < 0 or code != HC_ACTION:
			return ctypes.windll.user32.CallNextHookEx(0, code, wParam, lParam)
		event_type = wParam
		kbd = KBDLLHOOKSTRUCT.from_address(lParam)
		vk_code = kbd.vkCode
		scan_code = kbd.scanCode
		extended = bool(kbd.flags&LLKHF_EXTENDED)
		pressed = not bool(kbd.flags&LLKHF_UP)
		should_pass_on = True
		for callback in self.callbacks:
			try:
				should_pass_on = not callback(vk_code=vk_code, scan_code=scan_code, extended=extended, pressed=pressed)
			except Exception:
				# Deliberately fail open (should_pass_on keeps its prior value,
				# i.e. the key still reaches the local machine normally) -
				# a buggy callback must never be able to lock up the entire
				# keyboard system-wide. But it must be loud: this is exactly
				# the failure mode that silently turns "remote key control"
				# into "nothing happens, everything stays local" with no
				# visible symptom other than this log entry.
				log.exception(f"NVDA Remote: keyboard hook callback failed: {callback!r}")
		if not should_pass_on:
			return 1
		return ctypes.windll.user32.CallNextHookEx(0, code, wParam, lParam)

	def free(self):
		if self.handle:
			ctypes.windll.user32.UnhookWindowsHookEx(self.handle)
			self.handle = None
