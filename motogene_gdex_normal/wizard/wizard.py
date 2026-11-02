from odoo import api, models, fields, _
from odoo.tools import html2plaintext
from odoo.exceptions import UserError


class GdexWizard(models.TransientModel):
    _name = 'motogene.gdex.wizard'
    _description = 'GDEX Normal Bulk Wizard'

    line_ids = fields.One2many('motogene.gdex.wizard.line', 'wizard_id', string='Shipments')
    results = fields.Text(readonly=True)

    def load_pickings(self, pickings):
        self.ensure_one()
        vals = []
        for p in pickings:
            partner = p.partner_id
            address = ', '.join(x for x in [partner.street, partner.street2, partner.city] if x)
            vals.append((0, 0, {
                'picking_id': p.id, 'receiver_name': partner.name,
                'receiver_mobile': partner.mobile or partner.phone,
                'receiver_address1': address, 'receiver_postcode': partner.zip,
                'weight': 1.0, 'pieces': 1, 'note_source': 'none',
            }))
        self.line_ids = vals

    def action_create(self):
        self.ensure_one()
        consignments = self.env['motogene.gdex.consignment']
        consignments._check_operator()
        # A wizard with uncommitted edits should not be used to send external
        # requests; persist wizard-line edits in the normal Odoo RPC first.
        self.line_ids.flush_recordset()
        for line in self.line_ids:
            if line.consignment_attempt_id:
                previous = consignments.sudo().browse(
                    line.consignment_attempt_id
                ).exists()
                if previous and previous.state != 'failed':
                    line.result = (
                        'Blocked: already processed or unresolved; '
                        'check GDEX history.'
                    )
                    continue
            if line.note_source == 'do':
                raw_note = html2plaintext(line.picking_id.note or '').strip()
                if len(raw_note) > 100:
                    line.result = 'DO Note exceeds 100 characters; select Manual and rewrite it.'
                    continue
            # Each attempt is independently reserved/finalized in private cursors.
            attempt_id, outcome = consignments.create_from_wizard_line(line)
            self.env.invalidate_all()
            if attempt_id:
                line.consignment_attempt_id = attempt_id
            line.result = outcome
        self.results = '\n'.join('%s: %s' % (line.picking_id.name, line.result or '') for line in self.line_ids)
        return {'type': 'ir.actions.act_window', 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_print_results(self):
        self.ensure_one()
        ids = [
            line.consignment_attempt_id
            for line in self.line_ids
            if line.consignment_attempt_id
        ]
        cons = self.env['motogene.gdex.consignment'].browse(
            ids
        ).exists().filtered(lambda c: c.state == 'success')
        return cons.action_print_bulk()


class GdexWizardLine(models.TransientModel):
    _name = 'motogene.gdex.wizard.line'
    _description = 'GDEX Wizard Shipment'

    wizard_id = fields.Many2one('motogene.gdex.wizard', ondelete='cascade', required=True)
    picking_id = fields.Many2one('stock.picking', readonly=True)
    receiver_name = fields.Char()
    receiver_mobile = fields.Char()
    receiver_address1 = fields.Char()
    receiver_postcode = fields.Char()
    weight = fields.Float(default=1.0)
    pieces = fields.Integer(default=1)
    note_source = fields.Selection([('none', 'No Note'), ('manual', 'Manual'), ('do', 'Use DO Note')], default='none', required=True)
    note1 = fields.Char(string='GDEX Note 1 (50 chars)')
    note2 = fields.Char(string='GDEX Note 2 (50 chars)')

    @api.onchange('note_source')
    def _onchange_note_source(self):
        for line in self:
            if line.note_source == 'none':
                line.note1 = False
                line.note2 = False
            elif line.note_source == 'do':
                raw = html2plaintext(line.picking_id.note or '').strip()
                line.note1 = raw[:50]
                line.note2 = raw[50:100]
                # User must review/edit before creating. Text over 100 chars
                # is deliberately not silently sent as a complete instruction.
    result = fields.Char(readonly=True)
    consignment_attempt_id = fields.Integer(
        string='GDEX Attempt ID',
        readonly=True,
        copy=False
    )
