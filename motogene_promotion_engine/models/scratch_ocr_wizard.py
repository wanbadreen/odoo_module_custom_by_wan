# -*- coding: utf-8 -*-
import base64

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .scratch_ocr import read_serials


class ScratchCardOcrWizard(models.TransientModel):
    _name = "motogene.scratch.ocr.wizard"
    _description = "Check Scratch Card Photo"

    picking_id = fields.Many2one("stock.picking", required=True, readonly=True)
    photo = fields.Image(string="Photo of Serial Number", required=True,
                         max_width=2048, max_height=2048, attachment=False)
    detected_serial = fields.Char(string="Serial Read from Photo", readonly=True)
    confirmed_serial = fields.Char(string="Serial on Physical Card",
                                   help="Check this against the card. Correct it if OCR misread the photo.")
    ocr_text = fields.Text(string="OCR Text", readonly=True)
    ocr_status = fields.Char(string="Result", readonly=True)
    photo_read = fields.Boolean(default=False)

    @api.onchange("photo")
    def _onchange_photo(self):
        self.detected_serial = False
        self.confirmed_serial = False
        self.ocr_text = False
        self.ocr_status = False
        self.photo_read = False

    def _reopen(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Check Scratch Card Photo"),
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }

    def action_read_photo(self):
        self.ensure_one()
        if not self.picking_id.scratch_card_line_ids:
            raise UserError(_("Prepare the Scratch & Win cards on this delivery first."))
        try:
            candidates, raw_text = read_serials(base64.b64decode(self.photo))
        except (ValueError, RuntimeError) as exc:
            raise UserError(str(exc)) from exc
        # Avoid silently selecting a wrong number when the photo contains several.
        detected = candidates[0] if len(candidates) == 1 else False
        self.write({
            "detected_serial": detected,
            "confirmed_serial": detected,
            "ocr_text": raw_text[:2000],
            "ocr_status": (
                _("Read %(serial)s. Check it against the physical card.") % {"serial": detected}
                if detected else _("Could not read one clear serial. Retake a close photo of the number, "
                                "or enter the number yourself.")
            ),
            "photo_read": True,
        })
        return self._reopen()

    def action_verify_card(self):
        self.ensure_one()
        if not self.photo_read:
            raise UserError(_("Read the photo first."))
        if self.picking_id.state in ("done", "cancel"):
            raise UserError(_("This delivery is already completed or cancelled."))
        serial = (self.confirmed_serial or "").strip().upper().replace(" ", "")
        if not serial:
            raise UserError(_("Confirm the serial printed on the physical card."))
        lines = self.picking_id.scratch_card_line_ids.filtered(
            lambda line: line.serial_number == serial
        )
        if not lines:
            raise UserError(_("Card %s is not assigned to this delivery. Do not pack it.") % serial)
        if lines.packed:
            raise UserError(_("Card %s has already been marked as packed.") % serial)
        lines.write({
            "packed": True,
            "verified_serial": serial,
            "verification_method": "photo_ocr" if serial == self.detected_serial else "photo_corrected",
        })
        return {"type": "ir.actions.act_window_close"}
