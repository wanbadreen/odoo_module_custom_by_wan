# -*- coding: utf-8 -*-
"""Native Odoo event questions reused for centralized Activity registration settings."""
from odoo import fields, models, _
from odoo.exceptions import UserError


class EventQuestion(models.Model):
    _inherit = "event.question"

    motogene_activity_id = fields.Many2one(
        "motogene.event.activity", string="MotoGene Activity",
        ondelete="cascade", index=True, copy=False,
        help="Activity whose sessions use this registration question.",
    )
    motogene_activity_source_id = fields.Many2one(
        "event.question", string="Source Activity Question",
        ondelete="set null", index=True, copy=False,
        help="Original Activity question from which this session question was created.",
    )

    _sql_constraints = [
        (
            "motogene_unique_question_source_per_event",
            "unique(event_id, motogene_activity_source_id)",
            "An Activity question may only appear once per session.",
        )
    ]

    def _motogene_managed_children(self):
        return self.env["event.question"].search([
            ("motogene_activity_source_id", "in", self.ids),
            ("event_id", "!=", False),
        ])

    def action_view_question_answers(self):
        self.ensure_one()
        if not self.motogene_activity_id:
            return super().action_view_question_answers()
        children = self._motogene_managed_children()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "event.action_event_registration_report"
        )
        action["domain"] = [("question_id", "in", children.ids)]
        if self.question_type == "simple_choice":
            action["views"] = [(False, "graph"), (False, "pivot"), (False, "list")]
        elif self.question_type == "text_box":
            action["views"] = [(False, "list")]
        return action

    def write(self, vals):
        if "motogene_activity_id" in vals and not vals["motogene_activity_id"]:
            # Odoo one2many operations may detach rather than unlink lines.
            # Clean their child copies safely in either case.
            sources = self.filtered("motogene_activity_id")
            children = sources._motogene_managed_children()
            if children and self.env["event.registration.answer"].search_count([
                ("question_id", "in", children.ids)
            ]):
                raise UserError(_(
                    "This question already has attendee answers in a linked "
                    "session and cannot be removed from the activity."
                ))
            if children:
                children.unlink()
        if "question_type" in vals:
            changed = self.filtered(
                lambda q: q.motogene_activity_id and q.question_type != vals["question_type"]
            )
            if changed:
                linked = changed._motogene_managed_children()
                if linked and self.env["event.registration.answer"].search_count([
                    ("question_id", "in", linked.ids)
                ]):
                    raise UserError(_(
                        "You cannot change the question type after a participant "
                        "has answered this question in a linked session."
                    ))
        return super().write(vals)

    def unlink(self):
        sources = self.filtered("motogene_activity_id")
        children = sources._motogene_managed_children()
        if children and self.env["event.registration.answer"].search_count([
            ("question_id", "in", children.ids)
        ]):
            raise UserError(_(
                "This question already has attendee answers in a linked session. "
                "It cannot be deleted."
            ))
        if children:
            children.unlink()
        return super().unlink()


class EventQuestionAnswer(models.Model):
    _inherit = "event.question.answer"

    motogene_activity_source_answer_id = fields.Many2one(
        "event.question.answer", string="Source Activity Answer",
        ondelete="set null", index=True, copy=False,
        help="Original Activity option copied to this session answer.",
    )

    def write(self, vals):
        """Keep historical selection labels stable across parent/child edits.

        Odoo stores an attendee's Selection answer as a pointer to the
        event.question.answer row. Renaming that row changes the display
        of all past answers. Protect only centrally managed Activity options,
        leaving independent Single Events and unused options editable.
        """
        if "name" in vals:
            modified = self.filtered(
                lambda answer: answer.name != vals["name"]
                and (
                    answer.question_id.motogene_activity_id
                    or answer.motogene_activity_source_answer_id
                )
            )
            if modified:
                # Include every linked child option for Activity parent answers;
                # check direct child changes against their own saved responses.
                parents = modified.filtered("question_id.motogene_activity_id")
                related_children = self.search([
                    ("motogene_activity_source_answer_id", "in", parents.ids),
                ]) if parents else self.browse()
                options = modified | related_children
                already_selected = self.env["event.registration.answer"].search_count([
                    ("value_answer_id", "in", options.ids),
                ])
                if already_selected:
                    raise UserError(_(
                        "This answer option has already been selected by an "
                        "attendee. Its text cannot be changed because this "
                        "would alter historical registration answers. "
                        "Add a new answer option instead."
                    ))
        return super().write(vals)

    def unlink(self):
        source_answers = self.filtered(lambda a: bool(a.question_id.motogene_activity_id))
        if source_answers:
            linked = self.search([
                ("motogene_activity_source_answer_id", "in", source_answers.ids)
            ])
            if linked and self.env["event.registration.answer"].search_count([
                ("value_answer_id", "in", linked.ids)
            ]):
                raise UserError(_(
                    "This answer option has been selected by an attendee in "
                    "a linked session and cannot be deleted."
                ))
            if linked:
                linked.unlink()
        return super().unlink()
