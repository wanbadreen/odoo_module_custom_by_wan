import requests
from odoo import models, fields, _
from odoo.exceptions import UserError

DEMO = 'https://myopenapi.gdexpress.com/api/demo/prime'
PRODUCTION = 'https://myopenapi.gdexpress.com/api/prime'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gdex_normal_environment = fields.Selection(
        [('sandbox', 'Sandbox'), ('production', 'Production (AWB creation locked)')],
        default='sandbox', config_parameter='motogene_gdex_normal.environment',
        required=True,
    )
    gdex_normal_sandbox_token = fields.Char(
        string='Sandbox User Token', config_parameter='motogene_gdex_normal.sandbox_token',
        groups='base.group_system',
    )
    gdex_normal_sandbox_key = fields.Char(
        string='Testing Subscription Key', config_parameter='motogene_gdex_normal.sandbox_key',
        groups='base.group_system',
    )
    gdex_normal_production_token = fields.Char(
        string='Production User Token', config_parameter='motogene_gdex_normal.production_token',
        groups='base.group_system',
    )
    gdex_normal_production_key = fields.Char(
        string='Production Subscription Key',
        config_parameter='motogene_gdex_normal.production_key',
        groups='base.group_system',
    )
    gdex_normal_account = fields.Char(
        string='GDEX Account No (optional)', config_parameter='motogene_gdex_normal.account_no',
        groups='base.group_system',
    )

    def action_gdex_normal_test(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise UserError(_('Only administrators can test GDEX credentials.'))
        params = self.env['ir.config_parameter'].sudo()
        environment = params.get_param('motogene_gdex_normal.environment', 'sandbox')
        if environment not in ('sandbox', 'production'):
            raise UserError(_('Unknown GDEX environment.'))
        prefix = 'motogene_gdex_normal.'
        if environment == 'sandbox':
            endpoint = DEMO
            token = params.get_param(prefix + 'sandbox_token')
            key = params.get_param(prefix + 'sandbox_key')
        else:
            endpoint = PRODUCTION
            token = params.get_param(prefix + 'production_token')
            key = params.get_param(prefix + 'production_key')
        if not token or not key:
            raise UserError(_('Please save the %s token and subscription key first.') % environment)
        try:
            response = requests.get(
                endpoint + '/GetAccountStatus',
                headers={'ApiToken': token, 'Subscription-Key': key},
                timeout=(10, 20),
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise UserError(_('GDEX connection test failed (%s).') % type(exc).__name__) from exc
        if not isinstance(data, dict) or response.status_code != 200 or data.get('s') != 'success':
            raise UserError(_('GDEX %s connection rejected (HTTP %s).') % (environment, response.status_code))
        if str(data.get('r', '')).lower() != 'active':
            raise UserError(_('GDEX %s account is not Active.') % environment)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('GDEX %s Connected') % environment.title(),
                'message': _('Account status: Active. Production AWB creation remains locked.'),
                'type': 'success', 'sticky': False,
            },
        }
