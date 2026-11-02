import base64
import io
import json

from odoo import fields, models, _
from odoo.exceptions import UserError
from pypdf import PdfReader

from ..models.mps_labels import normalize_official_mps


class GdexMpsUploadWizard(models.TransientModel):
    _name = 'motogene.gdex.mps.upload.wizard'
    _description = 'Import Official GDEX MPS Labels'

    consignment_id = fields.Many2one('motogene.gdex.consignment', readonly=True, required=True)
    picking_id = fields.Many2one(related='consignment_id.picking_id', readonly=True)
    awb = fields.Char(related='consignment_id.awb', readonly=True)
    pieces = fields.Integer(related='consignment_id.pieces', readonly=True)
    pdf_file = fields.Binary(string='Official myGDEX Prime PDF', required=True, attachment=False)
    filename = fields.Char()

    def action_import(self):
        self.ensure_one()
        self.env['motogene.gdex.consignment']._check_operator()
        consignment = self.consignment_id.sudo()
        if consignment.state != 'success' or consignment.pieces <= 1:
            raise UserError(_('Only successful multi-piece consignments can import MPS labels.'))
        try:
            children = json.loads(consignment.mps_numbers or '[]')
        except (ValueError, TypeError):
            children = []
        if (not isinstance(children, list)
                or len(children) != consignment.pieces - 1
                or not all(isinstance(c, str) and c for c in children)):
            raise UserError(_('Retrieve all child MPS numbers before importing the official PDF.'))
        if not self.pdf_file:
            raise UserError(_('Upload the official myGDEX Prime PDF first.'))
        try:
            raw = base64.b64decode(self.pdf_file)
            normalized = normalize_official_mps(raw, consignment.awb, children)
            if len(PdfReader(io.BytesIO(normalized)).pages) != consignment.pieces:
                raise ValueError('Normalized page count does not match pieces.')
        except (ValueError, TypeError) as exc:
            raise UserError(_('MPS PDF rejected: %s') % exc) from exc
        attachment = self.env['ir.attachment'].sudo().create({
            'name': consignment.awb + '_MPS_%s_labels_100x150.pdf' % consignment.pieces,
            'type': 'binary',
            'datas': base64.b64encode(normalized),
            'mimetype': 'application/pdf',
            'res_model': consignment._name,
            'res_id': consignment.id,
        })
        consignment.write({'pdf_attachment_id': attachment.id})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('GDEX MPS Labels Ready'),
                'message': _('%s official labels saved for printing and bulk reprint.') % consignment.pieces,
                'type': 'success', 'sticky': False,
            },
        }
