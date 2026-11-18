import requests

from odoo import _, models
from odoo.exceptions import AccessError, UserError


class MotogeneFedexApi(models.AbstractModel):
    _name = "motogene.fedex.api"
    _description = "MotoGene FedEx Sandbox Rates Service"

    BASE_URL = "https://apis-sandbox.fedex.com"

    def _assert_operator(self):
        if not (
            self.env.user.has_group("motogene_fedex_quotation.group_fedex_operator")
            or self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_("Only FedEx Operations users can request rates."))

    def _credentials(self):
        self._assert_operator()
        params = self.env["ir.config_parameter"].sudo()
        values = [
            params.get_param("motogene_fedex_quotation.client_id"),
            params.get_param("motogene_fedex_quotation.client_secret"),
            params.get_param("motogene_fedex_quotation.account_number"),
        ]
        if not all(values):
            raise UserError(
                _("Configure FedEx sandbox client ID, secret and test account number.")
            )
        return values

    def _access_token(self, client_id, client_secret):
        try:
            response = requests.post(
                self.BASE_URL + "/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                timeout=30,
            )
        except requests.RequestException:
            raise UserError(_("FedEx sandbox authentication network error.")) from None
        if response.status_code != 200:
            raise UserError(
                _("FedEx authentication failed (HTTP %s).") % response.status_code
            )
        try:
            token = response.json().get("access_token")
        except ValueError:
            token = None
        if not token:
            raise UserError(_("FedEx authentication response was invalid."))
        return token

    def get_rates(self, shipment):
        """Rates only. Never calls the FedEx Ship API."""
        client_id, secret, account = self._credentials()
        token = self._access_token(client_id, secret)
        request_data = {
            "accountNumber": {"value": account},
            "rateRequestControlParameters": {"returnTransitTimes": True},
            "requestedShipment": shipment,
        }
        try:
            response = requests.post(
                self.BASE_URL + "/rate/v1/rates/quotes",
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "X-locale": "en_MY",
                },
                json=request_data,
                timeout=40,
            )
        except requests.RequestException:
            raise UserError(_("FedEx sandbox rates network error.")) from None
        try:
            data = response.json()
        except ValueError:
            raise UserError(_("FedEx returned a non-JSON rates response.")) from None
        if not response.ok:
            errors = data.get("errors") or []
            details = "; ".join(
                ("%s: %s" % (e.get("code", ""), e.get("message", "")))
                for e in errors[:3]
                if isinstance(e, dict)
            )
            raise UserError(
                _("FedEx Rates API HTTP %(status)s: %(details)s")
                % {"status": response.status_code, "details": details or _("No detail")}
            )
        return self._parse_rates(data)

    def _parse_rates(self, data):
        """Net charges are numeric; currency is a separate sibling key."""
        parsed = []
        for service in data.get("output", {}).get("rateReplyDetails", []):
            commit = service.get("commit") or {}
            delivery = (
                commit.get("dateDetail", {}).get("dayFormat")
                if isinstance(commit.get("dateDetail"), dict)
                else None
            )
            # Only accept an actual date from FedEx; never guess transit dates.
            delivery = commit.get("deliveryDate") or delivery
            if not (isinstance(delivery, str) and len(delivery) == 10
                    and delivery[4] == "-" and delivery[7] == "-"):
                delivery = False
            for rate in service.get("ratedShipmentDetails") or []:
                if rate.get("rateType") != "ACCOUNT":
                    continue
                charge = rate.get("totalNetCharge")
                if not isinstance(charge, (int, float)):
                    continue
                parsed.append({
                    "service_type": service.get("serviceType") or "",
                    "service_name": service.get("serviceName")
                    or service.get("serviceType") or "",
                    "amount": charge,
                    "currency_code": rate.get("currency") or "",
                    "surcharge": rate.get("shipmentRateDetail", {}).get(
                        "totalSurcharges"
                    ) or 0.0,
                    "delivery_date": delivery,
                    "transit_message": commit.get("commitMessageDetails") or "",
                })
        return parsed
