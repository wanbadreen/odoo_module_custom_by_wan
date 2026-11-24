from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # The original Studio field remains authoritative on upgraded production DBs.
    # Development DBs without Studio may explicitly select Normal/COD here.
    gdex_order_type_fallback = fields.Selection(
        [('Normal', 'Normal'), ('COD', 'COD')],
        string='GDEX Type (when Studio unavailable)', copy=False,
        help='Used only when the original x_studio_order_type field does not exist.',
    )
    gdex_order_type = fields.Selection(
        [('Normal', 'Normal'), ('COD', 'COD')],
        string='GDEX Shipment Type', compute='_compute_gdex_order_type',
    )
    gdex_studio_type_available = fields.Boolean(
        compute='_compute_gdex_order_type',
    )

    def _gdex_shipment_type(self):
        """Canonical GDEX type without redefining/migrating the Studio field.

        Presence of Studio always wins, even if its value is empty/unsupported.
        This prevents Development fallback settings changing real production
        classification when the Studio customization is installed.
        """
        self.ensure_one()
        if 'x_studio_order_type' in self._fields:
            raw = self['x_studio_order_type']
        else:
            raw = self.gdex_order_type_fallback
        if raw == 'Normal':
            return 'Normal'
        if raw in ('COD', 'Cash On Delivery', 'Cash on Delivery'):
            return 'COD'
        return False

    @api.depends('gdex_order_type_fallback')
    def _compute_gdex_order_type(self):
        has_studio = 'x_studio_order_type' in self._fields
        for picking in self:
            picking.gdex_studio_type_available = has_studio
            picking.gdex_order_type = picking._gdex_shipment_type()


    gdex_cod_ids = fields.One2many(
        'motogene.gdex.cod.consignment', 'picking_id',
        string='GDEX COD AWBs', readonly=True)
    gdex_cod_summary = fields.Char(
        string='GDEX COD AWB Numbers',
        compute='_compute_gdex_cod_summary')

    @api.depends('gdex_cod_ids.awb', 'gdex_cod_ids.state')
    def _compute_gdex_cod_summary(self):
        for picking in self:
            picking.gdex_cod_summary = ' / '.join(
                picking.gdex_cod_ids.filtered(lambda a: a.state == 'success' and a.awb).mapped('awb'))


    def action_gdex_normal_wizard(self):
        self.env['motogene.gdex.consignment']._check_operator()
        pickings = self or self.browse(self.env.context.get('active_ids', [])).exists()
        if not pickings:
            raise UserError(_('Select Delivery Orders first.'))
        wizard = self.env['motogene.gdex.wizard'].create({})
        wizard.load_pickings(pickings)
        return {'type':'ir.actions.act_window', 'name':_('Create GDEX Normal Consignment'),
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode':'form', 'target':'new'}

    def action_open_gdex_cancel_wizard(self):
        """Open a reason/confirmation popup; never cancel from the button itself."""
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can cancel GDEX AWBs.'))
        if not self.mori_awb_no:
            raise UserError(_('This Delivery Order has no GDEX AWB to cancel.'))
        consignment = self.env['motogene.gdex.consignment'].sudo().search([
            ('picking_id', '=', self.id),
            ('awb', '=', self.mori_awb_no),
            ('state', '=', 'success'),
        ], limit=1)
        if not consignment:
            raise UserError(_('No successful GDEX consignment matches this Delivery Order AWB.'))
        if consignment.pieces > 1:
            raise UserError(_('MPS cancellation remains blocked until GDEX confirms all child CNs are cancelled.'))
        wizard = self.env['motogene.gdex.cancel.wizard'].create({
            'picking_id': self.id,
            'consignment_id': consignment.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Cancel GDEX AWB'),
            'res_model': 'motogene.gdex.cancel.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_gdex_mps_upload_wizard(self):
        """Open the official multi-piece label importer directly from the DO."""
        self.ensure_one()
        self.env['motogene.gdex.consignment']._check_operator()
        consignment = self.env['motogene.gdex.consignment'].sudo().search([
            ('picking_id', '=', self.id),
            ('awb', '=', self.mori_awb_no),
            ('state', '=', 'success'),
            ('pieces', '>', 1),
        ], limit=1)
        if not consignment:
            raise UserError(_('No successful GDEX multi-piece consignment found for this DO.'))
        return consignment.action_open_mps_upload_wizard()

    def action_open_gdex_actions(self):
        self.ensure_one()
        self.env['motogene.gdex.consignment']._check_operator()
        if self.picking_type_code != 'outgoing' or self._gdex_shipment_type() not in ('Normal', 'COD'):
            raise UserError(_('This Delivery Order is not an eligible GDEX Normal or COD outgoing shipment.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('GDEX Actions'),
            'res_model': 'motogene.gdex.do.actions',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_picking_id': self.id},
        }
