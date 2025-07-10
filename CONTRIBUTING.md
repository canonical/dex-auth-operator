# Contributing

## Overview

This document outlines the processes and practices recommended for contributing enhancements to `dex-auth`.

## Talk to us First

Before developing enhancements to this charm, you should [open an issue](/../../issues) explaining your use case. If you would like to chat with us about your use-cases or proposed implementation, you can reach us at [MLOps Mattermost public channel](https://chat.charmhub.io/charmhub/channels/mlops-documentation) or on [Discourse](https://discourse.charmhub.io/).

## Pull Requests

Please help us out in ensuring easy to review branches by rebasing your pull request branch onto the `main` branch. This also avoids merge commits and creates a linear Git commit history.

All pull requests require review before being merged. Code review typically examines:
  - code quality
  - test coverage
  - user experience for Juju administrators of this charm.

## Recommended Knowledge

Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library will help you a lot when working on new features or bug fixes.

## Build Charm

To build `dex-auth` run:

```shell
charmcraft pack
```

## Developing

You can use the environments created by `tox` for development. For example, to load the `unit` environment into your shell, run:

```shell
tox --notest -e unit
source .tox/unit/bin/activate
```

### Testing

Use tox for testing. For example to test the `integration` environment, run:

```shell
tox -e integration
```

See `tox.ini` for all available environments.

### Deploy

```bash
# Create a model
juju add-model dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
# Deploy the charm
juju deploy ./dex-auth_ubuntu@24.04-amd64.charm \
    --resource oci-image=$(yq '.resources."oci-image"."upstream-source"' metadata.yaml)

## Canonical Contributor Agreement

Canonical welcomes contributions to this charm. Please check out our [contributor agreement](https://ubuntu.com/legal/contributors) if you're interested in contributing.

## How to Manage Python Dependencies and Environments


### Prerequisites

`tox` is the only tool required locally, as `tox` internally installs and uses `poetry`, be it to manage Python dependencies or to run `tox` environments. To install it: `pipx install tox`.

Optionally, `poerty` can be additionally installed independently just for the sake of running Python commands locally outside of `tox` during debugging/development. To install it: `pipx install poetry`.


### Updating Dependencies

To add/update/remove any dependencies and/or to upgrade Python, simply:

1. add/update/remove such dependencies to/in/from the desired group(s) below `[tool.poetry.group.<your-group>.dependencies]` in `pyproject.toml`, and/or upgrade Python itself in `requires-python` under `[project]`

    _⚠️ dependencies for the charm itself are also defined as dependencies of a dedicated group called `charm`, specifically below `[tool.poetry.group.charm.dependencies]`, and not as project dependencies below `[project.dependencies]` or `[tool.poetry.dependencies]` ⚠️_

2. run `tox -e update-requirements` to update the lock file

    by this point, `poerty`, through `tox`, will let you know if there are any dependency conflicts to solve.

3. optionally, if you also want to update your local environment for running Python commands/scripts yourself and not through tox, see [Running Python Environments](#running-python-environments) below


### Running `tox` Environments

To run `tox` environments, either locally for development or in CI workflows for testing, ensure to have `tox` installed first and then simply run your `tox` environments natively (e.g.: `tox -e lint`). `tox` will internally first install `poetry` and then rely on it to install and run its environments.


### Running Python Environments

To run Python commands locally for debugging/development from any environments built from any combinations of dependency groups without relying on `tox`:
1. ensure you have `poetry` installed
2. install any required dependency groups: `poetry install --only <your-group-a>,<your-group-b>` (or all groups, if you prefer: `poetry install --all-groups`)
3. run Python commands via poetry: `poetry run python3 <your-command>`
