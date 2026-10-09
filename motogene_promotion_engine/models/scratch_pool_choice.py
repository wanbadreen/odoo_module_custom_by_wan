# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ScratchPoolChoiceProgram(models.Model):
    _inherit = 'motogene.promotion.program'

    # Relational UI over existing prefix settings: upgrades and setup transfer
    # retain their original values without resetting serial counters/history.
    scratch_vip_pool_id = fields.Many2one(
        'motogene.scratch.serial.pool', string='VIP Bonus Card Type',
        compute='_compute_card_pool_choices', inverse='_inverse_vip_pool')
    scratch_extra_pool_id = fields.Many2one(
        'motogene.scratch.serial.pool', string='Default Extra Spend Card Type',
        compute='_compute_card_pool_choices', inverse='_inverse_extra_pool')
    scratch_extra_allowed_pool_ids = fields.Many2many(
        'motogene.scratch.serial.pool', string='Allowed Extra Spend Card Types',
        compute='_compute_card_pool_choices', inverse='_inverse_allowed_pools')

    @api.depends('scratch_vip_prefix', 'scratch_extra_prefix', 'scratch_extra_allowed_prefixes',
                 'scratch_serial_pool_ids', 'scratch_serial_pool_ids.prefix')
    def _compute_card_pool_choices(self):
        for program in self:
            pools = program.scratch_serial_pool_ids
            program.scratch_vip_pool_id = pools.filtered(lambda p: p.prefix == program.scratch_vip_prefix)[:1]
            program.scratch_extra_pool_id = pools.filtered(lambda p: p.prefix == program.scratch_extra_prefix)[:1]
            program.scratch_extra_allowed_pool_ids = pools.filtered(lambda p: p.prefix in program._scratch_topup_prefixes())

    def _validate_selected_pools(self, pools):
        self.ensure_one()
        if any(p.program_id != self for p in pools):
            raise ValidationError(_('Select card types from this promotion serial pools only.'))

    def _inverse_vip_pool(self):
        for program in self:
            program._validate_selected_pools(program.scratch_vip_pool_id)
            program.scratch_vip_prefix = program.scratch_vip_pool_id.prefix or False

    def _inverse_extra_pool(self):
        for program in self:
            program._validate_selected_pools(program.scratch_extra_pool_id)
            program.scratch_extra_prefix = program.scratch_extra_pool_id.prefix or False

    def _inverse_allowed_pools(self):
        for program in self:
            program._validate_selected_pools(program.scratch_extra_allowed_pool_ids)
            program.scratch_extra_allowed_prefixes = ','.join(sorted(program.scratch_extra_allowed_pool_ids.mapped('prefix')))

    def _validate_scratch_pool_setup(self):
        for program in self.filtered(lambda p: p.reward_type == 'scratch_cards'):
            available = set(program.scratch_serial_pool_ids.mapped('prefix'))
            needed = {program.scratch_vip_prefix, program.scratch_extra_prefix}
            needed.update(program._scratch_topup_prefixes())
            for package in program.scratch_package_line_ids:
                needed.update(p.strip().upper() for p in (package.card_prefixes or '').split(',') if p.strip())
            if not available or not all(needed) or not needed.issubset(available):
                raise ValidationError(_(
                    'Scratch Card Setup Required: Please configure the Scratch Card Serial Pools, '
                    'then select valid VIP, default extra spend and package card types before activating this promotion.'))

    def action_activate(self):
        self._validate_scratch_pool_setup()
        return super().action_activate()
