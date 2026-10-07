# -*- coding: utf-8 -*-
from collections import Counter
from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError


class ScratchReplacementProgram(models.Model):
    _inherit = 'motogene.promotion.program'

    scratch_replacement_policy = fields.Selection([
        ('any', 'Any Available Type'), ('allowed', 'Allowed Types Only'),
        ('none', 'No Replacement'),
    ], string='Card Replacement Policy', required=True, default='any',
        help='Applies to new replacements only. Existing allocations are preserved.')

    scratch_replacement_history = fields.Text(string='Replacement Setup History', readonly=True, copy=False)

    def _log_replacement_setup(self, message):
        for program in self:
            program.invalidate_recordset(['scratch_replacement_history'])
            entry = '%s | %s | %s' % (fields.Datetime.now(), self.env.user.display_name, message)
            super(ScratchReplacementProgram, program).write({
                'scratch_replacement_history': (program.scratch_replacement_history or '') + entry + '\n'})

    def write(self, vals):
        if 'scratch_replacement_history' in vals:
            raise UserError(_('Replacement setup history cannot be edited.'))
        if 'scratch_replacement_policy' in vals:
            for program in self.sorted('id'):
                self.env.cr.execute('SELECT id FROM motogene_promotion_program WHERE id = %s FOR UPDATE', [program.id])
        previous = {p.id: p.scratch_replacement_policy for p in self}
        result = super().write(vals)
        if 'scratch_replacement_policy' in vals:
            for p in self:
                if previous[p.id] != p.scratch_replacement_policy:
                    p._log_replacement_setup('Policy: %s -> %s' % (previous[p.id], p.scratch_replacement_policy))
        return result


class ScratchReplacementPool(models.Model):
    _inherit = 'motogene.scratch.serial.pool'
    _rec_name = 'prefix'

    replacement_policy = fields.Selection(related='program_id.scratch_replacement_policy')
    allowed_replacement_pool_ids = fields.Many2many(
        'motogene.scratch.serial.pool', 'motogene_scratch_replacement_pool_rel',
        'source_pool_id', 'replacement_pool_id', string='Allowed Replacement Types')

    @api.constrains('allowed_replacement_pool_ids', 'program_id', 'prefix')
    def _check_replacement_pools(self):
        for pool in self:
            if any(p == pool or p.program_id != pool.program_id for p in pool.allowed_replacement_pool_ids):
                raise ValidationError(_('Replacement types must be other prefixes in the same promotion.'))

    def write(self, vals):
        if 'allowed_replacement_pool_ids' in vals:
            for program in self.mapped('program_id').sorted('id'):
                self.env.cr.execute('SELECT id FROM motogene_promotion_program WHERE id = %s FOR UPDATE', [program.id])
            for pool in self.sorted('id'):
                self.env.cr.execute('SELECT id FROM motogene_scratch_serial_pool WHERE id = %s FOR UPDATE', [pool.id])
        previous = {p.id: sorted(p.allowed_replacement_pool_ids.mapped('prefix')) for p in self}
        result = super().write(vals)
        if 'allowed_replacement_pool_ids' in vals:
            for pool in self:
                current = sorted(pool.allowed_replacement_pool_ids.mapped('prefix'))
                if previous[pool.id] != current:
                    pool.program_id._log_replacement_setup('Type %s allowed replacements: %s -> %s' % (pool.prefix, previous[pool.id], current))
        return result

    def _scratch_available_count(self):
        self.ensure_one()
        Card = self.env['motogene.scratch.picking.card']
        Card.flush_model(['state', 'serial_number', 'program_id', 'prefix'])
        self.invalidate_recordset(['next_number', 'last_number'])
        self.env.cr.execute('''
            SELECT COUNT(DISTINCT released.serial_number)
            FROM motogene_scratch_picking_card released
            WHERE released.program_id = %s AND released.prefix = %s
              AND released.state = 'released'
              AND NOT EXISTS (SELECT 1 FROM motogene_scratch_picking_card active
                WHERE active.serial_number = released.serial_number AND active.state != 'released')
        ''', [self.program_id.id, self.prefix])
        return max(0, self.last_number - self.next_number + 1) + self.env.cr.fetchone()[0]

    def _replacement_allowed(self, replacement):
        self.ensure_one()
        policy = self.program_id.scratch_replacement_policy
        return bool(replacement and replacement != self and replacement.program_id == self.program_id
                    and policy != 'none' and (policy == 'any' or replacement in self.allowed_replacement_pool_ids))


class ScratchReplacementCard(models.Model):
    _inherit = 'motogene.scratch.picking.card'

    original_prefix = fields.Char(string='Replaces Card Type', readonly=True, copy=False)
    replacement_by_id = fields.Many2one('res.users', string='Replacement Selected By', readonly=True, copy=False)
    replacement_at = fields.Datetime(string='Replacement Allocated At', readonly=True, copy=False)

    def write(self, vals):
        if set(vals) & {'original_prefix', 'replacement_by_id', 'replacement_at'}:
            raise UserError(_('Replacement allocation history cannot be edited.'))
        return super().write(vals)


class ScratchAlternativeSale(models.Model):
    _inherit = 'sale.order'

    def _allocate_scratch_alternatives(self, choices):
        self.ensure_one()
        program = self.scratch_program_id
        if not program or (program.company_id and program.company_id != self.company_id):
            raise UserError(_('Select replacements from the order promotion and company.'))
        program.invalidate_recordset(['scratch_replacement_policy'])
        expected = Counter(self._scratch_order_types())
        cards = self.scratch_card_line_ids.filtered(lambda c: c.state != 'released')
        current = Counter(c.original_prefix or c.prefix for c in cards)
        missing = expected - current
        pools = {p.prefix: p for p in program.scratch_serial_pool_ids}
        selected = Counter()
        validated = []
        for choice in choices:
            if choice.get('sale_id') != self.id:
                continue
            source = pools.get(choice.get('original_prefix'))
            target = self.env['motogene.scratch.serial.pool'].browse(choice.get('pool_id', 0)).exists()
            qty = choice.get('quantity')
            if not source or len(target) != 1 or not source._replacement_allowed(target):
                raise UserError(_('Replacement policy changed or this card type is not allowed. Reopen the alternative card selection.'))
            if type(qty) is not int or qty <= 0:
                raise UserError(_('Replacement quantities must be positive whole numbers.'))
            selected[source.prefix] += qty
            validated.append((source, target, qty))
        if selected != missing:
            raise UserError(_('The shortage has changed. Reopen the warning and select replacements for the current missing cards.'))
        for source, target, qty in validated:
            for index in range(qty):
                card = self.env['motogene.scratch.picking.card'].create({
                    'sale_id': self.id, 'program_id': program.id, 'prefix': target.prefix,
                    'serial_number': target.reserve_serial(), 'original_prefix': source.prefix,
                    'replacement_by_id': self.env.user.id, 'replacement_at': fields.Datetime.now(),
                })
                self.message_post(body=_('Scratch card %(serial)s allocated instead of type %(original)s.') % {
                    'serial': card.serial_number, 'original': source.prefix})
        self._assign_scratch_cards_to_delivery()


class ScratchAlternativeShortage(models.TransientModel):
    _inherit = 'motogene.scratch.shortage.wizard'

    def action_alternative_cards(self):
        self.ensure_one()
        orders = self.order_ids.exists()
        orders.check_access_rights('write')
        orders.check_access_rule('write')
        if len(orders) != 1:
            raise UserError(_('Select alternative cards for one quotation at a time.'))
        if not orders or any(o.state not in ('draft', 'sent') for o in orders):
            raise UserError(_('Close this warning and confirm the quotation again.'))
        # Snapshot only entitlement, not serial reservations or order confirmation.
        orders._set_scratch_card_counts()
        budgets, lines = {}, []
        for order in orders.sorted('id'):
            program = order.scratch_program_id
            if not order.scratch_total_cards:
                continue
            if program.scratch_replacement_policy == 'none':
                raise UserError(_('Card replacements are disabled in this promotion setup.'))
            if program.company_id and program.company_id != order.company_id:
                raise UserError(_('Promotion company does not match the Sales Order.'))
            pools = {p.prefix: p for p in program.scratch_serial_pool_ids}
            current = Counter(c.original_prefix or c.prefix for c in order.scratch_card_line_ids if c.state != 'released')
            needed = Counter(order._scratch_order_types()) - current
            for prefix, qty in sorted(needed.items()):
                pool = pools.get(prefix)
                if not pool:
                    raise UserError(_('Configure the serial pool for type %s.') % prefix)
                available = budgets.setdefault(pool.id, pool._scratch_available_count())
                allocated = min(qty, available)
                budgets[pool.id] -= allocated
                if qty > allocated:
                    lines.append(Command.create({'sale_id': order.id, 'source_pool_id': pool.id,
                                                 'missing_quantity': qty - allocated,
                                                 'quantity': qty - allocated}))
        if not lines:
            raise UserError(_('Original card stock is now sufficient. Close this warning and confirm again.'))
        wizard = self.env['motogene.scratch.alternative.wizard'].create({
            'order_ids': [Command.set(orders.ids)], 'line_ids': lines,
        })
        return {'type': 'ir.actions.act_window', 'name': _('Select Alternative Cards'),
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new',
                'view_id': self.env.ref('motogene_promotion_engine.view_scratch_alternative_wizard').id}


class ScratchAlternativeWizard(models.TransientModel):
    _name = 'motogene.scratch.alternative.wizard'
    _description = 'Select Alternative Scratch Cards'

    order_ids = fields.Many2many('sale.order', readonly=True, required=True)
    line_ids = fields.One2many('motogene.scratch.alternative.line', 'wizard_id')

    def _replacement_stock(self, pool):
        if not pool:
            return 0
        available = pool._scratch_available_count()
        for order in self.order_ids.filtered(lambda o: o.scratch_program_id == pool.program_id):
            cards = order.scratch_card_line_ids.filtered(lambda c: c.state != 'released')
            needed = Counter(order._scratch_order_types()) - Counter(c.original_prefix or c.prefix for c in cards)
            available = max(0, available - needed[pool.prefix])
        return available

    def action_confirm(self):
        self.ensure_one()
        orders = self.order_ids.exists()
        orders.check_access_rights('write')
        orders.check_access_rule('write')
        if len(orders) != 1:
            raise UserError(_('Select alternative cards for one quotation at a time.'))
        if not orders or any(o.state not in ('draft', 'sent') for o in orders):
            raise UserError(_('The quotation changed. Confirm it again.'))
        # Lock orders, then all affected programs/pools consistently. Policy and
        # stock are rechecked under these locks, not trusted from popup values.
        for order in orders.sorted('id'):
            self.env.cr.execute('SELECT id FROM sale_order WHERE id = %s FOR UPDATE', [order.id])
        orders._set_scratch_card_counts()
        programs = orders.mapped('scratch_program_id').sorted('id')
        for program in programs:
            self.env.cr.execute('SELECT id FROM motogene_promotion_program WHERE id = %s FOR UPDATE', [program.id])
        pools = programs.mapped('scratch_serial_pool_ids').sorted('id')
        for pool in pools:
            self.env.cr.execute('SELECT id FROM motogene_scratch_serial_pool WHERE id = %s FOR UPDATE', [pool.id])
        pools.invalidate_recordset()
        programs.invalidate_recordset(['scratch_replacement_policy'])
        choices = []
        for line in self.line_ids:
            if line.sale_id not in orders or line.source_pool_id.program_id != line.sale_id.scratch_program_id:
                raise UserError(_('Alternative selection does not belong to this quotation.'))
            if not line.replacement_pool_id or line.quantity <= 0:
                raise UserError(_('Select a replacement type and positive quantity for every row.'))
            choices.append({'sale_id': line.sale_id.id, 'original_prefix': line.source_pool_id.prefix,
                            'pool_id': line.replacement_pool_id.id, 'quantity': line.quantity})
        with self.env.cr.savepoint():
            orders.with_context(motogene_allow_scratch_shortage=True,
                                motogene_scratch_shortage_mode='alternative',
                                motogene_scratch_replacements=choices)._confirm_with_scratch_cards()
        return {'type': 'ir.actions.act_window_close'}


class ScratchAlternativeLine(models.TransientModel):
    _name = 'motogene.scratch.alternative.line'
    _description = 'Alternative Scratch Card Choice'

    wizard_id = fields.Many2one('motogene.scratch.alternative.wizard', required=True, ondelete='cascade')
    sale_id = fields.Many2one('sale.order', required=True, string='Sales Order')
    source_pool_id = fields.Many2one('motogene.scratch.serial.pool', required=True, string='Original Type')
    missing_quantity = fields.Integer(string='Missing Original Cards')
    quantity = fields.Integer(required=True, default=1, string='Replacement Quantity')
    replacement_pool_id = fields.Many2one('motogene.scratch.serial.pool', string='Replacement Type')
    allowed_pool_ids = fields.Many2many('motogene.scratch.serial.pool', compute='_compute_allowed_pools')
    available_quantity = fields.Integer(compute='_compute_available', string='Stock Now')
    prize_description = fields.Char(related='replacement_pool_id.prize_description', string='Replacement Prize')

    @api.depends('source_pool_id', 'source_pool_id.program_id.scratch_replacement_policy',
                 'source_pool_id.allowed_replacement_pool_ids')
    def _compute_allowed_pools(self):
        for line in self:
            source = line.source_pool_id
            line.allowed_pool_ids = source.program_id.scratch_serial_pool_ids.filtered(
                lambda p: source._replacement_allowed(p) and line.wizard_id._replacement_stock(p) > 0)

    @api.depends('replacement_pool_id')
    def _compute_available(self):
        for line in self:
            line.available_quantity = line.wizard_id._replacement_stock(line.replacement_pool_id)

    def action_split(self):
        self.ensure_one()
        if self.quantity <= 1:
            raise UserError(_('Only quantities greater than one can be split.'))
        self.quantity -= 1
        self.create({'wizard_id': self.wizard_id.id, 'sale_id': self.sale_id.id,
                     'source_pool_id': self.source_pool_id.id,
                     'missing_quantity': self.missing_quantity, 'quantity': 1})
        return {'type': 'ir.actions.act_window', 'name': _('Select Alternative Cards'),
                'res_model': self.wizard_id._name, 'res_id': self.wizard_id.id,
                'view_mode': 'form', 'target': 'new',
                'view_id': self.env.ref('motogene_promotion_engine.view_scratch_alternative_wizard').id}
