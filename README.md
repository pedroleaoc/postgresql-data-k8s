# PostgreSQL Data Injector Charm (Kubernetes)

## Description

This charm can be used to import data into a related PostgreSQL charm, and optionally refresh that data periodically (typically useful for public instances).

## Usage

To deploy this charm, simply run:

```bash
juju deploy ./postgresql-data-k8s_ubuntu-20.04-amd64.charm --resource noop-image=google/pause --resource sql-dump-file=dump.tar
```

This charm will require the database to update and the user which will own the data contained in the SQL dump (otherwise, Permission Denied errors will occur when another user tries to access that data):

```bash
juju config postgresql-data-k8s db-name=somedb db-user=someuser
```

This Charm needs to be related to a ``postgresql-k8s`` charm using the ``db-admin`` relation. The following commands will deploy a new ``postgresql-k8s`` charm and relate it to the ``postgresql-data-k8s`` charm:

```bash
juju deploy postgresql-k8s
juju relate postgresql-k8s:db-admin postgresql-data-k8s:db-admin
```

After a few moments, and if there were no issues encountered, the ``postgresql-data-k8s`` charm should become Active.

By default, the ``postgresql-data-k8s`` charm will not reexecute the provided SQL dump. However, it can be configured to periodically reexecute it, rewriting any existing data. This can be done by setting the ``refresh-period`` config option (minutes):

```bash
juju config postgresql-data-k8s refresh-period=60
```

## Relations

This charm requires an ``db-admin`` relation, typically provided by the ``postgresql-k8s`` charm.

## OCI Images

This Charm does not use a Workload Container, how an OCI image is still required to deploy the charm. Any noop image can be used; but it is recommended to use a pause image (``google/pause``).

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and `CONTRIBUTING.md` for developer guidance.
