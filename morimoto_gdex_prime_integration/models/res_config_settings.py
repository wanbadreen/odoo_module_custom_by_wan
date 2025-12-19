from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    gdex_account_no = fields.Char(
        related="company_id.gdex_account_no",
        readonly=False,
    )
    gdex_api_token_sandbox = fields.Char(
        related="company_id.gdex_api_token_sandbox",
        readonly=False,
    )
    gdex_api_token_production = fields.Char(
        related="company_id.gdex_api_token_production",
        readonly=False,
    )
    gdex_environment = fields.Selection(
        related="company_id.gdex_environment",
        readonly=False,
    )
    gdex_base_url = fields.Char(
        related="company_id.gdex_base_url",
        readonly=False,
    )
