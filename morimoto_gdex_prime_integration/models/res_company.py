from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    gdex_account_no = fields.Char(string="GDEX Account No")
    gdex_api_token_sandbox = fields.Char(string="GDEX API Token (Sandbox)")
    gdex_api_token_production = fields.Char(string="GDEX API Token (Production)")
    gdex_environment = fields.Selection(
        [
            ("sandbox", "Sandbox/Demo"),
            ("production", "Production"),
        ],
        string="GDEX Environment",
        default="sandbox",
    )
    gdex_base_url = fields.Char(
        string="GDEX Base URL",
        default="https://myopenapi.gdexpress.com/api/demo/prime",
        help="Override the base URL if needed. Default uses demo endpoint.",
    )
