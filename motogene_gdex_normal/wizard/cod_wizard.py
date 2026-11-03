"""Editable COD allocation wizard, one distinct AWB per physical parcel."""
from decimal import Decimal, ROUND_HALF_UP

from odoo import api, fields, models, _
from odoo.exceptions import UserError


def cents(amount):
    return int((Decimal(str(amount or 0)) * Decimal('100')).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))


class GdexCodWizard(models.TransientModel):
    _name = 'motogene.gdex.cod.wizard'
    _description = 'Prepare GDEX COD Consignments'

    picking_id = fields.Many2one('stock.picking', required=True, readonly=True)
    currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    sales_amount = fields.Monetary(currency_field='currency_id', readonly=True)
    total_cod = fields.Monetary(currency_field='currency_id', required=True)
    allocated_amount = fields.Monetary(currency_field='currency_id',
                                      compute='_compute_allocated')
    change_reason = fields.Char(string='Reason for Amount Difference')
    receiver_name = fields.Char(required=True)
    receiver_mobile = fields.Char(required=True)
    receiver_address1 = fields.Char(required=True)
    receiver_postcode = fields.Char(required=True)
    line_ids = fields.One2many('motogene.gdex.cod.wizard.line', 'wizard_id',
                               string='Physical Parcels')
    result_message = fields.Text(readonly=True)
    processed = fields.Boolean(readonly=True)

    @api.depends('line_ids.cod_amount')
    def _compute_allocated(self):
        for wizard in self:
            wizard.allocated_amount = sum(wizard.line_ids.mapped('cod_amount'))

    @api.onchange('total_cod')
    def _onchange_total_cod(self):
        for w in self:
            if w.processed:
                continue
            balance = cents(w.total_cod)
            lines = []
            number = 1
            while balance > 0 and number <= 50:
                alloc = min(balance, 150000)
                lines.append((0, 0, {
                    'parcel_no': number, 'cod_amount': alloc / 100.0, 'weight': 1.0,
                }))
                balance -= alloc
                number += 1
            w.line_ids = [(5, 0, 0)] + lines

    @api.model
    def action_open_for_picking(self, picking):
        self.env['motogene.gdex.cod.consignment']._check_operator()
        picking.ensure_one()
        if picking.state != 'assigned' or picking.picking_type_code != 'outgoing':
            raise UserError(_('GDEX COD can only be created for Ready outgoing Delivery Orders.'))
        if picking.x_studio_order_type not in ('COD', 'Cash On Delivery', 'Cash on Delivery'):
            raise UserError(_('This is not a COD Delivery Order. Check the internal delivery type value.'))
        if picking.mori_awb_no or self.env['motogene.gdex.cod.consignment'].sudo().search_count([
                ('picking_id', '=', picking.id)]):
            raise UserError(_('AWB or COD attempt already exists. Check the DO/portal before creating.'))
        sale = picking.sale_id
        if not sale:
            sale = picking.move_ids.sale_line_id.order_id[:1]
        if not sale:
            raise UserError(_('No linked Sales Order was found for the COD amount.'))
        if sale.currency_id.name != 'MYR':
            raise UserError(_('COD amounts must be in Malaysian Ringgit (MYR).'))
        partner = picking.partner_id
        address = ', '.join(v for v in (
            partner.street, partner.street2, partner.city) if v)
        total = sale.amount_total
        wizard = self.create({
            'picking_id': picking.id, 'currency_id': sale.currency_id.id,
            'sales_amount': total, 'total_cod': total,
            'receiver_name': partner.name, 'receiver_mobile': partner.mobile or partner.phone,
            'receiver_address1': address, 'receiver_postcode': partner.zip,
        })
        # Explicitly initialize allocation; onchange is not guaranteed on create().
        wizard._onchange_total_cod()
        return {'type': 'ir.actions.act_window', 'name': _('Create GDEX COD'),
                'res_model': self._name, 'res_id': wizard.id,
                'view_mode': 'form', 'target': 'new'}

    def action_create_cod(self):
        self.ensure_one()
        COD = self.env['motogene.gdex.cod.consignment']
        COD._check_operator()
        if self.processed:
            raise UserError(_('This wizard has already been processed.'))
        lines = self.line_ids.sorted('parcel_no')
        if not lines or len(lines) > 50:
            raise UserError(_('COD requires 1–50 parcels.'))
        if not 0 < cents(self.total_cod):
            raise UserError(_('Total COD must be positive.'))
        if cents(self.allocated_amount) != cents(self.total_cod):
            raise UserError(_('Sum of parcel COD amounts must equal the editable total COD.'))
        if cents(self.total_cod) != cents(self.sales_amount) and not (self.change_reason or '').strip():
            raise UserError(_('Provide a reason when COD total differs from Sales Order amount.'))
        if len(set(lines.mapped('parcel_no'))) != len(lines) or min(lines.mapped('parcel_no')) < 1:
            raise UserError(_('Parcel numbers must be unique positive numbers.'))
        if any(cents(line.cod_amount) < 1 or cents(line.cod_amount) > 150000
               or line.weight <= 0 for line in lines):
            raise UserError(_('Each parcel needs RM0.01–RM1,500 and positive weight.'))
        # Validate *all* parcel details before creating the first real AWB.
        # Otherwise an invalid later parcel could leave an avoidable partial order.
        if not all((self.receiver_name, self.receiver_mobile,
                    self.receiver_address1, self.receiver_postcode)):
            raise UserError(_('Complete all COD receiver details before creating AWBs.'))
        if len(self.receiver_mobile or '') > 25:
            raise UserError(_('GDEX receiver mobile must not exceed 25 characters.'))
        if any(len(line.note1 or '') > 50 or len(line.note2 or '') > 50
               for line in lines):
            raise UserError(_('Parcel Note 1/2 must not exceed 50 characters.'))
        if self.picking_id.partner_id.country_id.code != 'MY':
            raise UserError(_('COD delivery is available only for Malaysian addresses.'))
        COD._cod_credentials()  # Check COD account and safety switch before any AWB.
        if self.picking_id.mori_awb_no or COD.sudo().search_count([
                ('picking_id', '=', self.picking_id.id)]):
            raise UserError(_('Existing COD attempt or DO AWB found. Do not create duplicates.'))
        # Deliberately do NOT blindly retry a failed or uncertain parcel.
        results = []
        for line in lines:
            try:
                attempt_id, outcome = COD.create_from_cod_line(self, line)
                results.append('Parcel %s: %s (%s)' % (
                    line.parcel_no, outcome.get('state'), outcome.get('awb') or
                    outcome.get('error_message')))
            except Exception as exc:
                # An earlier parcel can already exist in GDEX. Return to the
                # wizard with details rather than throwing away what succeeded.
                results.append('Parcel %s: STOPPED (%s)' % (line.parcel_no, str(exc)))
                break
        self.env.invalidate_all()
        self.write({'processed': True, 'result_message': '\n'.join(results)})
        return {'type': 'ir.actions.act_window', 'name': _('GDEX COD Results'),
                'res_model': self._name, 'res_id': self.id,
                'view_mode': 'form', 'target': 'new'}


class GdexCodWizardLine(models.TransientModel):
    _name = 'motogene.gdex.cod.wizard.line'
    _description = 'GDEX COD Parcel Allocation'
    _order = 'parcel_no'

    wizard_id = fields.Many2one('motogene.gdex.cod.wizard', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='wizard_id.currency_id', readonly=True)
    parcel_no = fields.Integer(required=True)
    cod_amount = fields.Monetary(currency_field='currency_id', required=True)
    weight = fields.Float(default=1.0, required=True)
    note1 = fields.Char(string='Parcel Note 1')
    note2 = fields.Char(string='Parcel Note 2')
