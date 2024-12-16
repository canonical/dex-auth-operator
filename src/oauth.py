# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helper module used to manage oauth-related workloads."""

import logging
import secrets

import ops
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger()

OAUTH_STATIC_CLIENT_SECRET_LABEL = "oauth.static.client"


class OAuthClient(BaseModel):
    """Context used to render MAS configuration file.

    Attrs:
        client_id: OAuth client id
        client_secret: OAuth client secret
    """

    client_id: str = Field(min_length=32, max_length=32)
    client_secret: str = Field(min_length=32, max_length=32)


class OAuthRelationService():
    """Oauth Relation service class."""

    def __init__(self, charm: ops.CharmBase) -> None:
        """Init method for the class.
        Args:
            charm: The synapse charm.
        """
        self.charm = charm
        self.model = charm.model
        self.application = charm.app

    def get_oauth_static_client(self) -> OAuthClient:
        try:
            secret = self.model.get_secret(label=OAUTH_STATIC_CLIENT_SECRET_LABEL)
            oauth_client_static_secret = secret.get_content()
        except ops.model.SecretNotFoundError:
            # pylint: disable=raise-missing-from
            # We don't use "raise ... from exc" here
            # because SecretNotFoundError is not relevant to our error case.
            if not self.charm.unit.is_leader():
                logger.warning("Waiting for leader to set MAS context in secrets.")
                raise RuntimeError("Waiting for leader to set MAS context.")

            # The leader unit skips raising the above exception to generate the initial values
            # which can be picked up by peer units
            oauth_client_static_secret = {
                "client-id": secrets.token_hex(16),
                "client-secret": secrets.token_hex(16),
            }
            secret = self.application.add_secret(content=oauth_client_static_secret, label=OAUTH_STATIC_CLIENT_SECRET_LABEL)

        try:
            return OAuthClient(
                client_id=oauth_client_static_secret["client-id"],
                client_secret=oauth_client_static_secret["client-secret"],
            )
        except ValidationError as exc:
            logger.exception("Error validating Oauth static client information.")
            raise RuntimeError("Oauth static client validation failed") from exc

    def remove_oauth_static_client(self) -> None:
        try:
            secret = self.model.get_secret(label=OAUTH_STATIC_CLIENT_SECRET_LABEL)
            secret.remove_all_revisions()
        except ops.model.SecretNotFoundError:
            logger.warning("Secret %s already not exists, skipping.", OAUTH_STATIC_CLIENT_SECRET_LABEL)