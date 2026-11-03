from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError


class GdexDoActions(models.TransientModel):
    _name = 'motogene.gdex.do.actions'
    _description = 'GDEX Delivery Order Actions'

    picking_id = fields.Many2one('stock.picking', required=True, readonly=True)
    awb = fields.Char(related='picking_id.mori_awb_no', readonly=True)
    can_create = fields.Boolean(compute='_compute_available')
    can_track = fields.Boolean(compute='_compute_available')
    can_import_mps = fields.Boolean(compute='_compute_available')
    can_cancel = fields.Boolean(compute='_compute_available')
    can_create_cod = fields.Boolean(compute='_compute_available')
    can_view_cod = fields.Boolean(compute='_compute_available')

    @api.depends('picking_id', 'picking_id.state', 'picking_id.mori_awb_no')
    def _compute_available(self):
        cons = self.env['motogene.gdex.consignment'].sudo()
        for wizard in self:
            p = wizard.picking_id
            awb = (p.mori_awb_no or '').strip()
            is_normal = p.picking_type_code == 'outgoing' and p.x_studio_order_type == 'Normal'
            wizard.can_create = bool(is_normal and p.state == 'assigned' and not awb)
            wizard.can_track = bool(awb)
            matching = cons.search([('picking_id', '=', p.id),
                                    ('awb', '=', awb),
                                    ('state', '=', 'success')], limit=1) if awb else cons.browse()
            wizard.can_import_mps = bool(matching and matching.pieces > 1)
            is_cod = p.picking_type_code == 'outgoing' and p.x_studio_order_type in ('COD', 'Cash On Delivery', 'Cash on Delivery')
            cod_count = self.env['motogene.gdex.cod.consignment'].sudo().search_count([('picking_id', '=', p.id)])
            wizard.can_create_cod = bool(is_cod and p.state == 'assigned' and not awb and not cod_count)
            wizard.can_view_cod = bool(is_cod and cod_count)
            wizard.can_cancel = bool(is_normal and p.state == 'assigned' and matching
                                     and matching.pieces == 1 and
                                     self.env.user.has_group('base.group_system'))

    def _check_gdex_operator_access(self):
        self.ensure_one()
        self.env['motogene.gdex.consignment']._check_operator()
        if not self.picking_id.exists():
            raise UserError(_('Delivery Order not found.'))

    def action_create_awb(self):
        self._check_gdex_operator_access()
        if not self.can_create:
            raise UserError(_('AWB creation is not available for this Delivery Order.'))
        return self.picking_id.action_gdex_normal_wizard()

    def action_tracking(self):
        self._check_gdex_operator_access()
        return self.picking_id.action_open_gdex_tracking()

    def action_import_mps(self):
        self._check_gdex_operator_access()
        if not self.can_import_mps:
            raise UserError(_('No eligible multi-piece GDEX AWB found.'))
        return self.picking_id.action_open_gdex_mps_upload_wizard()

    def action_cancel_awb(self):
        self._check_gdex_operator_access()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators may cancel GDEX AWBs.'))
        if not self.can_cancel:
            raise UserError(_('GDEX cancellation is unavailable for this Delivery Order.'))
        return self.picking_id.action_open_gdex_cancel_wizard()

    def action_create_cod(self):
        self._check_gdex_operator_access()
        if not self.can_create_cod:
            raise UserError(_('COD creation is not available for this DO.'))
        return self.env['motogene.gdex.cod.wizard'].action_open_for_picking(self.picking_id)

    def action_view_cod(self):
        self._check_gdex_operator_access()
        return {
            'type': 'ir.actions.act_window', 'name': _('GDEX COD Consignments'),
            'res_model': 'motogene.gdex.cod.consignment', 'view_mode': 'list,form',
            'domain': [('picking_id', '=', self.picking_id.id)],
            'target': 'current',
        }
