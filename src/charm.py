#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Module defining the PostgreSQL Data Injector Charm."""

import logging
import subprocess
from datetime import datetime

import pgconnstr
from ops import charm, framework, lib, main, model

logger = logging.getLogger(__name__)

pgsql = lib.use("pgsql", 1, "postgresql-charmers@lists.launchpad.net")


class PostgresqlDataK8SCharm(charm.CharmBase):
    """A Juju Charm for injecting data into a related PostgreSQL database.

    This charm can be related to a postgresql-k8s charm (using the db-admin relation), and
    this charm will execute the SQL tar dump given as a resource.

    If the refresh-period (minutes) is non-zero, this charm will reexecute the SQL tar dump after
    that number of minutes have passed since the last update.

    This charm can be configured with the db-user config option, which will be the new owner of the
    newly created data (otherwise, consumers of that database will encounter Permission Denied
    errors).
    """

    _stored = framework.StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self._stored.set_default(last_update=0)

        # PostgreSQL relation hooks:
        self.db = pgsql.PostgreSQLClient(self, "db-admin")
        self.framework.observe(self.db.on.database_relation_joined, self._on_db_relation_joined)
        self.framework.observe(self.db.on.database_relation_broken, self._on_db_relation_broken)
        self.framework.observe(self.db.on.database_changed, self._on_db_changed)

        # General hooks:
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)

    def _on_db_relation_joined(self, event: pgsql.DatabaseRelationJoinedEvent):
        """Handles the PostgreSQL database relation joined event.

        When joining a relation with the PostgreSQL charm, we can request it credentials for a
        specific database with a name chosen by us.
        """
        event.database = self.config["db-name"]

    def _on_db_relation_broken(self, event: pgsql.DatabaseRelationBrokenEvent):
        """Handles the PostgreSQL database relation broken event.

        When the relation is broken, it means that we can no longer use the database we had
        the credentials for.
        """
        # We need to reset last_update, so on the next join we can update it as soon as possible.
        self._stored.last_update = 0

    def _on_db_changed(self, event: pgsql.MasterChangedEvent):
        """Handles the PostgreSQL database relation update event.

        This event is generated whenever the PostgreSQL charm updates our relation with it,
        including when it has provisioned credentials for the database we requested.
        """
        # Check if we received credentials for the database we've asked for.
        if event.database != self.config["db-name"]:
            return

        if event.master is None:
            # Thre is no connection data.
            return

        self._update_database()

    def _on_install(self, _):
        """Handles the charm install hook.

        This will install the postgresql-client dependency, which contains the pg_restore binary
        used by this charm.
        """
        proc = subprocess.Popen(["apt", "update"], stdout=subprocess.PIPE)
        proc.wait()

        proc = subprocess.Popen(
            ["apt", "install", "-y", "postgresql-client"], stdout=subprocess.PIPE
        )
        proc.wait()

    def _on_config_changed(self, _):
        """Refreshes the service config.

        If the config options have changed, the database will be updated if needed (at least
        refresh-period minutes have passed since the last update).
        """
        self._update_database()

    def _on_update_status(self, _):
        """Handles the charm update status event.

        The charm status is updated periodically. If enough time passed since the last update,
        reupdate the related database.
        """
        self._update_database()

    def _update_database(self):
        """Updates the related database.

        This requires the db-admin relation to be satisfied. When a postgresql-k8s charm is
        joined for the first time, this will update the database with the given SQL tar dump
        and with the configured db-user.

        On subsequent calls, the database will only be updated if the refresh-period config
        option is non-zero, and refresh-period minutes have passed since the last update.
        """
        conn = self._get_db_conn()
        if not conn:
            self.unit.status = model.BlockedStatus("Waiting for database relation.")
            return

        # If the refresh-period config option is set to 0, we don't need to refresh the database,
        # we only need to update the database once.
        first_time = self._stored.last_update == 0
        if self.config["refresh-period"] == 0 and not first_time:
            return

        # Check if enough time passed since the last update.
        last_update = datetime.fromtimestamp(self._stored.last_update)
        now = datetime.utcnow()
        delta = now - last_update
        if delta.seconds / 60 < self.config["refresh-period"]:
            # It's not our time to refresh the database.
            return

        # We need to update the database.
        self.unit.status = model.WaitingStatus("Updating the database.")

        # Get the SQL dump file resource we're given and run pg_restore using it.
        dump_file = self.model.resources.fetch("sql-dump-file")
        with open(dump_file, "r") as f:
            user = self.model.config["db-user"]
            cmd = ["pg_restore", "--dbname", conn.uri, "--clean", "--no-owner", "--role", user]
            proc = subprocess.Popen(cmd, stdin=f, stdout=subprocess.PIPE)
            proc.wait()

        # We're done. Save the current timestamp, and set the Charm to the Active status.
        self._stored.last_update = now.timestamp()
        self.unit.status = model.ActiveStatus()

    def _get_db_conn(self) -> pgconnstr.ConnectionString:
        """Returns the postgresql database connection details.

        Returns a ConnectionString containing the admin connection details.
        """
        db_relation = self.model.get_relation("db-admin")
        if db_relation and db_relation.units and db_relation.data[db_relation.app].get("master"):
            conn_string = db_relation.data[db_relation.app]["master"]
            return pgconnstr.ConnectionString(conn_string)

        return None


if __name__ == "__main__":
    main.main(PostgresqlDataK8SCharm)
