from odoo import models, _
from odoo.exceptions import AccessError, UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

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
