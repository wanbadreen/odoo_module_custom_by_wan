from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests

DEMO = 'https://myopenapi.gdexpress.com/api/demo/prime'
# Production endpoint deliberately unavailable until separately verified and approved.

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gdex_normal_environment = fields.Selection([('sandbox','Sandbox'),('production','Production (locked)')], default='sandbox', config_parameter='motogene_gdex_normal.environment', required=True)
    gdex_normal_sandbox_token = fields.Char(string='Sandbox User Token', config_parameter='motogene_gdex_normal.sandbox_token', groups='base.group_system')
    gdex_normal_sandbox_key = fields.Char(string='Testing Subscription Key', config_parameter='motogene_gdex_normal.sandbox_key', groups='base.group_system')
    gdex_normal_account = fields.Char(string='GDEX Account No (optional)', config_parameter='motogene_gdex_normal.account_no', groups='base.group_system')

    def action_gdex_normal_test(self):
        self.ensure_one()
        params = self.env['ir.config_parameter'].sudo()
        if params.get_param('motogene_gdex_normal.environment', 'sandbox') != 'sandbox':
            raise UserError(_('Production API is locked pending separate approval.'))
        token = params.get_param('motogene_gdex_normal.sandbox_token')
        key = params.get_param('motogene_gdex_normal.sandbox_key')
        if not token or not key:
            raise UserError(_('Set Sandbox User Token and Testing Subscription Key first.'))
        try:
            res = requests.get(DEMO+'/GetAccountStatus', headers={'ApiToken':token,'Subscription-Key':key},timeout=20)
            data = res.json()
            if res.status_code != 200 or data.get('s') != 'success':
                raise UserError(_('Connection rejected: HTTP %s, %s') % (res.status_code, data.get('e') or 'Unknown error'))
        except (requests.RequestException, ValueError) as e:
            raise UserError(_('Connection test failed (%s).') % type(e).__name__) from e
        return {'type':'ir.actions.client','tag':'display_notification','params':{'title':_('GDEX Sandbox Connected'),'message':_('Account status: %s') % data.get('r'),'type':'success','sticky':False}}
