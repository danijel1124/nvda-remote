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
# Not one of ctypes.wintypes' predefined typedefs, but structurally the same
# as any other Win32 handle - a bare pointer-sized value. Declaring it (and
# argtypes/restype below for every WinAPI call this module makes) matters a
# lot more than it looks: without an explicit restype, ctypes assumes plain
# c_int (32-bit) for every windll/WinDLL function call. On 64-bit Windows a
# real handle/module-address is a 64-bit value, so an unset restype silently
# truncates it to its low 32 bits - this is exactly what broke SetWindowsHookExW
# below (see GetModuleHandleW's restype comment for the confirmed failure).
HHOOK = wintypes.HANDLE

LowLevelKeyboardProc = ctypes.WINFUNCTYPE(LRESULT, c_int, wintypes.LPARAM, wintypes.WPARAM)

# use_last_error=True (not the plain ctypes.windll proxy) so
# ctypes.get_last_error() in __init__ reflects SetWindowsHookExW's own
# GetLastError() reliably - going through ctypes.windll instead risks an
# intervening Win32 call (from the interpreter itself) clobbering the
# thread's last-error value before we get to read it.
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# GetModuleHandleW's return value (a HMODULE, i.e. a pointer) is exactly what
# gets fed into SetWindowsHookExW's hMod parameter below. Confirmed in
# production 2026-08-14: without this restype, ctypes.windll.kernel32
# .GetModuleHandleW(None) truncates the 64-bit module base address to 32
# bits (defaulting to c_int), so SetWindowsHookExW was handed a corrupted
# handle and failed with GetLastError()==126 (ERROR_MOD_NOT_FOUND) - keys
# then silently kept reaching the local machine, since a hook that never
# installed can never call keyboard_proc, let alone raise from it.
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

_user32.SetWindowsHookExW.restype = HHOOK
_user32.SetWindowsHookExW.argtypes = [c_int, LowLevelKeyboardProc, wintypes.HINSTANCE, wintypes.DWORD]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.UnhookWindowsHookEx.argtypes = [HHOOK]
_user32.CallNextHookEx.restype = LRESULT
_user32.CallNextHookEx.argtypes = [HHOOK, c_int, wintypes.WPARAM, wintypes.LPARAM]


class KeyboardHook:

	def __init__(self) -> None:
		self.callbacks: List[Callable[..., bool]] = list()
		self.proc = LowLevelKeyboardProc(self.keyboard_proc)
		self.handle: Optional[int] = _user32.SetWindowsHookExW(
			WH_KEYBOARD_LL,
			self.proc,
			_kernel32.GetModuleHandleW(None),
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
			return _user32.CallNextHookEx(0, code, wParam, lParam)
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
		return _user32.CallNextHookEx(0, code, wParam, lParam)

	def free(self):
		if self.handle:
			_user32.UnhookWindowsHookEx(self.handle)
			self.handle = None
