# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import datetime
import os
import subprocess
import tarfile
import unittest
from unittest import mock

import pgconnstr
from ops import model, testing
from pgsql.opslib.pgsql import client

import charm


class TestCharm(unittest.TestCase):
    def setUp(self):
        # Mock pgsql's leader data getter and setter.
        self.leadership_data = {}
        self._patch(client, "_get_pgsql_leader_data", self.leadership_data.copy)
        self._patch(client, "_set_pgsql_leader_data", self.leadership_data.update)

        self.harness = testing.Harness(charm.PostgresqlDataK8SCharm)
        self.addCleanup(self.harness.cleanup)

    def _patch(self, obj, method, *args, **kwargs):
        """Patches the given method and returns its Mock."""
        patcher = mock.patch.object(obj, method, *args, **kwargs)
        mock_patched = patcher.start()
        self.addCleanup(patcher.stop)

        return mock_patched

    def _add_relation(self, relation_name, relator_name, relation_data):
        """Adds a relation to the charm."""
        relation_id = self.harness.add_relation(relation_name, relator_name)
        self.harness.add_relation_unit(relation_id, "%s/0" % relator_name)

        self.harness.update_relation_data(relation_id, relator_name, relation_data)
        return relation_id

    @mock.patch("subprocess.Popen")
    def test_database_relation(self, mock_popen):
        """Test for the PostgreSQL relation."""
        # Setting the leader will allow the related PostgreSQL charm to write relation data.
        self.harness.set_leader(True)
        self.harness.begin_with_initial_hooks()

        # Join a PostgreSQL charm. A database relation joined is then triggered. The database
        # name should be set in the event.database. Without it, we can't continue on the
        # master changed event.
        dummy_url = "ima/dummy.tar"
        self.harness.update_config({"db-name": "foo.lish", "sql-dump-url": dummy_url})

        rel_id = self._add_relation("db-admin", "postgresql-charm", {})

        # Check that the event.database is set.
        relation = self.harness.model.get_relation("db-admin")
        self.assertEqual(
            relation.data[self.harness.charm.app]["database"], self.harness.charm.config["db-name"]
        )

        # The status should still be Blocked since we don't have any relation data.
        expected_status = model.BlockedStatus("Waiting for database relation.")
        self.assertEqual(self.harness.model.unit.status, expected_status)

        # Setup mocks and update the relation data with a PostgreSQL connection string.
        self._patch(charm.requests, "get")
        mock_open = self._patch(charm, "open", mock.mock_open(read_data=""), create=True)
        self._patch(charm.tarfile, "is_tarfile")
        mock_tarfile_open = self._patch(charm.tarfile, "open")

        # Consider it a .tar file.
        mock_tarfile_open.side_effect = tarfile.ReadError("Expected error.")
        connection_url = "host=foo.lish port=5432 dbname=foo.lish user=someuser password=somepass"
        rel_data = {
            "database": self.harness.charm.config["db-name"],
            "master": connection_url,
        }
        self.harness.update_relation_data(rel_id, "postgresql-charm", rel_data)

        mock_open.assert_has_calls(
            [mock.call("/tmp/dummy.tar", "wb"), mock.call("/tmp/dummy.tar", "r")], any_order=True
        )
        mock_tarfile_open.assert_called_once()

        # Check that pg_restore is called.
        conn = pgconnstr.ConnectionString(connection_url)
        user = self.harness.charm.config["db-user"]
        cmd = ["pg_restore", "--dbname", conn.uri, "--clean", "--no-owner", "--role", user]
        mock_f = mock_open.return_value.__enter__.return_value
        mock_popen.assert_called_with(cmd, stdin=mock_f, stdout=subprocess.PIPE)
        self.assertNotEqual(self.harness.charm._stored.last_update, 0)
        self.assertEqual(self.harness.model.unit.status, model.ActiveStatus())

    @mock.patch("subprocess.Popen")
    def test_on_upgrade(self, mock_popen):
        self.harness.begin_with_initial_hooks()

        cmd_update = ["apt", "update"]
        cmd_install = ["apt", "install", "-y", "postgresql-client"]
        mock_calls = [
            mock.call(cmd_update, stdout=subprocess.PIPE),
            mock.call(cmd_install, stdout=subprocess.PIPE),
        ]

        # Assert initial calls from on_install hook.
        mock_popen.assert_has_calls(mock_calls, any_order=True)
        mock_popen.reset_mock()

        # Trigger upgrade and make assertions.
        self.harness.charm.on.upgrade_charm.emit()
        mock_popen.assert_has_calls(mock_calls, any_order=True)

    @mock.patch("subprocess.Popen")
    def test_config_changed(self, mock_popen):
        """Test for the config updated hook."""
        # We don't have a PostgreSQL charm, so the charm should be in Blocked Status.
        self.harness.begin_with_initial_hooks()
        self.harness.update_config({"db-name": "foo.lish"})

        # no SQL dump URL configured, the charm should be Blocked.
        expected_status = model.BlockedStatus("No sql-dump-url (dump.tar.gz) configured.")
        self.assertEqual(self.harness.model.unit.status, expected_status)

        # Configure sql-dump-url, it shouldn't block on it anymore.
        self.harness.update_config({"sql-dump-url": "ima/dummy.tar"})
        expected_status = model.BlockedStatus("Waiting for database relation.")
        self.assertEqual(self.harness.model.unit.status, expected_status)

        # Setup mocks and join a PostgreSQL charm with the relation data containing a connection
        # string. The charm should be in Blocked status if requests.get failed.
        self.harness.set_leader(True)
        mock_get = self._patch(charm.requests, "get")
        mock_get.side_effect = Exception("Expected exception")
        connection_url = "host=foo.lish port=5432 dbname=foo.lish user=someuser password=somepass"
        rel_data = {
            "database": self.harness.charm.config["db-name"],
            "master": connection_url,
        }
        self._add_relation("db-admin", "postgresql-charm", rel_data)

        # The charm should be in Blocked status because requests.get failed.
        expected_status = model.BlockedStatus(
            "Encountered error while getting SQL dump. Check juju logs for more details."
        )
        self.assertEqual(self.harness.model.unit.status, expected_status)

        # requests.get won't fail anymore. Trigger an update.
        mock_get.side_effect = None
        mock_open = self._patch(charm, "open", mock.mock_open(read_data=""), create=True)
        self._patch(charm.tarfile, "open")
        self.harness.update_config({"sql-dump-url": "dummy.tar"})

        conn = pgconnstr.ConnectionString(connection_url)
        user = self.harness.charm.config["db-user"]
        cmd = ["pg_restore", "--dbname", conn.uri, "--clean", "--no-owner", "--role", user]

        mock_f = mock_open.return_value.__enter__.return_value
        mock_popen.assert_called_with(cmd, stdin=mock_f, stdout=subprocess.PIPE)
        self.assertNotEqual(self.harness.charm._stored.last_update, 0)
        self.assertEqual(self.harness.model.unit.status, model.ActiveStatus())

        # Trigger an update status event, and make sure that pg_restore is not called again,
        # as the refresh-period is 0 (disabled) by default.
        mock_popen.reset_mock()
        self.harness.charm.on.update_status.emit()

        mock_popen.assert_not_called()

        # Update the refresh-period config option, but not enough time has passed yet.
        self.harness.update_config({"refresh-period": 10})
        mock_popen.assert_not_called()

        # 15 minutes have passed since the last update. The database should be updated again.
        now = datetime.datetime.utcnow()
        ago_15m = now - datetime.timedelta(minutes=15)
        self.harness.charm._stored.last_update = ago_15m.timestamp()

        self.harness.charm.on.update_status.emit()
        mock_popen.assert_called_with(cmd, stdin=mock_f, stdout=subprocess.PIPE)

    @mock.patch("tarfile.open")
    @mock.patch("tarfile.is_tarfile")
    @mock.patch("requests.get")
    def test_fetch_dump_file(self, mock_get, mock_is_tarfile, mock_tarfile_open):
        self.harness.begin_with_initial_hooks()
        mock_open = self._patch(charm, "open", mock.mock_open(read_data=""), create=True)

        # Check that an exception is raised if it's not a tar file.
        dump_url = "https://foo.lish/dump.tar"
        mock_is_tarfile.return_value = False
        self.assertRaises(
            charm.PostgresqlDataK8sError, self.harness.charm._fetch_dump_file, dump_url
        )
        mock_get.assert_called_once_with(dump_url)
        mock_open.assert_called_once_with(os.path.join("/tmp", "dump.tar"), "wb")
        mock_open.return_value.write.assert_called_once_with(mock_get.return_value.content)

        # Check that it will return the unextracted tar if it's not a gz archive.
        mock_is_tarfile.return_value = True
        mock_tarfile_open.side_effect = tarfile.ReadError("Expected error.")
        mock_tar = mock_tarfile_open.return_value
        file_path = self.harness.charm._fetch_dump_file(dump_url)
        self.assertEqual(os.path.join("/tmp", "dump.tar"), file_path)
        mock_tar.close.assert_not_called()

        # Check that it will raise an exception if it's an empty archive.
        mock_tarfile_open.side_effect = None
        mock_tar = mock_tarfile_open.return_value
        mock_tar.getnames.return_value = []
        self.assertRaises(
            charm.PostgresqlDataK8sError, self.harness.charm._fetch_dump_file, dump_url
        )
        mock_tar.close.assert_has_calls([mock.call()] * 2)

        # Check that the archive is extracted and that the right path is returned.
        mock_tar.getnames.return_value = ["dump.sql"]
        mock_tar.close.reset()
        file_path = self.harness.charm._fetch_dump_file(dump_url)
        self.assertEqual(os.path.join("/tmp", mock_tar.getnames.return_value[0]), file_path)
        mock_tar.close.assert_has_calls([mock.call()] * 2)

    @mock.patch("tarfile.open")
    def test_is_gz_archive(self, mock_tarfile_open):
        self.harness.begin_with_initial_hooks()
        self.assertTrue(self.harness.charm._is_gz_archive(mock.sentinel.file_path))

        mock_tarfile_open.side_effect = tarfile.ReadError("Expected error")
        self.assertFalse(self.harness.charm._is_gz_archive(mock.sentinel.file_path))
