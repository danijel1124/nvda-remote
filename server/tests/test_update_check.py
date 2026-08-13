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


if __name__ == '__main__':
	unittest.main()
