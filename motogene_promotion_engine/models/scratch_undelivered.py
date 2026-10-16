# -*- coding: utf-8 -*-
from collections import Counter
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ScratchUndeliveredCard(models.Model):
    _inherit = 'motogene.scratch.picking.card'

    undelivered_released_at = fields.Datetime(readonly=True, copy=False, string='Undelivered Card Released At')
    undelivered_released_by_id = fields.Many2one('res.users', readonly=True, copy=False, string='Undelivered Card Released By')
    undelivered_release_reason = fields.Text(readonly=True, copy=False, string='Undelivered Release Reason')

    @api.model_create_multi
    def create(self, vals_list):
        audit = {'undelivered_released_at', 'undelivered_released_by_id', 'undelivered_release_reason'}
        if any(set(vals) & audit for vals in vals_list):
            raise UserError(_('Release audit is created only by Release Undelivered Cards.'))
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) & {'undelivered_released_at', 'undelivered_released_by_id', 'undelivered_release_reason'}:
            raise UserError(_('Use Release Undelivered Cards to record the release audit.'))
        if vals.get('state') == 'released' and any(c.state == 'sent' for c in self):
            raise UserError(_('Use Release Undelivered Cards for a validated delivery.'))
        if set(vals) & {'state', 'sale_id', 'picking_id', 'serial_number', 'program_id', 'prefix'} and any(c.undelivered_released_at for c in self):
            raise UserError(_('Recovered card history cannot be changed. A new allocation creates a separate history record.'))
        return super().write(vals)

    def _release_undelivered(self, reason):
        # Called only after the wizard has checked access, confirmations and locks.
        if any(c.state != 'sent' or c.redemption_id or c.return_picking_id for c in self):
            raise UserError(_('Only dispatched, unredeemed cards without a physical return can be released.'))
        return super().write({'state': 'released', 'undelivered_released_at': fields.Datetime.now(),
                              'undelivered_released_by_id': self.env.user.id,
                              'undelivered_release_reason': reason})


class ScratchUndeliveredSale(models.Model):
    _inherit = 'sale.order'

    def _scratch_order_types(self):
        types = super()._scratch_order_types()
        # These cards were deliberately withdrawn from the original order.
        # Count their original entitlement even when an alternative was issued.
        recovered = Counter(c.original_prefix or c.prefix for c in self.scratch_card_line_ids
                            if c.undelivered_released_at and c.state == 'released')
        result = []
        for prefix in types:
            if recovered[prefix]:
                recovered[prefix] -= 1
            else:
                result.append(prefix)
        return result

    @api.depends('scratch_total_cards', 'scratch_card_line_ids.state', 'state',
                 'scratch_card_line_ids.undelivered_released_at')
    def _compute_scratch_pending_cards(self):
        super()._compute_scratch_pending_cards()
        for order in self:
            recovered = len(order.scratch_card_line_ids.filtered(lambda c: c.state == 'released' and c.undelivered_released_at))
            order.scratch_pending_cards = max(0, order.scratch_pending_cards - recovered)


class ScratchUndeliveredPicking(models.Model):
    _inherit = 'stock.picking'

    scratch_can_release_undelivered = fields.Boolean(compute='_compute_can_release_undelivered')
    scratch_undelivered_history_ids = fields.One2many(
        'motogene.scratch.picking.card', 'picking_id', string='Undelivered Card Release History',
        domain=[('undelivered_released_at', '!=', False)], readonly=True)

    @api.depends('state', 'picking_type_code', 'scratch_return_source_id', 'scratch_card_line_ids.state')
    def _compute_can_release_undelivered(self):
        for picking in self:
            picking.scratch_can_release_undelivered = bool(
                picking.state == 'done' and picking.picking_type_code == 'outgoing'
                and not picking.scratch_return_source_id
                and picking.scratch_card_line_ids.filtered(lambda c: c.state == 'sent'))

    def action_release_undelivered_cards(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.env.user.has_group('stock.group_stock_user') or not self.scratch_can_release_undelivered:
            raise UserError(_('Release is only available to Operations on a completed outgoing delivery with dispatched cards.'))
        wizard = self.env['motogene.scratch.undelivered.wizard'].create({'picking_id': self.id})
        return {'type': 'ir.actions.act_window', 'name': _('Release Undelivered Scratch Cards'),
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new'}


class ScratchUndeliveredWizard(models.TransientModel):
    _name = 'motogene.scratch.undelivered.wizard'
    _description = 'Release Undelivered Scratch Cards'

    picking_id = fields.Many2one('stock.picking', required=True, readonly=True, string='Delivery Order')
    partner_id = fields.Many2one(related='picking_id.partner_id', string='Customer')
    available_card_ids = fields.Many2many('motogene.scratch.picking.card', compute='_compute_available')
    card_ids = fields.Many2many('motogene.scratch.picking.card', string='Physical Cards Recovered', required=True)
    reason = fields.Text(required=True)
    not_handed_over = fields.Boolean(string='Parcel has not been handed over to the courier or customer')
    cards_unscratched = fields.Boolean(string='Selected cards are physically recovered and remain unscratched')

    @api.depends('picking_id', 'picking_id.scratch_card_line_ids.state')
    def _compute_available(self):
        for wizard in self:
            wizard.available_card_ids = wizard.picking_id.scratch_card_line_ids.filtered(lambda c: c.state == 'sent')

    def action_confirm(self):
        self.ensure_one()
        if not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_('Only Operations users can release undelivered cards.'))
        picking = self.picking_id.exists()
        picking.check_access_rights('write')
        picking.check_access_rule('write')
        cards = self.card_ids.exists()
        cards.check_access_rights('write')
        cards.check_access_rule('write')
        if not cards or not (self.reason or '').strip() or not self.not_handed_over or not self.cards_unscratched:
            raise UserError(_('Select recovered cards, enter a reason and complete both physical confirmations.'))
        orders = cards.mapped('sale_id').sorted('id')
        for order in orders:
            self.env.cr.execute('SELECT id FROM sale_order WHERE id = %s FOR UPDATE', [order.id])
        self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [picking.id])
        cards.flush_recordset()
        self.env.cr.execute('SELECT id FROM motogene_scratch_picking_card WHERE id IN %s ORDER BY id FOR UPDATE', [tuple(sorted(cards.ids))])
        picking.invalidate_recordset()
        cards.invalidate_recordset()
        if picking.state != 'done' or picking.picking_type_code != 'outgoing' or picking.scratch_return_source_id:
            raise UserError(_('Select the completed original outgoing delivery.'))
        if any(c.picking_id != picking or c.sale_id.company_id != picking.company_id for c in cards):
            raise UserError(_('Every selected card must belong to this delivery and company.'))
        # A card selected on an open physical return must first be removed there.
        pending_returns = self.env['stock.picking'].search_count([
            ('scratch_return_card_ids', 'in', cards.ids), ('state', '!=', 'cancel')])
        if pending_returns:
            raise UserError(_('A selected card is recorded under Physical Cards Received. Remove it from the pending return before releasing it as undelivered.'))
        cards._release_undelivered(self.reason.strip())
        orders.invalidate_recordset(['scratch_card_line_ids', 'scratch_pending_cards'])
        picking.invalidate_recordset(['scratch_card_line_ids', 'scratch_can_release_undelivered', 'scratch_undelivered_history_ids'])
        picking.message_post(body=_('Undelivered scratch cards released: %(serials)s. Reason: %(reason)s. Released by: %(user)s.') % {
            'serials': ', '.join(cards.mapped('serial_number')), 'reason': self.reason.strip(), 'user': self.env.user.display_name})
        return {'type': 'ir.actions.act_window_close'}
