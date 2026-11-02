from odoo import fields, models, _
from odoo.exceptions import AccessError, UserError


class GdexCancelWizard(models.TransientModel):
    _name = 'motogene.gdex.cancel.wizard'
    _description = 'Confirm Cancellation of a GDEX AWB'

    picking_id = fields.Many2one('stock.picking', string='Delivery Order', required=True, readonly=True)
    consignment_id = fields.Many2one('motogene.gdex.consignment', required=True, readonly=True)
    awb = fields.Char(related='consignment_id.awb', string='AWB', readonly=True)
    api_environment = fields.Selection(related='consignment_id.api_environment', readonly=True)
    cancellation_reason = fields.Char(required=True, string='Cancellation Reason')

    def action_confirm_cancel(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can cancel GDEX AWBs.'))
        reason = (self.cancellation_reason or '').strip()
        if not reason:
            raise UserError(_('Enter a cancellation reason before cancelling.'))
        consignment = self.consignment_id.sudo()
        if (consignment.picking_id != self.picking_id
                or consignment.state != 'success'
                or not consignment.awb
                or self.picking_id.mori_awb_no != consignment.awb):
            raise UserError(_('The Delivery Order AWB has changed. Close and reopen cancellation.'))
        # The existing cancellation service commits a reservation in a private
        # cursor. Pass the reason through context so it is recorded in that
        # cursor, rather than relying on an uncommitted wizard transaction.
        return consignment.with_context(gdex_cancel_reason=reason).action_cancel_consignment()
