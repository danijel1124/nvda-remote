"""Tests for update_check.py - the server's own self-update-check logic
(distinct from server.py's addon_update, which pushes client updates).

Plain unittest (not twisted.trial) since update_check.py has no Twisted
dependency by design - these run under both `python -m unittest` and
`python -m twisted.trial`.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import update_check  # noqa: E402


def _fake_response(payload):
	class _Resp:
		def __enter__(self):
			return self

		def __exit__(self, *a):
			return False

		def read(self):
			return json.dumps(payload).encode('utf-8')

	return _Resp()


class UpdateCheckTests(unittest.TestCase):
	def setUp(self):
		self.data_dir = tempfile.mkdtemp()

	def tearDown(self):
		shutil.rmtree(self.data_dir, ignore_errors=True)

	# --- version parsing ---

	def test_parse_version_basic(self):
		self.assertEqual(update_check._parse_version("1.2.3"), (1, 2, 3))

	def test_parse_version_tolerates_non_numeric(self):
		self.assertEqual(update_check._parse_version("1.2.rc1"), (1, 2, 0))

	# --- interval config ---

	def test_default_interval_is_24_hours_when_no_config_file(self):
		self.assertEqual(update_check.get_configured_interval_hours(self.data_dir), 24)

	def test_reads_configured_interval(self):
		with open(os.path.join(self.data_dir, update_check.CONFIG_FILENAME), 'w') as f:
			json.dump({'update_check_interval_hours': 6}, f)
		self.assertEqual(update_check.get_configured_interval_hours(self.data_dir), 6)

	def test_malformed_config_file_falls_back_to_default(self):
		with open(os.path.join(self.data_dir, update_check.CONFIG_FILENAME), 'w') as f:
			f.write("not json")
		self.assertEqual(update_check.get_configured_interval_hours(self.data_dir), 24)

	def test_non_positive_interval_falls_back_to_default(self):
		with open(os.path.join(self.data_dir, update_check.CONFIG_FILENAME), 'w') as f:
			json.dump({'update_check_interval_hours': 0}, f)
		self.assertEqual(update_check.get_configured_interval_hours(self.data_dir), 24)

	# --- due-ness ---

	def test_check_is_due_when_never_checked_before(self):
		self.assertTrue(update_check.is_check_due(self.data_dir))

	@mock.patch('update_check.urllib.request.urlopen')
	def test_check_not_due_immediately_after_a_check(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'server-v1.0.0', 'html_url': 'https://example.org/server-v1.0.0'},
		])
		update_check.check_for_update("1.0.0", self.data_dir)
		self.assertFalse(update_check.is_check_due(self.data_dir))

	def test_check_due_again_after_interval_elapses(self):
		with open(os.path.join(self.data_dir, update_check.STATE_FILENAME), 'w') as f:
			json.dump({'checked_at': 0.0}, f)  # effectively "a long time ago"
		self.assertTrue(update_check.is_check_due(self.data_dir))

	# --- check_for_update ---

	@mock.patch('update_check.urllib.request.urlopen')
	def test_reports_update_available_for_newer_release(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'server-v1.2.0', 'html_url': 'https://example.org/server-v1.2.0'},
			{'tag_name': 'v3.2.2', 'html_url': 'https://example.org/v3.2.2'},  # client tag - must be ignored
		])
		result = update_check.check_for_update("1.0.0", self.data_dir)
		self.assertTrue(result['update_available'])
		self.assertEqual(result['latest_version'], '1.2.0')
		self.assertEqual(result['url'], 'https://example.org/server-v1.2.0')
		self.assertIsNone(result['error'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_reports_up_to_date(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'server-v1.0.0', 'html_url': 'https://example.org/server-v1.0.0'},
		])
		result = update_check.check_for_update("1.0.0", self.data_dir)
		self.assertFalse(result['update_available'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_never_reports_older_release_as_an_update(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'server-v0.9.0', 'html_url': 'https://example.org/server-v0.9.0'},
		])
		result = update_check.check_for_update("1.0.0", self.data_dir)
		self.assertFalse(result['update_available'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_ignores_non_server_tags(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'v3.2.2', 'html_url': 'https://example.org/v3.2.2'},
		])
		result = update_check.check_for_update("1.0.0", self.data_dir)
		self.assertIsNone(result['latest_version'])
		self.assertFalse(result['update_available'])

	@mock.patch('update_check.urllib.request.urlopen', side_effect=OSError("network unreachable"))
	def test_network_failure_does_not_raise(self, mock_urlopen):
		result = update_check.check_for_update("1.0.0", self.data_dir)
		self.assertIsNotNone(result['error'])
		self.assertFalse(result['update_available'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_result_is_persisted_to_state_file(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'server-v1.0.0', 'html_url': 'https://example.org/server-v1.0.0'},
		])
		update_check.check_for_update("1.0.0", self.data_dir)
		persisted = update_check.read_last_check(self.data_dir)
		self.assertIsNotNone(persisted)
		self.assertEqual(persisted['current_version'], "1.0.0")

	@mock.patch('update_check.urllib.request.urlopen')
	def test_log_callback_receives_a_message(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'server-v1.2.0', 'html_url': 'https://example.org/server-v1.2.0'},
		])
		messages = []
		update_check.check_for_update("1.0.0", self.data_dir, log=messages.append)
		self.assertEqual(len(messages), 1)
		self.assertIn("1.2.0", messages[0])

	# --- check_for_client_update ---

	def _write_addon_release(self, version, url):
		with open(os.path.join(self.data_dir, update_check.ADDON_RELEASE_FILENAME), 'w') as f:
			json.dump({'version': version, 'url': url}, f)

	def _releases_payload(self, *, client_versions=(), server_versions=(), prerelease_client_versions=(), asset=True):
		releases = []
		for v in client_versions:
			releases.append({
				'tag_name': f'v{v}',
				'prerelease': False,
				'draft': False,
				'assets': [{'name': f'remote-{v}.nvda-addon', 'browser_download_url': f'https://example.org/remote-{v}.nvda-addon'}] if asset else [],
			})
		for v in prerelease_client_versions:
			releases.append({
				'tag_name': f'v{v}',
				'prerelease': True,
				'draft': False,
				'assets': [{'name': f'remote-{v}.nvda-addon', 'browser_download_url': f'https://example.org/remote-{v}.nvda-addon'}],
			})
		for v in server_versions:
			releases.append({'tag_name': f'server-v{v}', 'prerelease': False, 'draft': False, 'assets': []})
		return releases

	@mock.patch('update_check.urllib.request.urlopen')
	def test_client_update_writes_addon_release_when_newer(self, mock_urlopen):
		self._write_addon_release("3.2.1", "https://example.org/remote-3.2.1.nvda-addon")
		mock_urlopen.return_value = _fake_response(self._releases_payload(client_versions=["3.2.2"]))
		result = update_check.check_for_client_update(self.data_dir)
		self.assertTrue(result['updated'])
		current = update_check.read_current_addon_release(self.data_dir)
		self.assertEqual(current['version'], "3.2.2")
		self.assertEqual(current['url'], "https://example.org/remote-3.2.2.nvda-addon")

	@mock.patch('update_check.urllib.request.urlopen')
	def test_client_update_does_not_write_when_not_newer(self, mock_urlopen):
		self._write_addon_release("3.2.2", "https://example.org/remote-3.2.2.nvda-addon")
		mock_urlopen.return_value = _fake_response(self._releases_payload(client_versions=["3.2.1", "3.2.2"]))
		result = update_check.check_for_client_update(self.data_dir)
		self.assertFalse(result['updated'])
		current = update_check.read_current_addon_release(self.data_dir)
		self.assertEqual(current['version'], "3.2.2")

	@mock.patch('update_check.urllib.request.urlopen')
	def test_client_update_writes_when_no_prior_addon_release_file(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response(self._releases_payload(client_versions=["3.2.2"]))
		result = update_check.check_for_client_update(self.data_dir)
		self.assertTrue(result['updated'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_client_update_ignores_prerelease_tags(self, mock_urlopen):
		self._write_addon_release("3.2.1", "https://example.org/remote-3.2.1.nvda-addon")
		# A newer-numbered but pre-release tag must never win over an
		# older official one - matches gh release's own "Pre-release"
		# flag semantics (e.g. this repo's real v3.2 tag).
		mock_urlopen.return_value = _fake_response(self._releases_payload(prerelease_client_versions=["9.9.9"]))
		result = update_check.check_for_client_update(self.data_dir)
		self.assertFalse(result['updated'])
		self.assertIsNone(result['latest_version'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_client_update_ignores_server_tags(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response(self._releases_payload(server_versions=["1.0.0"]))
		result = update_check.check_for_client_update(self.data_dir)
		self.assertIsNone(result['latest_version'])
		self.assertFalse(result['updated'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_client_update_skips_release_with_no_addon_asset(self, mock_urlopen):
		self._write_addon_release("3.2.1", "https://example.org/remote-3.2.1.nvda-addon")
		mock_urlopen.return_value = _fake_response(self._releases_payload(client_versions=["3.2.2"], asset=False))
		result = update_check.check_for_client_update(self.data_dir)
		self.assertFalse(result['updated'])
		current = update_check.read_current_addon_release(self.data_dir)
		self.assertEqual(current['version'], "3.2.1")

	@mock.patch('update_check.urllib.request.urlopen', side_effect=OSError("network unreachable"))
	def test_client_update_network_failure_does_not_raise(self, mock_urlopen):
		result = update_check.check_for_client_update(self.data_dir)
		self.assertIsNotNone(result['error'])
		self.assertFalse(result['updated'])

	# --- run_scheduled_checks ---

	@mock.patch('update_check.urllib.request.urlopen')
	def test_run_scheduled_checks_runs_both_server_and_client_checks(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response(self._releases_payload(
			client_versions=["3.2.2"], server_versions=["1.0.0"],
		))
		combined = update_check.run_scheduled_checks("1.0.0", self.data_dir)
		self.assertEqual(combined['server']['current_version'], "1.0.0")
		self.assertEqual(combined['client']['latest_version'], "3.2.2")
		self.assertIsNotNone(update_check.read_last_check(self.data_dir))  # server check persisted
		current = update_check.read_current_addon_release(self.data_dir)
		self.assertEqual(current['version'], "3.2.2")  # client check applied


class ClientBetaUpdateTests(unittest.TestCase):
	"""check_for_client_beta_update - the rolling 'nightly' release, tracked
	separately from the stable channel (data/addon_beta_release.json)."""

	def setUp(self):
		self.data_dir = tempfile.mkdtemp()

	def tearDown(self):
		shutil.rmtree(self.data_dir, ignore_errors=True)

	def _nightly_release(self, build_id, asset=True):
		return {
			'tag_name': 'nightly',
			'prerelease': True,
			'draft': False,
			'assets': (
				[{'name': f'remote-{build_id}.nvda-addon', 'browser_download_url': f'https://example.org/remote-{build_id}.nvda-addon'}]
				if asset else []
			),
		}

	@mock.patch('update_check.urllib.request.urlopen')
	def test_writes_addon_beta_release_from_nightly_tag(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			self._nightly_release('nightly-20260813203214'),
			{'tag_name': 'v3.2.3.1', 'prerelease': False, 'draft': False, 'assets': []},
		])
		result = update_check.check_for_client_beta_update(self.data_dir)
		self.assertTrue(result['updated'])
		self.assertEqual(result['version'], 'nightly-20260813203214')
		with open(os.path.join(self.data_dir, update_check.ADDON_BETA_RELEASE_FILENAME)) as f:
			data = json.load(f)
		self.assertEqual(data['version'], 'nightly-20260813203214')
		self.assertEqual(data['url'], 'https://example.org/remote-nightly-20260813203214.nvda-addon')

	@mock.patch('update_check.urllib.request.urlopen')
	def test_no_nightly_tag_leaves_file_untouched(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			{'tag_name': 'v3.2.3.1', 'prerelease': False, 'draft': False, 'assets': []},
		])
		result = update_check.check_for_client_beta_update(self.data_dir)
		self.assertFalse(result['updated'])
		self.assertIsNone(result['error'])
		self.assertFalse(os.path.exists(os.path.join(self.data_dir, update_check.ADDON_BETA_RELEASE_FILENAME)))

	@mock.patch('update_check.urllib.request.urlopen')
	def test_nightly_tag_without_addon_asset_is_skipped(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			self._nightly_release('nightly-20260813203214', asset=False),
		])
		result = update_check.check_for_client_beta_update(self.data_dir)
		self.assertFalse(result['updated'])
		self.assertIsNone(result['error'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_always_overwrites_on_each_new_nightly_build(self, mock_urlopen):
		"""No 'is this newer' gate here, unlike the stable channel - the
		nightly tag is a single rolling build, always mirror whatever's
		currently there. Client-side (addon_update.py) is what actually
		decides whether to install it."""
		mock_urlopen.return_value = _fake_response([self._nightly_release('nightly-20260813100000')])
		update_check.check_for_client_beta_update(self.data_dir)
		mock_urlopen.return_value = _fake_response([self._nightly_release('nightly-20260813203214')])
		update_check.check_for_client_beta_update(self.data_dir)
		with open(os.path.join(self.data_dir, update_check.ADDON_BETA_RELEASE_FILENAME)) as f:
			data = json.load(f)
		self.assertEqual(data['version'], 'nightly-20260813203214')

	@mock.patch('update_check.urllib.request.urlopen', side_effect=OSError("network unreachable"))
	def test_network_failure_does_not_raise(self, mock_urlopen):
		result = update_check.check_for_client_beta_update(self.data_dir)
		self.assertIsNotNone(result['error'])
		self.assertFalse(result['updated'])

	@mock.patch('update_check.urllib.request.urlopen')
	def test_run_scheduled_checks_includes_client_beta(self, mock_urlopen):
		mock_urlopen.return_value = _fake_response([
			self._nightly_release('nightly-20260813203214'),
			{'tag_name': 'v3.2.3.1', 'prerelease': False, 'draft': False, 'assets': []},
			{'tag_name': 'server-v1.1.0', 'prerelease': False, 'draft': False, 'assets': []},
		])
		combined = update_check.run_scheduled_checks("1.1.0", self.data_dir)
		self.assertEqual(combined['client_beta']['version'], 'nightly-20260813203214')


if __name__ == '__main__':
	unittest.main()
