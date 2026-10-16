# -*- coding: utf-8 -*-
import json
from collections import Counter
from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError


class ScratchTopupProgram(models.Model):
    _inherit = 'motogene.promotion.program'

    scratch_extra_allowed_prefixes = fields.Char(
        string='Allowed Extra Spend Card Types',
        help='Optional comma-separated prefixes, e.g. D,B,G. Leave blank to allocate the default type automatically.')

    def _scratch_topup_prefixes(self):
        self.ensure_one()
        return list(dict.fromkeys(p.strip().upper() for p in (self.scratch_extra_allowed_prefixes or '').split(',') if p.strip()))

    @api.constrains('scratch_extra_allowed_prefixes', 'scratch_extra_prefix')
    def _check_topup_prefixes(self):
        for program in self:
            prefixes = program._scratch_topup_prefixes()
            if any(len(p) != 1 or not p.isalpha() for p in prefixes):
                raise ValidationError(_('Use single-letter card types separated by commas, e.g. D,B,G.'))
            if prefixes and program.scratch_extra_prefix not in prefixes:
                raise ValidationError(_('The default extra spend card type must be among the allowed types.'))


class ScratchTopupSale(models.Model):
    _inherit = 'sale.order'

    scratch_topup_selection = fields.Text(string='Extra Spend Card Selection', readonly=True, copy=False)

    def _scratch_topup_details(self):
        self.ensure_one()
        types = super()._scratch_order_types()
        program = self.scratch_program_id
        if not program:
            return [], 0
        included = sum(
            p.advertised_cards * int(line.product_uom_qty + 1e-9)
            for line in self.order_line
            for p in program.scratch_package_line_ids
            if line.product_id.product_tmpl_id == p.product_tmpl_id
            and program._is_paid_scratch_package_line(line))
        extra = self.scratch_base_cards - included
        return types[:included] + types[included + extra:], extra

    def _scratch_order_types(self):
        self.ensure_one()
        if not self.scratch_topup_selection:
            return super()._scratch_order_types()
        fixed, extra = self._scratch_topup_details()
        choices = json.loads(self.scratch_topup_selection)
        if any(type(q) is not int or q < 0 for q in choices.values()) or sum(choices.values()) != extra:
            raise UserError(_('Extra spend card entitlement changed. Confirm the quotation again and select the correct quantity.'))
        # Preserve confirmed allocations when the live promotion policy changes.
        if self.state in ('draft', 'sent') and set(choices) - set(self.scratch_program_id._scratch_topup_prefixes()):
            raise UserError(_('Allowed extra spend card types changed. Confirm the quotation again.'))
        return fixed + [prefix for prefix, qty in choices.items() for index in range(qty)]

    def action_confirm(self):
        if not self.env.context.get('motogene_topup_selected'):
            drafts = self.filtered(lambda o: o.state in ('draft', 'sent'))
            drafts._apply_motogene_promotions()
            drafts._set_scratch_card_counts()
            candidates = drafts.filtered(lambda o: o.scratch_program_id.scratch_extra_allowed_prefixes and o._scratch_topup_details()[1] > 0)
            if candidates:
                if len(self) != 1:
                    raise UserError(_('Confirm quotations with extra spend card choices one at a time.'))
                order = candidates
                program = order.scratch_program_id
                prefixes = program._scratch_topup_prefixes()
                pools = program.scratch_serial_pool_ids.filtered(lambda p: p.prefix in prefixes)
                if set(prefixes) != set(pools.mapped('prefix')):
                    raise UserError(_('Configure a serial pool for every allowed extra spend card type.'))
                fixed, extra = order._scratch_topup_details()
                wizard = self.env['motogene.scratch.topup.wizard'].create({
                    'sale_id': order.id, 'program_id': program.id,
                    'package_quantity': order.scratch_base_cards - extra,
                    'extra_quantity': extra, 'vip_quantity': order.scratch_vip_cards,
                    'line_ids': [Command.create({'pool_id': p.id, 'quantity': extra if p.prefix == program.scratch_extra_prefix else 0}) for p in pools],
                })
                return {'type': 'ir.actions.act_window', 'name': _('Scratch & Win Card Allocation'),
                        'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new'}
        return super().action_confirm()

    def action_cancel(self):
        result = super().action_cancel()
        self.with_context(motogene_skip_promotion_engine=True).write({'scratch_topup_selection': False})
        return result


class ScratchTopupWizard(models.TransientModel):
    _name = 'motogene.scratch.topup.wizard'
    _description = 'Scratch & Win Card Allocation'

    sale_id = fields.Many2one('sale.order', required=True, readonly=True, string='Sales Order')
    partner_id = fields.Many2one(related='sale_id.partner_id', string='Customer')
    program_id = fields.Many2one('motogene.promotion.program', required=True, readonly=True)
    package_quantity = fields.Integer(string='Cards Included in Promotion Packages', readonly=True)
    extra_quantity = fields.Integer(string='Additional Cards Based on Spending', readonly=True)
    vip_quantity = fields.Integer(string='VIP Member Bonus Cards', readonly=True)
    total_quantity = fields.Integer(compute='_compute_totals', string='Total Eligible Cards')
    selected_quantity = fields.Integer(compute='_compute_totals', string='Selected Additional Cards')
    line_ids = fields.One2many('motogene.scratch.topup.line', 'wizard_id')

    @api.depends('package_quantity', 'extra_quantity', 'vip_quantity', 'line_ids.quantity')
    def _compute_totals(self):
        for wizard in self:
            wizard.total_quantity = wizard.package_quantity + wizard.extra_quantity + wizard.vip_quantity
            wizard.selected_quantity = sum(wizard.line_ids.mapped('quantity'))

    def _available(self, pool):
        fixed, extra = self.sale_id._scratch_topup_details()
        current = Counter(c.original_prefix or c.prefix for c in self.sale_id.scratch_card_line_ids if c.state != 'released')
        needed = Counter(fixed) - current
        return max(0, pool._scratch_available_count() - needed[pool.prefix])

    def action_confirm(self):
        self.ensure_one()
        order = self.sale_id.exists()
        order.check_access_rights('write')
        order.check_access_rule('write')
        if not order or order.state not in ('draft', 'sent'):
            raise UserError(_('This quotation changed. Close this window and confirm it again.'))
        self.env.cr.execute('SELECT id FROM sale_order WHERE id = %s FOR UPDATE', [order.id])
        order.invalidate_recordset()
        order._apply_motogene_promotions()
        order._set_scratch_card_counts()
        if order.scratch_program_id != self.program_id:
            raise UserError(_('The promotion changed. Close this window and confirm again.'))
        self.env.cr.execute('SELECT id FROM motogene_promotion_program WHERE id = %s FOR UPDATE', [self.program_id.id])
        self.program_id.invalidate_recordset()
        pools = self.program_id.scratch_serial_pool_ids.sorted('id')
        for pool in pools:
            self.env.cr.execute('SELECT id FROM motogene_scratch_serial_pool WHERE id = %s FOR UPDATE', [pool.id])
        pools.invalidate_recordset()
        fixed, extra = order._scratch_topup_details()
        choices = Counter()
        for line in self.line_ids:
            if line.quantity < 0:
                raise UserError(_('Allocation quantities cannot be negative.'))
            if line.pool_id not in pools or line.pool_id.prefix not in self.program_id._scratch_topup_prefixes():
                raise UserError(_('The selected card type is no longer allowed. Reopen the allocation window.'))
            choices[line.pool_id.prefix] += line.quantity
        if sum(choices.values()) != extra:
            raise UserError(_('This Sales Order is eligible for %(eligible)s additional cards. You selected %(selected)s. Please adjust the allocation quantity to proceed.') % {'eligible': extra, 'selected': sum(choices.values())})
        # Existing shortage handling performs the final reservation under locks.
        # Persist choices so its available/without/alternative options use them.
        choices = {p: q for p, q in choices.items() if q}
        order.with_context(motogene_skip_promotion_engine=True).write({'scratch_topup_selection': json.dumps(choices, sort_keys=True)})
        result = order.with_context(motogene_topup_selected=True).action_confirm()
        return result if isinstance(result, dict) else {'type': 'ir.actions.act_window_close'}


class ScratchTopupLine(models.TransientModel):
    _name = 'motogene.scratch.topup.line'
    _description = 'Additional Scratch Card Selection'

    wizard_id = fields.Many2one('motogene.scratch.topup.wizard', required=True, ondelete='cascade')
    pool_id = fields.Many2one('motogene.scratch.serial.pool', required=True, readonly=True, string='Card Type')
    prize_description = fields.Char(related='pool_id.prize_description', string='Reward')
    available_quantity = fields.Integer(compute='_compute_available', string='Available Cards')
    quantity = fields.Integer(string='Quantity to Allocate', default=0)

    @api.depends('pool_id', 'wizard_id.sale_id')
    def _compute_available(self):
        for line in self:
            line.available_quantity = line.wizard_id._available(line.pool_id) if line.pool_id else 0
