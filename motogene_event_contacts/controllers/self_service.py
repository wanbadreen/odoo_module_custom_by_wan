# -*- coding: utf-8 -*-
"""Verified booking owners can manage their own booking group only."""
from odoo import http
from odoo.http import request
from odoo.addons.motogene_event_self_service.controllers.main import MotoGeneGuestRegistration
from odoo.addons.motogene_event_self_service.models.self_service import normalize_email


class MotoGeneBookingSelfServiceController(MotoGeneGuestRegistration):

    @http.route()
    def cancellation_confirmation(self, registration_id, **kw):
        config = self._config()
        email = self._session_email()
        if not config or not email:
            return request.redirect("/motogene/events/manage")
        reg = request.env["event.registration"].sudo().browse(registration_id).exists()
        if not reg or not config._motogene_can_manage_registration(reg, email):
            return request.not_found()
        if normalize_email(reg.email) == normalize_email(email):
            return super().cancellation_confirmation(registration_id, **kw)
        if not config._eligible_registration(reg):
            return request.redirect("/motogene/events/manage?status=unavailable")
        return self._page("motogene_event_self_service.confirm_cancellation", {
            "registration": reg,
        })
