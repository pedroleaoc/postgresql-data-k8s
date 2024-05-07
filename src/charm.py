#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Module defining the PostgreSQL Data Injector Charm."""

import logging
import os
import subprocess
import tarfile
from datetime import datetime

import pgconnstr
import requests
from ops import charm, framework, lib, main, model

logger = logging.getLogger(__name__)

pgsql = lib.use("pgsql", 1, "postgresql-charmers@lists.launchpad.net")


class PostgresqlDataK8sError(Exception):
    """Custom exception class used to raise errors in the project."""

    pass


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
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade)
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
        self._install_client()

    def _on_upgrade(self, _):
        """Handles the charm upgrade hook.

        This will install the postgresql-client dependency, which contains the pg_restore binary
        used by this charm.

        If a Pod is respawned, the install and start hooks are not triggered, but this one is.
        We need this in order to ensure that the dependency exists.
        """
        self._install_client()

    def _install_client(self):
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
        sql_dump_url = self.model.config["sql-dump-url"]
        if sql_dump_url == "":
            self.unit.status = model.BlockedStatus("No sql-dump-url (dump.tar.gz) configured.")
            return

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
        logger.info("Starting the database update.")
        self.unit.status = model.WaitingStatus("Updating the database.")

        # Get the SQL dump file resource we're given and run pg_restore using it.
        try:
            dump_file = self._fetch_dump_file(sql_dump_url)
        except Exception as ex:
            logger.error(ex)
            self.unit.status = model.BlockedStatus(
                "Encountered error while getting SQL dump. Check juju logs for more details."
            )
            return

        logger.info("Updating the database using the SQL dump.")
        with open(dump_file, "r") as f:
            user = self.model.config["db-user"]
            cmd = ["pg_restore", "--dbname", conn.uri, "--clean", "--no-owner", "--role", user]
            proc = subprocess.Popen(cmd, stdin=f, stdout=subprocess.PIPE)
            proc.wait()

        # We're done. Save the current timestamp, and set the Charm to the Active status.
        self._stored.last_update = now.timestamp()
        logger.info("Updated database.")
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

    def _fetch_dump_file(self, dump_url):
        """Downloads the given SQL Dump URL and returns its path.

        If the downloaded file is a .gz file, this method will extract it and return the new
        path instead.
        """
        # We always redownload the file, we can't make any assumption that it hasn't changed.
        logger.debug("Fetching SQL dump from %s", dump_url)
        response = requests.get(dump_url)

        filename = dump_url.rsplit("/", 1)[-1]
        file_path = os.path.join("/tmp", filename)
        with open(file_path, "wb") as dump_file:
            dump_file.write(response.content)

        if not tarfile.is_tarfile(file_path):
            raise PostgresqlDataK8sError("Given dump URL is not a .tar or .tar.gz file.")

        if not self._is_gz_archive(file_path):
            return file_path

        # If it's a .gz file, we need to extract it, since pg_restore expects a tar file.
        logger.debug("Decompressing .gz file.")
        targz = tarfile.open(file_path)
        names = targz.getnames()
        if not names:
            targz.close()
            raise PostgresqlDataK8sError("No file names found in the given dump URL archive.")

        targz.extractall("/tmp/")
        targz.close()

        new_file_path = os.path.join("/tmp", names[0])
        return new_file_path

    def _is_gz_archive(self, file_path):
        """Returns whether the given file_path is .gz file or not."""
        try:
            tar = tarfile.open(file_path, "r:gz")
            tar.close()
            return True
        except tarfile.ReadError:
            return False


if __name__ == "__main__":
    main.main(PostgresqlDataK8SCharm)
