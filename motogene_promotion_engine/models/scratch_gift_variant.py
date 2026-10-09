# -*- coding: utf-8 -*-
from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError


class ScratchVariantRewardProduct(models.Model):
    _inherit = 'motogene.scratch.reward.product'

    variant_mode = fields.Selection([
        ('fixed', 'Fixed Variant'), ('choose', 'Customer Chooses Variant'),
    ], string='Variant Selection', required=True, default='fixed')
    product_tmpl_id = fields.Many2one('product.template', string='Gift Product',
                                     domain=[('sale_ok', '=', True)], ondelete='restrict')

    @api.onchange('variant_mode', 'product_tmpl_id')
    def _onchange_variant_mode(self):
        if self.variant_mode == 'choose':
            if not self.product_tmpl_id and self.product_id:
                self.product_tmpl_id = self.product_id.product_tmpl_id
            self.product_id = False

    def _validate_gift_setup(self):
        for line in self:
            product = line.product_tmpl_id if line.variant_mode == 'choose' else line.product_id
            if not product or not product.active or not product.sale_ok or line.quantity <= 0:
                raise UserError(_('Configure a saleable gift product and a positive quantity for every reward row.'))
            company = line.pool_id.program_id.company_id
            if company and product.company_id and product.company_id != company:
                raise UserError(_('Gift products must belong to the promotion company or be shared.'))

    @api.constrains('variant_mode', 'product_id', 'product_tmpl_id', 'quantity', 'pool_id')
    def _check_variant_setup(self):
        self._validate_gift_setup()

    def _gift_variants(self, company):
        self.ensure_one()
        return self.env['product.product'].search([
            ('product_tmpl_id', '=', self.product_tmpl_id.id),
            ('active', '=', True), ('sale_ok', '=', True),
            ('company_id', 'in', [False, company.id]),
        ]) if self.variant_mode == 'choose' and self.product_tmpl_id else self.env['product.product']


def choice_commands(pool):
    return [Command.clear()] + [Command.create({'reward_line_id': line.id})
        for line in pool.reward_product_line_ids if pool.redemption_reward_type == 'products'
        and line.variant_mode == 'choose']


class ScratchVariantRedemption(models.Model):
    _inherit = 'motogene.scratch.redemption'

    gift_choice_ids = fields.One2many('motogene.scratch.gift.choice', 'redemption_id',
                                      string='Customer Gift Variants', copy=False)

    @api.onchange('card_id')
    def _onchange_gift_card(self):
        self.gift_choice_ids = choice_commands(self.pool_id)

    def action_load_gift_choices(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.state != 'draft':
            raise UserError(_('Variant choices are locked after redemption.'))
        self.gift_choice_ids = choice_commands(self.pool_id)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def write(self, vals):
        if 'gift_choice_ids' in vals and any(r.state != 'draft' for r in self):
            raise UserError(_('Variant choices are locked after redemption.'))
        return super().write(vals)

    def _selected_free_product_rewards(self, pool):
        self.ensure_one()
        required = pool.reward_product_line_ids.filtered(lambda line: line.variant_mode == 'choose')
        choices = self.gift_choice_ids
        if len(choices) != len(required) or set(choices.mapped('reward_line_id').ids) != set(required.ids):
            raise UserError(_('Gift setup changed or variant choices are missing. Reload the gift choices and select each variant.'))
        selected = {line.reward_line_id.id: line.product_id for line in choices}
        rewards = []
        for line in pool.reward_product_line_ids:
            product = selected.get(line.id) if line.variant_mode == 'choose' else line.product_id
            if line.variant_mode == 'choose' and (not product or product not in line._gift_variants(self.target_sale_id.company_id)):
                raise UserError(_('Select an active variant of the configured gift product for this company.'))
            rewards.append((product, line.quantity, 0))
        return rewards


class ScratchGiftChoice(models.Model):
    _name = 'motogene.scratch.gift.choice'
    _description = 'Scratch Redemption Gift Variant'

    redemption_id = fields.Many2one('motogene.scratch.redemption', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='redemption_id.company_id', store=True, index=True)
    reward_line_id = fields.Many2one('motogene.scratch.reward.product', ondelete='set null', string='Gift Setup Row')
    product_tmpl_id = fields.Many2one(related='reward_line_id.product_tmpl_id', string='Gift Product')
    quantity = fields.Float(related='reward_line_id.quantity', string='Configured Quantity')
    product_id = fields.Many2one('product.product', string='Selected Variant', ondelete='restrict')
    allowed_product_ids = fields.Many2many('product.product', compute='_compute_allowed_products')
    _sql_constraints = [('scratch_gift_choice_unique', 'UNIQUE(redemption_id, reward_line_id)',
                         'Select one variant per gift setup row.')]

    @api.depends('reward_line_id', 'redemption_id.target_sale_id.company_id')
    def _compute_allowed_products(self):
        for line in self:
            line.allowed_product_ids = line.reward_line_id._gift_variants(line.redemption_id.target_sale_id.company_id) if line.reward_line_id else False

    @api.model_create_multi
    def create(self, vals_list):
        parents = self.env['motogene.scratch.redemption'].browse([v.get('redemption_id') for v in vals_list if v.get('redemption_id')])
        parents._lock_redemptions()
        if any(p.state != 'draft' for p in parents):
            raise UserError(_('Variant choices are locked after redemption.'))
        return super().create(vals_list)

    def write(self, vals):
        parents = self.mapped('redemption_id')
        if vals.get('redemption_id'):
            parents |= self.env['motogene.scratch.redemption'].browse(vals['redemption_id'])
        parents._lock_redemptions()
        if any(p.state != 'draft' for p in parents):
            raise UserError(_('Variant choices are locked after redemption.'))
        return super().write(vals)

    def unlink(self):
        self.mapped('redemption_id')._lock_redemptions()
        if any(p.state != 'draft' for p in self.mapped('redemption_id')):
            raise UserError(_('Variant choices are locked after redemption.'))
        return super().unlink()


class ScratchVariantWizard(models.TransientModel):
    _inherit = 'motogene.scratch.redemption.wizard'

    gift_choice_ids = fields.One2many('motogene.scratch.gift.choice.wizard', 'wizard_id', string='Choose Gift Variants')

    @api.onchange('card_id')
    def _onchange_gift_card(self):
        self.gift_choice_ids = choice_commands(self.pool_id)

    @api.model_create_multi
    def create(self, vals_list):
        wizards = super().create(vals_list)
        for wizard, vals in zip(wizards, vals_list):
            if wizard.card_id and 'gift_choice_ids' not in vals:
                wizard.gift_choice_ids = choice_commands(wizard.pool_id)
        return wizards


class ScratchGiftChoiceWizard(models.TransientModel):
    _name = 'motogene.scratch.gift.choice.wizard'
    _description = 'Select Scratch Gift Variant'

    wizard_id = fields.Many2one('motogene.scratch.redemption.wizard', required=True, ondelete='cascade')
    reward_line_id = fields.Many2one('motogene.scratch.reward.product', required=True, ondelete='cascade', readonly=True)
    product_tmpl_id = fields.Many2one(related='reward_line_id.product_tmpl_id', string='Gift Product')
    quantity = fields.Float(related='reward_line_id.quantity', string='Gift Quantity')
    product_id = fields.Many2one('product.product', string='Selected Variant')
    allowed_product_ids = fields.Many2many('product.product', compute='_compute_allowed_products')

    @api.depends('reward_line_id', 'wizard_id.sale_id.company_id')
    def _compute_allowed_products(self):
        for line in self:
            line.allowed_product_ids = line.reward_line_id._gift_variants(line.wizard_id.sale_id.company_id) if line.reward_line_id else False
