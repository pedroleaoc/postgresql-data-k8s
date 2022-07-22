# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import datetime
import subprocess
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
        dummy_url = "ima/dummy.tar.gz"
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
        mock_gzip_open = self._patch(charm.gzip, "open", mock.mock_open(read_data=""), create=True)
        connection_url = "host=foo.lish port=5432 dbname=foo.lish user=someuser password=somepass"
        rel_data = {
            "database": self.harness.charm.config["db-name"],
            "master": connection_url,
        }
        self.harness.update_relation_data(rel_id, "postgresql-charm", rel_data)

        mock_open.assert_has_calls(
            [
                mock.call("/tmp/dummy.tar.gz", "wb"),
                mock.call("/tmp/dummy.tar", "wb"),
                mock.call("/tmp/dummy.tar", "r"),
            ],
            any_order=True,
        )
        mock_gzip_open.assert_called_once_with("/tmp/dummy.tar.gz", "rb")

        # Check that pg_restore is called.
        conn = pgconnstr.ConnectionString(connection_url)
        user = self.harness.charm.config["db-user"]
        cmd = ["pg_restore", "--dbname", conn.uri, "--clean", "--no-owner", "--role", user]
        mock_f = mock_open.return_value.__enter__.return_value
        mock_popen.assert_called_with(cmd, stdin=mock_f, stdout=subprocess.PIPE)
        self.assertNotEqual(self.harness.charm._stored.last_update, 0)
        self.assertEqual(self.harness.model.unit.status, model.ActiveStatus())

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
