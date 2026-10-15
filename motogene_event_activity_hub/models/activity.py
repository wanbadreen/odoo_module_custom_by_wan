# -*- coding: utf-8 -*-
"""Activity grouping and idempotent creation of standard Odoo Events."""
import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class MotoGeneEventActivity(models.Model):
    _name = "motogene.event.activity"
    _description = "MotoGene Event Activity"
    _order = "name, id"

    name = fields.Char(required=True, translate=True, help="Public name shared by the activity's sessions.")
    subtitle = fields.Char(translate=True, help="Short introduction shown on the website.")
    description = fields.Html(translate=True, sanitize=True, help="Content shown on the activity details page.")
    image_1920 = fields.Image(string="Cover Image", help="Image displayed in activity listings and on its website page.")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company,
        help="Company responsible for the activity and its sessions.")
    website_id = fields.Many2one("website", required=True, ondelete="restrict",
        help="Website where this activity may be published.")
    event_type_id = fields.Many2one("event.type", string="Event Template", ondelete="set null",
        help="Odoo template used when preparing session tickets and communications.")
    default_venue_id = fields.Many2one("res.partner", string="Default Venue",
        help="Venue used when a session does not specify a different venue.")
    date_tz = fields.Selection(
        selection=lambda self: [(tz, tz) for tz in pytz.common_timezones],
        default="Asia/Kuala_Lumpur", required=True, string="Timezone",
        help="Timezone used to display session dates and times.")
    ticket_name = fields.Char(default="Free Registration", required=True,
        help="Name of the complimentary registration ticket created for each session.")
    phone_required = fields.Boolean(help="Require a phone number from every participant.")
    notification_partner_ids = fields.Many2many(
        "res.partner", "motogene_activity_notification_partner_rel", "activity_id", "partner_id",
        string="Staff Notification Recipients",
        help="Recipients copied to the existing event staff notification field when available.")
    session_ids = fields.One2many("motogene.event.activity.session", "activity_id", string="Sessions",
        help="Independent sessions linked to standard Odoo Events.")
    website_published = fields.Boolean(readonly=True, copy=False,
        help="Whether the activity is accessible on its public website.")
    state = fields.Selection([("draft", "Draft"), ("published", "Published")], default="draft",
        required=True, readonly=True, copy=False, help="Current publication state.")

    @api.constrains("company_id", "website_id")
    def _validate_website(self):
        for activity in self:
            if activity.website_id.company_id and activity.website_id.company_id != activity.company_id:
                raise ValidationError(_("The activity company must match the selected website company."))

    @api.model
    def _public_activity(self, website, activity_id):
        domain = [("id", "=", activity_id), ("website_id", "=", website.id),
                  ("website_published", "=", True)]
        if website.company_id:
            domain.append(("company_id", "=", website.company_id.id))
        return self.sudo().search(domain, limit=1)

    @api.model
    def _website_activities(self, website):
        domain = [("website_id", "=", website.id), ("website_published", "=", True)]
        if website.company_id:
            domain.append(("company_id", "=", website.company_id.id))
        return self.sudo().search(domain).filtered(lambda a: bool(a._website_sessions()))

    def _website_sessions(self):
        self.ensure_one()
        now = fields.Datetime.now()
        return self.session_ids.filtered(
            lambda s: s.event_id and s.event_id.website_published
            and s.event_id.website_id == self.website_id
            and s.event_id.company_id == self.company_id
            and s.event_id.date_end >= now
        ).sorted(lambda s: (s.event_id.date_begin, s.id))

    def _event_values(self, session):
        self.ensure_one()
        vals = {
            "name": "%s - %s" % (self.name, session.name),
            "company_id": self.company_id.id,
            "website_id": self.website_id.id,
            "website_visibility": "link",
            "website_published": False,
            "date_begin": session.date_begin,
            "date_end": session.date_end,
            "date_tz": self.date_tz,
            "seats_limited": True,
            "seats_max": session.seats_max,
            "motogene_activity_id": self.id,
            "motogene_activity_session_id": session.id,
        }
        if self.event_type_id:
            vals["event_type_id"] = self.event_type_id.id
        venue = session.venue_id or self.default_venue_id
        if venue:
            vals["address_id"] = venue.id
        if self.company_id.partner_id:
            vals["organizer_id"] = self.company_id.partner_id.id
        return vals

    def _ensure_questions(self, event):
        question_model = self.env["event.question"]
        for q_type, label, required in [
            ("name", "Full Name (as per IC)", True),
            ("email", "Email", True),
            ("phone", "Phone", self.phone_required),
        ]:
            existing = event.question_ids.filtered(lambda q: q.question_type == q_type)[:1]
            vals = {"title": label, "is_mandatory_answer": required, "once_per_order": False}
            if existing:
                existing.write(vals)
            else:
                question_model.create(dict(vals, event_id=event.id, question_type=q_type))

    def _create_free_ticket(self, event):
        # Newly created sessions have no registrations; remove tickets cloned
        # from templates so paid/template ticket variants cannot leak into v1.
        event.event_ticket_ids.unlink()
        ticket_model = self.env["event.event.ticket"]
        vals = {"event_id": event.id, "name": self.ticket_name, "seats_max": event.seats_max}
        if "price" in ticket_model._fields:
            vals["price"] = 0.0
        ticket_model.create(vals)

    def _sync_recipients(self, event):
        field_name = "x_studio_event_notification_recipients"
        if field_name in event._fields:
            event.write({field_name: [(6, 0, self.notification_partner_ids.ids)]})

    def action_prepare_sessions(self):
        """Create missing child events, and update linked ones without duplication."""
        for activity in self:
            activity._validate_website()
            if not activity.session_ids:
                raise UserError(_("Add at least one session before preparing events."))
            for session in activity.session_ids.sorted(lambda s: (s.date_begin, s.id)):
                vals = activity._event_values(session)
                event = session.event_id
                if event:
                    if event.company_id != activity.company_id or event.website_id != activity.website_id:
                        raise ValidationError(_("The linked event belongs to another company or website."))
                    if session.seats_max < event.seats_taken:
                        raise UserError(_("Session capacity cannot be below the number of registered attendees."))
                    if event.seats_taken and (
                        event.date_begin != session.date_begin or event.date_end != session.date_end
                    ):
                        raise UserError(_(
                            "Session dates cannot be changed after registration. "
                            "Review attendee communication before rescheduling."
                        ))
                    vals.pop("event_type_id", None)
                    vals.pop("website_published", None)
                    event.write(vals)
                    for ticket in event.event_ticket_ids:
                        if ticket.seats_max != session.seats_max:
                            ticket.write({"seats_max": session.seats_max})
                    if not event.event_ticket_ids:
                        activity._create_free_ticket(event)
                else:
                    event = self.env["event.event"].create(vals)
                    session.event_id = event.id
                    activity._ensure_questions(event)
                    activity._create_free_ticket(event)
                activity._sync_recipients(event)
                if activity.website_published and not event.website_published:
                    event.write({"website_published": True})
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"title": _("Sessions Prepared"),
                       "message": _("Session events are synchronized. No attendees were created."),
                       "type": "success", "sticky": False},
        }

    def action_publish(self):
        for activity in self:
            if not activity.session_ids:
                raise UserError(_("Add at least one session before publishing."))
            if not any(s.date_end > fields.Datetime.now() for s in activity.session_ids):
                raise UserError(_("At least one session must be upcoming."))
        self.action_prepare_sessions()
        for activity in self:
            activity.session_ids.mapped("event_id").write({
                "website_visibility": "link", "website_published": True,
            })
            activity.write({"website_published": True, "state": "published"})
        return True

    def action_unpublish(self):
        for activity in self:
            activity.session_ids.mapped("event_id").write({"website_published": False})
            activity.write({"website_published": False, "state": "draft"})
        return True

    def action_open_website(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url",
                "url": "/motogene/activity/%s" % self.id, "target": "new"}

    def unlink(self):
        if any(a.session_ids.mapped("event_id").filtered(lambda e: e.registration_ids) for a in self):
            raise UserError(_("An activity with registration history cannot be deleted. Unpublish it instead."))
        return super().unlink()
