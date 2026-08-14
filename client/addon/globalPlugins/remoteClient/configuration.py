import os
import socket
from io import StringIO

import configobj
import globalVars
from configobj import validate

from .connection_info import ConnectionInfo

CONFIG_FILE_NAME = 'remote.ini'

_config = None
configspec = StringIO("""
[connections]
	last_connected = list(default=list())
[controlserver]
	version = float(default=2.0)
	autoconnect = boolean(default=True)
	self_hosted = boolean(default=False)
	connection_type = integer(default=0)
	host = string(default="danijel0.danijels-computer.de")
	port = integer(default=6837)
	key = string(default="")
	admin_token = string(default="")

[admin_tokens]
	__many__ = string(default="")

[seen_motds]
	__many__ = string(default="")

[trusted_certs]
	__many__ = string(default="")

[ui]
	play_sounds = boolean(default=True)

[addon_update]
	# The last version we've already downloaded+installed (or tried and
	# failed to) via the server's addon_update push - see addon_update.py.
	# This is the *primary* gate on whether to act on a new push, not just a
	# dedup convenience: addonHandler.getCodeAddon().version keeps reporting
	# the old version until NVDA is restarted to complete a pending install,
	# so comparing only against the installed version would re-download and
	# re-install the same update on every reconnect.
	last_handled_version = string(default="")
	last_handled_failed = boolean(default=False)
	# Opt-in only, off by default - self-reported on 'join' (like
	# client_version) so the server can push the rolling nightly build to
	# this connection instead of the stable one. See settings_panel.py's
	# checkbox and server.py's User.allow_beta_updates.
	allow_beta_updates = boolean(default=False)
""")
def get_config():
	global _config
	if not _config:
		path = os.path.abspath(os.path.join(globalVars.appArgs.configPath, CONFIG_FILE_NAME))
		_config = configobj.ConfigObj(infile=path, configspec=configspec, create_empty=True)
		val = validate.Validator()
		_config.validate(val, copy=True)
		# Always enforce hostname as the session name (key)
		_config['controlserver']['key'] = socket.gethostname()
	return _config

def get_admin_token_for_address(address: str) -> str:
	"""Look up a stored admin token for a given "host:port" address, falling
	back to the legacy single-token slot if nothing server-specific is stored.
	Shared by the admin login dialog and the automatic post-reconnect re-auth
	in client.py, so both agree on where a token for a server lives."""
	config = get_config()
	token = config.get('admin_tokens', {}).get(address)
	if not token:
		token = config['controlserver'].get('admin_token', '')
	return token

def minify_config(parent_window):
	"""Identifies and removes unused configuration keys after asking the user."""
	import wx
	import gui
	conf = get_config()
	spec = conf.configspec
	to_delete = [] # List of (section_path, key_or_section_name)

	def find_extra(config_section, spec_section, path=[]):
		# Check for extra scalars
		for key in config_section.scalars:
			if key not in spec_section.scalars and '__many__' not in spec_section.scalars:
				to_delete.append((path, key))
		
		# Check for extra sections
		for section_name in config_section.sections:
			if section_name not in spec_section.sections:
				if '__many__' in spec_section.sections:
					continue
				else:
					to_delete.append((path, section_name))
			else:
				find_extra(config_section[section_name], spec_section[section_name], path + [section_name])

	find_extra(conf, spec)
	
	if not to_delete:
		return

	# Format the list for the user display
	items_text = "\n".join([f"{'.'.join(p) + '.' if p else ''}{k}" for p, k in to_delete])
	
	def do_minify():
		msg = _("The following unused or old configuration entries were found and can be removed:\n\n{items}\n\nDo you want to delete these entries?").format(items=items_text)
		if gui.messageBox(msg, _("Clean up Configuration"), wx.YES_NO | wx.ICON_QUESTION, parent=parent_window) == wx.YES:
			for path, key in to_delete:
				target = conf
				for p in path:
					target = target[p]
				if key in target:
					del target[key]
			conf.write()
			gui.messageBox(_("Configuration cleaned up successfully."), _("Success"), wx.OK | wx.ICON_INFORMATION)

	wx.CallAfter(do_minify)

def migrate_config(parent_window):
	"""Checks if the configuration needs migration and asks the user."""
	import wx
	import gui
	conf = get_config()
	if conf['controlserver']['version'] >= 3.1:
		return

	def do_migration():
		# Check if current config is already "standard" or if it's empty
		is_empty = not conf['controlserver']['host'] or conf['controlserver']['host'] == ""
		is_non_standard = conf['controlserver']['host'] != "danijel0.danijels-computer.de" or not conf['controlserver']['autoconnect']
		
		if is_empty:
			# Just apply defaults silently for fresh installs
			conf['controlserver']['host'] = "danijel0.danijels-computer.de"
			conf['controlserver']['autoconnect'] = True
			conf['controlserver']['version'] = 3.1
			conf.write()
			return

		if is_non_standard:
			msg = _("A new standard configuration for NVDA Remote is available. \n\n"
					"Should the connection to {new_host} be activated and autoconnect enabled? \n"
					"Your current host is: {current_host}").format(
						new_host="danijel0.danijels-computer.de",
						current_host=conf['controlserver']['host']
					)
			if gui.messageBox(msg, _("NVDA Remote Configuration Update"), wx.YES_NO | wx.ICON_QUESTION, parent=parent_window) == wx.YES:
				conf['controlserver']['host'] = "danijel0.danijels-computer.de"
				conf['controlserver']['autoconnect'] = True
				# If we update the host, we also set the key to hostname to be sure
				conf['controlserver']['key'] = socket.gethostname()
				gui.messageBox(_("Configuration successfully updated to new standards."), _("Success"), wx.OK | wx.ICON_INFORMATION)
		
		# Set version to 3.1 anyway to stop asking
		conf['controlserver']['version'] = 3.1
		conf.write()

	wx.CallAfter(do_migration)

def migrate_legacy_token(parent_window, client):
	"""Checks for a legacy admin token and asks the user to migrate it."""
	import wx
	import gui
	conf = get_config()
	legacy_token = conf['controlserver'].get('admin_token', '')
	
	if not legacy_token:
		return

	# If it's already in the new structure, we don't need to ask (or we could, but let's be smart)
	# Check if this token is already assigned to any server
	if any(t == legacy_token for t in conf.get('admin_tokens', {}).values()):
		# Already migrated or manually added
		return

	def do_migration():
		msg = _("New configuration structure\nLegacy admin tokens found. Should they be transferred? In the following screen, you must name the token (e.g., the server address).")
		if gui.messageBox(msg, _("Configuration Migration"), wx.YES_NO | wx.ICON_QUESTION, parent=parent_window) == wx.YES:
			dlg = wx.TextEntryDialog(parent_window, _("Enter a name or server address for this token:"), _("Token Migration"), value=conf['controlserver'].get('host', ''))
			if dlg.ShowModal() == wx.ID_OK:
				name = dlg.GetValue().strip()
				if name:
					if 'admin_tokens' not in conf:
						conf['admin_tokens'] = {}
					conf['admin_tokens'][name] = legacy_token
					# We keep the legacy one for now to avoid losing data if migration fails, 
					# but the UI will now prefer the new structure.
					conf.write()
					gui.messageBox(_("Token successfully transferred."), _("Success"), wx.OK | wx.ICON_INFORMATION)
			dlg.Destroy()
		else:
			# User denied migration, clear legacy token to stop asking
			conf['controlserver']['admin_token'] = ""
			conf.write()
			gui.messageBox(_("Legacy token deleted."), _("Configuration Updated"), wx.OK | wx.ICON_INFORMATION)

	wx.CallAfter(do_migration)

def write_connection_to_config(connection_info: ConnectionInfo):
	"""Writes a connection to the last connected section of the config.
	If the connection is already in the config, move it to the end.
	
	Args:
		connection_info: The ConnectionInfo object containing connection details
	"""
	conf = get_config()
	last_cons = conf['connections']['last_connected']
	address = connection_info.getAddress()
	if address in last_cons:
		conf['connections']['last_connected'].remove(address)
	conf['connections']['last_connected'].append(address)
	conf.write()
