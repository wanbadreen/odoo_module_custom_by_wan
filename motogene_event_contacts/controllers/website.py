# -*- coding: utf-8 -*-
"""Hook native website attendee creation; do not replace Odoo's ticket checks."""
from odoo.addons.website_event.controllers.main import WebsiteEventController
from odoo.addons.motogene_event_self_service.models.self_service import normalize_email, allowed_email
from odoo.exceptions import UserError
from odoo import _
from odoo.http import request


class MotoGeneBookingController(WebsiteEventController):
    def _create_attendees_from_registration_post(self, event, registration_data):
        config = request.env["motogene.event.contact.config"].for_event(event)
        if not config:
            return super()._create_attendees_from_registration_post(event, registration_data)

        booking_email = normalize_email(
            request.httprequest.form.get("motogene_booking_email")
        )
        if not allowed_email(booking_email):
            raise UserError(_("A valid Booking Email is required to manage this registration."))

        # Native Odoo copies the website visitor's partner_id into every
        # registration. Its computed attendee Email can then inherit the
        # visitor/booker's email for participants who left Email blank.
        # For the opted-in group-booking flow we must never do that.
        # Keep the visitor link, but set Booked by to False so the participant
        # Email remains an honest individual field. Ownership belongs to the
        # dedicated booking record, not to event.registration.partner_id.
        visitor = request.env["website.visitor"]._get_visitor_from_request(force_create=True)
        to_create = []
        for values in registration_data:
            item = dict(values)
            item["event_id"] = event.id
            item["partner_id"] = False
            item["visitor_id"] = visitor.id
            to_create.append(item)
        registrations = request.env["event.registration"].sudo().create(to_create)

        booking = request.env["motogene.event.booking"].sudo().create({
            "owner_email": booking_email,
            "company_id": event.company_id.id,
            "website_id": event.website_id.id,
            "event_id": event.id,
        })
        registrations.sudo().write({"motogene_booking_id": booking.id})
        return registrations
