# -*- coding: utf-8 -*-
"""Session rows and links to standard event.event records."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class MotoGeneEventActivitySession(models.Model):
    _name = "motogene.event.activity.session"
    _description = "MotoGene Activity Session"
    _order = "date_begin, id"

    activity_id = fields.Many2one("motogene.event.activity", required=True, ondelete="cascade",
        help="Activity that groups this session with related sessions.")
    name = fields.Char(required=True, help="Session title displayed to customers and staff.")
    date_begin = fields.Datetime(string="Start", required=True,
        help="Scheduled start date and time of the session.")
    date_end = fields.Datetime(string="End", required=True,
        help="Scheduled end date and time of the session.")
    seats_max = fields.Integer(string="Maximum Participants", required=True, default=10,
        help="Maximum number of participants that can register for this session.")
    venue_id = fields.Many2one("res.partner", string="Venue Override",
        help="Optional venue used instead of the activity default.")
    event_id = fields.Many2one("event.event", string="Linked Odoo Event", readonly=True,
        copy=False, ondelete="restrict",
        help="Standard Odoo Event that manages registration, tickets and attendees.")
    _sql_constraints = [
        ("activity_session_unique_name", "unique(activity_id, name)",
            "Session names must be unique within an activity."),
        ("activity_session_unique_event", "unique(event_id)",
            "An Odoo Event can only belong to one session."),
    ]

    @api.constrains("date_begin", "date_end", "seats_max")
    def _check_details(self):
        for session in self:
            if session.date_begin and session.date_end and session.date_end <= session.date_begin:
                raise ValidationError(_("The session end must be after its start."))
            if session.seats_max <= 0:
                raise ValidationError(_("Maximum Participants must be greater than zero."))

    def unlink(self):
        if any(s.event_id and s.event_id.registration_ids for s in self):
            raise UserError(_("A session with registrations cannot be deleted. Unpublish it instead."))
        events = self.mapped("event_id")
        result = super().unlink()
        events.filtered(lambda e: not e.registration_ids).unlink()
        return result


class EventEvent(models.Model):
    _inherit = "event.event"

    motogene_activity_id = fields.Many2one(
        "motogene.event.activity", string="MotoGene Activity", index=True,
        ondelete="set null", help="Activity hub grouping this event with related sessions.")
    motogene_activity_session_id = fields.Many2one(
        "motogene.event.activity.session", string="MotoGene Session", index=True,
        ondelete="set null", help="Activity session that manages this standard event.")
