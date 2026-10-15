# -*- coding: utf-8 -*-
"""Public guest registration management routes. All writes require email verification."""
import time
from html import escape
from urllib.parse import quote

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import AccessError, UserError

from ..models.self_service import allowed_email, normalize_email, _SESSION_LIFETIME

SESSION_KEY = "motogene_event_self_service_verified"
ROOT = "/motogene/events/manage"


class MotoGeneGuestRegistration(http.Controller):
    def _config(self):
        website = request.website
        if not website:
            return False
        return request.env["motogene.event.self.service.config"].sudo().for_website(website)

    def _session_email(self):
        grant = request.session.get(SESSION_KEY)
        if not isinstance(grant, dict):
            return False
        if grant.get("website_id") != request.website.id or grant.get("expires", 0) < time.time():
            request.session.pop(SESSION_KEY, None)
            return False
        return grant.get("email")

    def _page(self, template, values=None):
        response = request.render(template, values or {})
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @http.route(ROOT, auth="public", website=True, sitemap=False, methods=["GET"])
    def manage(self, **kw):
        config = self._config()
        if not config:
            return self._page("motogene_event_self_service.disabled")
        email = self._session_email()
        if not email:
            return self._page("motogene_event_self_service.request_link", {"error": False})
        regs = config._registrations_for_email(email).sorted(
            lambda r: (r.event_id.date_begin or fields.Datetime.now(), r.id), reverse=True,
        )
        return self._page("motogene_event_self_service.my_registrations", {
            "registrations": regs,
            "can_cancel": lambda reg: config._eligible_registration(reg),
            "cancellation_reason": lambda reg: config._cancellation_unavailable_reason(reg),
            "feedback": kw.get("status", ""),
        })

    @http.route(ROOT + "/request-link", auth="public", website=True, sitemap=False, methods=["POST"], csrf=True)
    def request_link(self, email=None, **post):
        config = self._config()
        if not config:
            return self._page("motogene_event_self_service.disabled")
        normalized = normalize_email(email)
        if not allowed_email(normalized):
            return self._page("motogene_event_self_service.request_link", {
                "error": "Please enter a valid email address.",
            })
        token = request.env["motogene.event.self.service.token"].sudo()._issue_token(
            normalized, request.website, request.httprequest.remote_addr,
        )
        if token and config._registrations_for_email(normalized):
            base = request.httprequest.url_root.rstrip("/")
            link = base + ROOT + "/verify?token=" + quote(token)
            sender = config.company_id.email or request.env.user.email or "noreply@localhost"
            request.env["mail.mail"].sudo().create({
                "subject": "MotoGene - Manage Your Event Registrations",
                "body_html": (
                    "<p>Use the secure link below to access your MotoGene registrations.</p>"
                    "<p><a href=\"%s\">Verify My Email</a></p>"
                    "<p>The link expires in 15 minutes. If you did not request it, ignore this email.</p>"
                ) % escape(link, quote=True),
                "email_to": normalized,
                "email_from": sender,
                "auto_delete": False,
            })
        return self._page("motogene_event_self_service.request_link_sent")

    @http.route(ROOT + "/verify", auth="public", website=True, sitemap=False, methods=["GET"])
    def verification_landing(self, token=None, **kw):
        if not self._config():
            return self._page("motogene_event_self_service.disabled")
        return self._page("motogene_event_self_service.verification_landing", {
            "token": (token or "")[:200],
        })

    @http.route(ROOT + "/verify", auth="public", website=True, sitemap=False, methods=["POST"], csrf=True)
    def verify(self, token=None, **post):
        if not self._config():
            return self._page("motogene_event_self_service.disabled")
        verified = request.env["motogene.event.self.service.token"].sudo()._consume_token(
            token, request.website,
        )
        if not verified:
            return self._page("motogene_event_self_service.verification_invalid")
        request.session[SESSION_KEY] = {
            "email": verified, "website_id": request.website.id,
            "expires": time.time() + _SESSION_LIFETIME,
        }
        return request.redirect(ROOT, code=303)

    @http.route(ROOT + "/cancel/<int:registration_id>", auth="public", website=True, sitemap=False, methods=["GET"])
    def cancellation_confirmation(self, registration_id, **kw):
        config = self._config()
        email = self._session_email()
        if not config or not email:
            return request.redirect(ROOT)
        reg = request.env["event.registration"].sudo().browse(registration_id).exists()
        if not reg or normalize_email(reg.email) != email:
            return request.not_found()
        if not config._eligible_registration(reg):
            return request.redirect(ROOT + "?status=unavailable")
        return self._page("motogene_event_self_service.confirm_cancellation", {"registration": reg})

    @http.route(ROOT + "/cancel/<int:registration_id>", auth="public", website=True, sitemap=False, methods=["POST"], csrf=True)
    def cancel(self, registration_id, **post):
        config = self._config()
        email = self._session_email()
        if not config or not email:
            return request.redirect(ROOT)
        try:
            config._cancel_for_verified_email(registration_id, email)
        except (AccessError, UserError):
            return request.redirect(ROOT + "?status=unavailable", code=303)
        return request.redirect(ROOT + "?status=cancelled", code=303)

    @http.route(ROOT + "/logout", auth="public", website=True, sitemap=False, methods=["POST"], csrf=True)
    def logout(self, **post):
        request.session.pop(SESSION_KEY, None)
        return request.redirect(ROOT, code=303)
