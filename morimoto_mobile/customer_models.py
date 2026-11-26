from datetime import timedelta
from odoo import api, fields, models


class CustomerAccount(models.Model):
    _name = 'morimoto.mobile.account'
    _description = 'Mobile customer identity'
    # No public ACLs: only the guarded gateway controllers may access these models.
    name = fields.Char(required=True)
    email = fields.Char(required=True, index=True)
    phone = fields.Char(index=True)
    pending_phone = fields.Char()
    password_hash = fields.Char(copy=False)
    verified = fields.Boolean(default=False, copy=False)
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one('res.partner', ondelete='restrict', copy=False)
    shipping_partner_id = fields.Many2one('res.partner', ondelete='restrict', copy=False)
    version = fields.Integer(default=1, copy=False)
    token_hash = fields.Char(index=True, copy=False)
    token_kind = fields.Char(copy=False)
    token_expires = fields.Datetime(copy=False)
    _email_unique = models.Constraint("unique(email)", "Email already registered.")
    _phone_unique = models.Constraint("unique(phone)", "Phone already linked.")


class CustomerSession(models.Model):
    _name = 'morimoto.mobile.session'
    _description = 'Mobile customer session'
    account_id = fields.Many2one('morimoto.mobile.account', required=True, ondelete='cascade')
    token_hash = fields.Char(required=True, index=True, copy=False)
    version = fields.Integer(required=True)
    expires = fields.Datetime(required=True)
    _token_unique = models.Constraint("unique(token_hash)", "Session already exists.")

    @api.autovacuum
    def _remove_expired(self):
        self.sudo().search([('expires', '<', fields.Datetime.now())]).unlink()


class CustomerRate(models.Model):
    _name = 'morimoto.mobile.rate'
    _description = 'Mobile authentication rate counter'
    key = fields.Char(required=True, index=True)
    count = fields.Integer(default=0)
    expires = fields.Datetime(required=True)
    _key_unique = models.Constraint("unique(key)", "Counter already exists.")

    @api.autovacuum
    def _remove_expired(self):
        self.sudo().search([('expires', '<', fields.Datetime.now() - timedelta(days=1))]).unlink()
