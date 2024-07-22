#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library for sharing Dex's OIDC configuration with OIDC clients.

This library offers a Python API for providing and requesting information about
Dex's OIDC configuration.
The default relation name is `dex-oidc-config` and it's recommended to use that name,
though if changed, you must ensure to pass the correct name when instantiating the
provider and requirer classes, as well as in `metadata.yaml`.

## Getting Started

### Fetching the library with charmcraft

Using charmcraft you can:
```shell
charmcraft fetch-lib charms.dex_auth.v0.dex_oidc_config


## Using the library as requirer

### Add relation to metadata.yaml
```yaml
requires:
  dex-oidc-config:
    interface: dex-oidc-config
    limit: 1
```

### Instantiate the DexOidcConfigRequirer class in charm.py

```python
from ops.charm import CharmBase
from charms.dex_auth.v0.dex_oidc_config import DexOidcConfigRequirer, DexOidcConfigRelationError

class RequirerCharm(CharmBase):
    def __init__(self, *args):
        self._dex_oidc_config_requirer = DexOidcConfigRequirer(self)
        self.framework.observe(self.on.some_event_emitted, self.some_event_function)
        self.framework.observe(self._dex_oidc_config_requirer.on.update, self.some_event_function)

    def some_event_function():
        # use the getter function wherever the info is needed
        try:
            k8s_svc_info_data = self._dex_oidc_config_requirer.get_data()
        except DexOidcConfigRelationError as error:
            "your error handler goes here"
```

## Using the library as provider

### Add relation to metadata.yaml
```yaml
provides:
  dex-oidc-config:
    interface: dex-oidc-config
```

### Instantiate the DexOidcConfigProvider class in charm.py

```python
from ops.charm import CharmBase
from charms.dex_auth.v0.dex_oidc_config import DexOidcConfigProvider, DexOidcConfigRelationError

class ProviderCharm(CharmBase):
    def __init__(self, *args, **kwargs):
        ...
        self._dex_oidc_config_provider = DexOidcConfigProvider(self)
        self.observe(self.on.some_event, self._some_event_handler)

    def _some_event_handler(self, ...):
        # This will update the relation data bag with the issuer URL 
        try:
            self._dex_oidc_config_provider.send_data(issuer_url)
        except DexOidcConfigRelationError as error:
            "your error handler goes here"
```

Alternatively, if the provider is just broadcasting known data, it can be:

```python
from ops.charm import CharmBase
from charms.dex_auth.v0.dex_oidc_config import DexOidcConfigProvider, DexOidcConfigRelationError

class ProviderCharm(CharmBase):
    def __init__(self, *args, **kwargs):
        ...
        self._dex_oidc_config_provider = DexOidcConfigProvider(self)
```

## Relation data

The data shared by this library is:
* issuer-url: the canonical URL for the issuer, OIDC cliets use this to refer to Dex
"""
import logging
from typing import List, Optional, Union

from ops.charm import CharmBase, RelationEvent
from ops.framework import BoundEvent, EventSource, Object, ObjectEvents
from ops.model import Relation
from pydantic import BaseModel

# The unique Charmhub library identifier, never change it
LIBID = "eb5a471989b246e4977399bc8cf9ae6f"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

# Default relation and interface names. If changed, consistency must be kept
# across the provider and requirer.
DEFAULT_RELATION_NAME = "dex-oidc-config"
DEFAULT_INTERFACE_NAME = "dex-oidc-config"
REQUIRED_ATTRIBUTES = ["issuer-url"]

logger = logging.getLogger(__name__)


class DexOidcConfigRelationError(Exception):
    """Base exception class for any relation error handled by this library."""

    pass


class DexOidcConfigRelationMissingError(DexOidcConfigRelationError):
    """Exception to raise when the relation is missing on either end."""

    def __init__(self):
        self.message = "Missing relation with a Dex OIDC config provider."
        super().__init__(self.message)


class DexOidcConfigRelationDataMissingError(DexOidcConfigRelationError):
    """Exception to raise when there is missing data in the relation data bag."""

    def __init__(self, message):
        self.message = message
        super().__init__(self.message)


class DexOidcConfigUpdatedEvent(RelationEvent):
    """Indicates the Dex OIDC config data was updated."""


class DexOidcConfigEvents(ObjectEvents):
    """Events for the Dex OIDC config library."""

    updated = EventSource(DexOidcConfigUpdatedEvent)


class DexOidcConfigObject(BaseModel):
    """Representation of a Dex OIDC config object.

    Args:
        issuer_url: This is the canonical URL that OIDC clients MUST use to refer to dex.
    """

    issuer_url: str


class DexOidcConfigRequirer(Object):
    """Implement the Requirer end of the Dex OIDC config relation.

    This library emits:
    * DexOidcConfigUpdatedEvent: when data received on the relation is updated.

    Args:
        charm (CharmBase): the provider application
        refresh_events: (list, optional): list of BoundEvents that this manager should handle.
                       Use this to update the data sent on this relation on demand.
        relation_name (str, optional): the name of the relation

    Attributes:
        charm (CharmBase): variable for storing the requirer application
        relation_name (str): variable for storing the name of the relation
    """

    on = DexOidcConfigEvents()

    def __init__(
        self,
        charm: CharmBase,
        refresh_events: Optional[List[BoundEvent]] = None,
        relation_name: Optional[str] = DEFAULT_RELATION_NAME,
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._requirer_wrapper = DexOidcConfigRequirerWrapper(self._charm, self._relation_name)

        self.framework.observe(
            self._charm.on[self._relation_name].relation_changed, self._on_relation_changed
        )

        self.framework.observe(
            self._charm.on[self._relation_name].relation_broken, self._on_relation_broken
        )

        if refresh_events:
            for evt in refresh_events:
                self.framework.observe(evt, self._on_relation_changed)

    def get_data(self) -> DexOidcConfigObject:
        """Return a DexOidcConfigObject."""
        return self._requirer_wrapper.get_data()

    def _on_relation_changed(self, event: BoundEvent) -> None:
        """Handle relation-changed event for this relation."""
        self.on.updated.emit(event.relation)

    def _on_relation_broken(self, event: BoundEvent) -> None:
        """Handle relation-broken event for this relation."""
        self.on.updated.emit(event.relation)


class DexOidcConfigRequirerWrapper(Object):
    """Wrapper for the relation data getting logic.

    Args:
        charm (CharmBase): the requirer application
        relation_name (str, optional): the name of the relation

    Attributes:
        relation_name (str): variable for storing the name of the relation
    """

    def __init__(self, charm, relation_name: Optional[str] = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.relation_name = relation_name

    @staticmethod
    def _validate_relation(relation: Optional[Relation]) -> None:
        """Series of checks for the relation and relation data.

        Args:
            relation (optional, Relation): the relation object to run the checks on.
              This object must always come from a call of get_relation, which
              can either return a Relation object or None.

        Raises:
            DexOidcConfigRelationDataMissingError if data is missing or incomplete
            DexOidcConfigRelationMissingError: if there is no related application
        """
        # Raise if there is no related application
        if not relation:
            raise DexOidcConfigRelationMissingError()

        # Extract remote app information from relation
        remote_app = relation.app
        # Get relation data from remote app
        relation_data = relation.data[remote_app]

        # Raise if there is no data found in the relation data bag
        if not relation_data:
            raise DexOidcConfigRelationDataMissingError(
                f"No data found in relation {relation.name} data bag."
            )

    def get_data(self) -> DexOidcConfigObject:
        """Return a DexOidcConfigObject containing Dex's OIDC configuration.

        Raises:
            DexOidcConfigRelationDataMissingError: if data is missing entirely or some attributes
            DexOidcConfigRelationMissingError: if there is no related application
            ops.model.TooManyRelatedAppsError: if there is more than one related application
        """
        # Validate relation data
        # Raises TooManyRelatedAppsError if related to more than one app
        relation = self.model.get_relation(self.relation_name)

        self._validate_relation(relation=relation)

        # Get relation data from remote app
        relation_data = relation.data[relation.app]

        return DexOidcConfigObject(issuer_url=relation_data["issuer-url"])


class DexOidcConfigProvider(Object):
    """Implement the Provider end of the Dex OIDC config relation.

    Observes relation events to send data to related applications.

    Args:
        charm (CharmBase): the provider application
        issuer_url (str): This is the canonical URL that OIDC clients MUST use to refer to dex.
        refresh_events: (list, optional): list of BoundEvents that this manager should handle.  Use this to update
                       the data sent on this relation on demand.
        relation_name (str, optional): the name of the relation

    Attributes:
        charm (CharmBase): variable for storing the provider application
        relation_name (str): variable for storing the name of the relation
    """

    def __init__(
        self,
        charm: CharmBase,
        issuer_url: str,
        refresh_events: Optional[List[BoundEvent]] = None,
        relation_name: Optional[str] = DEFAULT_RELATION_NAME,
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self._provider_wrapper = DexOidcConfigProviderWrapper(self.charm, self.relation_name)
        self._issuer_url = issuer_url

        self.framework.observe(self.charm.on.leader_elected, self._send_data)

        self.framework.observe(self.charm.on[self.relation_name].relation_created, self._send_data)

        if refresh_events:
            for evt in refresh_events:
                self.framework.observe(evt, self._send_data)

    def _send_data(self, _) -> None:
        """Serve as an event handler for sending Dex's OIDC configuration."""
        self._provider_wrapper.send_data(self._issuer_url)


class DexOidcConfigProviderWrapper(Object):
    """Wrapper for the relation data sending logic.

    Args:
        charm (CharmBase): the provider application
        relation_name (str, optional): the name of the relation

    Attributes:
        charm (CharmBase): variable for storing the provider application
        relation_name (str): variable for storing the name of the relation
    """

    def __init__(self, charm: CharmBase, relation_name: Optional[str] = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def send_data(
        self,
        issuer_url: str,
    ) -> None:
        """Update the relation data bag with data from Dex's OIDC configuration.

        This method will complete successfully even if there are no related applications.

        Args:
            issuer_url (str): This is the canonical URL that OIDC clients MUST use to refer to dex.
        """
        # Validate unit is leader to send data; otherwise return
        if not self.charm.model.unit.is_leader():
            logger.info(
                "DexOidcConfigProvider handled send_data event when it is not the leader."
                "Skipping event - no data sent."
            )
            return

        # Update the relation data bag with Dex's OIDC configuration
        relations = self.charm.model.relations[self.relation_name]

        # Update relation data
        for relation in relations:
            relation.data[self.charm.app].update(
                {
                    "issuer-url": issuer_url,
                }
            )
