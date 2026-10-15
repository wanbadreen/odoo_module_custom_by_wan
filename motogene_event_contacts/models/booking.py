# -*- coding: utf-8 -*-
from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

from odoo.addons.motogene_event_self_service.models.self_service import allowed_email, normalize_email


class MotoGeneEventBooking(models.Model):
    _name = "motogene.event.booking"
    _description = "MotoGene Group Event Booking"
    _order = "create_date desc"

    name = fields.Char(default="New", required=True, copy=False)
    owner_email = fields.Char(required=True, readonly=True, copy=False,
        help="Verified through guest email self-service before managing this booking.")
    company_id = fields.Many2one("res.company", required=True, readonly=True, ondelete="restrict")
    website_id = fields.Many2one("website", required=True, readonly=True, ondelete="restrict")
    event_id = fields.Many2one("event.event", required=True, readonly=True, ondelete="restrict")
    registration_ids = fields.One2many("event.registration", "motogene_booking_id", string="Participants", readonly=True)
    participant_count = fields.Integer(compute="_compute_participant_count")

    @api.depends("registration_ids")
    def _compute_participant_count(self):
        for booking in self:
            booking.participant_count = len(booking.registration_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            normalized = normalize_email(vals.get("owner_email"))
            if not allowed_email(normalized):
                raise ValidationError(_("A valid booking email is required."))
            vals["owner_email"] = normalized
            event = self.env["event.event"].browse(vals.get("event_id")).exists()
            if not event or event.company_id.id != vals.get("company_id") or event.website_id.id != vals.get("website_id"):
                raise ValidationError(_("The booking must match its Event, company, and website."))
        records = super().create(vals_list)
        for record in records:
            if record.name == "New":
                record.name = "EVB-%06d" % record.id
        return records
