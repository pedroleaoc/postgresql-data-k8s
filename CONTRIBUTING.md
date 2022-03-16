# PostgreSQL Data Injector Charm (Kubernetes) developer guide

## Prerequisites

In order to start developing and contributing to the PostgreSQL Data Injector Charm (Kubernetes), make sure you have an environment in which you can deploy it. If you do not have one, you can create a Microk8s environment:

```bash
sudo snap install microk8s --classic
sudo snap alias microk8s.kubectl kubectl
sudo usermod -a -G microk8s $USER
sudo chown -f -R $USER ~/.kube
newgrp microk8s

microk8s enable dns storage ingress
microk8s status --wait-ready
```

Make sure that ``microk8s status --wait-ready`` shows that ``dns``, ``storage``, and ``ingress`` have been enabled.

Next, you are going to need Juju installed, a Juju Controller bootstrapped, and a Juju Model created:

```bash
sudo snap install juju --classic
juju bootstrap microk8s test-controller
juju add-model test-model
```

In addition to that, you will need to install Charmcraft, which we'll use to build the Charm. For more information on how to install Charmcraft, check the Charmcraft section of the [dev-setup](https://juju.is/docs/sdk/dev-setup)

## Developing

After you've created the necessary changes and making sure that the [unit tests](#Testing) pass, run the following command to build the charm:

```bash
charmcraft pack
```

When building the Charm for the first time, it will take a bit longer, but subsequent builds will be significantly faster.

If there are any errors during building check the log file mentioned by Charmcraft. If you have added a new dependency in ``requirements.txt`` that is dependent on another package being installed, add that dependency in ``charmcraft.yaml`` in the ``build-packages`` section. For example, some python packages might require ``python3-dev`` to be installed. In that case, we add the following in ``charmcraft.yaml``:

```
parts:
  charm:
    build-packages:
      - python3-dev
```

If there are any other issues, ``charmcraft clean`` might help.

After the charm has been built, you will be able to find it in the local folder. You can then deploy it with the command:

```bash
juju deploy ./postgresql-data-k8s_ubuntu-20.04-amd64.charm --resource noop-image=google/pause --resource sql-dump-file=dump.tar
```

If it was already deployed, you can simply refresh it:

```bash
juju refresh --path=./postgresql-data-k8s_ubuntu-20.04-amd64.charm
```

Doing a ``juju refresh`` / ``juju upgrade-charm`` has the benefit of keeping any configurations or relations added previously.

## Testing

Any new functionality added will have to be covered through unit tests. After writing the necessary unit tests, you can run them with:

```bash
tox -e unit
```

Before pushing the local changes and submitting a Pull Request, make sure that your code is properly formatted and that there are no linting errors:

```bash
tox -e fmt
tox -e lint
```
