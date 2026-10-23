from odoo import models, _
from odoo.exceptions import UserError


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
