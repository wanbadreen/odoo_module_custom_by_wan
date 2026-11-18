import requests

from odoo import models, _
from odoo.exceptions import AccessError, UserError


class MotogeneFedexApi(models.AbstractModel):
    _name = "motogene.fedex.api"
    _description = "MotoGene FedEx Quotation API Service"

    FEDEX_SANDBOX_URL = "https://apis-sandbox.fedex.com"

    def _get_credentials(self):
        """Read sandbox credentials from restricted Odoo configuration."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(
                _("Only system administrators can access FedEx credentials.")
            )

        params = self.env["ir.config_parameter"].sudo()
        client_id = params.get_param("motogene_fedex_quotation.client_id")
        client_secret = params.get_param("motogene_fedex_quotation.client_secret")

        if not client_id or not client_secret:
            raise UserError(_("FedEx sandbox credentials are not configured."))
        return client_id, client_secret

    def _get_access_token(self):
        """Authenticate with sandbox; never log credentials or tokens."""
        client_id, client_secret = self._get_credentials()
        try:
            response = requests.post(
                f"{self.FEDEX_SANDBOX_URL}/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=30,
            )
        except requests.RequestException:
            raise UserError(_("Unable to connect to FedEx Sandbox.")) from None

        if response.status_code != 200:
            raise UserError(
                _("FedEx authentication failed. HTTP status: %s")
                % response.status_code
            )

        try:
            token = response.json().get("access_token")
        except ValueError:
            raise UserError(
                _("FedEx returned an invalid authentication response.")
            ) from None

        if not token:
            raise UserError(
                _("FedEx authentication response has no access token.")
            )

        return token
