# PostgreSQL Data Injector Charm (Kubernetes)

## Description

This charm can be used to import data into a related PostgreSQL charm, and optionally refresh that data periodically (typically useful for public instances).

## Usage

To deploy this charm, simply run:

```bash
juju deploy postgresql-data-k8s --channel=edge
```

This charm will require the database to update and the user which will own the data contained in the SQL dump (otherwise, Permission Denied errors will occur when another user tries to access that data):

```bash
juju config postgresql-data-k8s db-name=somedb db-user=someuser
```

Next, the charm will require the SQL dump URL which will be injected into the database. The SQL dump should be a tar archive instead of a plaintext dump, in order to avoid any potential errors while restoring the data into the database. The charm also supports ``.tar.gz`` SQL dumps.

For more information about how to generate the tar SQL dump, see [here](https://www.postgresql.org/docs/current/app-pgdump.html)

After obtaining the SQL dump URL, the charm can be configured to use it:

```bash
juju config postgresql-data-k8s sql-dump-url=the-url-of-the-dump
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
