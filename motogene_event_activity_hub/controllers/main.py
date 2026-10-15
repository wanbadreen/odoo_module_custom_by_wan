# -*- coding: utf-8 -*-
"""Website-scoped activity pages; no public create/write endpoints."""
import base64

from odoo import http
from odoo.http import request


class MotoGeneActivityWebsite(http.Controller):
    def _activity(self, activity_id):
        return request.env["motogene.event.activity"]._public_activity(
            request.website, activity_id
        )

    @http.route(
        "/motogene/activity/<int:activity_id>",
        type="http", auth="public", website=True, sitemap=False, methods=["GET"]
    )
    def activity_page(self, activity_id, **kw):
        activity = self._activity(activity_id)
        if not activity:
            return request.not_found()
        response = request.render("motogene_event_activity_hub.activity_page", {
            "activity": activity,
            "sessions": activity._website_sessions(),
        })
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @http.route(
        "/motogene/activity/<int:activity_id>/cover",
        type="http", auth="public", website=True, sitemap=False, methods=["GET"]
    )
    def activity_cover(self, activity_id, **kw):
        activity = self._activity(activity_id)
        if not activity or not activity.image_1920:
            return request.not_found()
        payload = base64.b64decode(activity.image_1920)
        content_type = "image/jpeg"
        if payload.startswith(b"\x89PNG"):
            content_type = "image/png"
        elif payload.startswith(b"RIFF") and b"WEBP" in payload[:16]:
            content_type = "image/webp"
        return request.make_response(payload, headers=[
            ("Content-Type", content_type),
            ("Cache-Control", "no-store, max-age=0"),
        ])
