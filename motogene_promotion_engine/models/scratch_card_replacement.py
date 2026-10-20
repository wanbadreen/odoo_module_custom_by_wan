# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .scratch_card import ScratchSerialShortage


REASONS = [
    ('damaged', 'Damaged / Already Scratched'),
    ('missing', 'Missing Card'),
    ('printing', 'Printing Defect'),
    ('other', 'Other'),
]


class ScratchCardReplacement(models.Model):
    _name = 'motogene.scratch.card.replacement'
    _description = 'Scratch Card Replacement History and Blocked Serials'
    _order = 'id desc'
    _rec_name = 'old_serial'

    card_id = fields.Many2one('motogene.scratch.picking.card', required=True, ondelete='restrict', index=True)
    sale_id = fields.Many2one('sale.order', required=True, ondelete='restrict', index=True)
    picking_id = fields.Many2one('stock.picking', required=True, ondelete='restrict', index=True)
    program_id = fields.Many2one('motogene.promotion.program', required=True, ondelete='restrict')
    company_id = fields.Many2one(related='sale_id.company_id', store=True, index=True)
    old_serial = fields.Char(string='Blocked Original Serial', required=True, index=True)
    new_serial = fields.Char(string='Replacement Serial', required=True)
    reason = fields.Selection(REASONS, required=True)
    notes = fields.Text(required=True)
    replaced_at = fields.Datetime(required=True, default=fields.Datetime.now)
    replaced_by_id = fields.Many2one('res.users', required=True, ondelete='restrict')
    legacy_entry = fields.Boolean(string='Recorded After Shell Replacement')
    _sql_constraints = [
        ('blocked_serial_unique', 'UNIQUE(old_serial)', 'This original serial is already blocked.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError(_('Use Replace Scratch Card to create replacement history.'))

    def write(self, vals):
        raise UserError(_('Replacement history and blocked serials cannot be edited.'))

    def unlink(self):
        raise UserError(_('Replacement history and blocked serials cannot be deleted.'))

    @api.model
    def _record(self, card, old_serial, reason, notes, legacy=False):
        # Private server-side entry point; never exposed as an RPC workflow.
        return super(ScratchCardReplacement, self.sudo()).create({
            'card_id': card.id, 'sale_id': card.sale_id.id,
            'picking_id': card.picking_id.id, 'program_id': card.program_id.id,
            'old_serial': old_serial, 'new_serial': card.serial_number,
            'reason': reason, 'notes': notes.strip(),
            'replaced_by_id': self.env.user.id, 'legacy_entry': legacy,
        })


class ScratchCardReplacementPool(models.Model):
    _inherit = 'motogene.scratch.serial.pool'

    def reserve_serial(self):
        self.ensure_one()
        self.env['motogene.scratch.picking.card'].flush_model()
        self.env['motogene.scratch.card.replacement'].flush_model()
        self.env.cr.execute(
            'SELECT next_number, last_number FROM motogene_scratch_serial_pool WHERE id = %s FOR UPDATE',
            [self.id])
        number, maximum = self.env.cr.fetchone()
        program_id, prefix = self.program_id.id, self.prefix
        self.env.cr.execute('''
            SELECT released.serial_number
              FROM motogene_scratch_picking_card released
             WHERE released.program_id = %s AND released.prefix = %s
               AND released.state = 'released'
               AND NOT EXISTS (SELECT 1 FROM motogene_scratch_picking_card active
                    WHERE active.serial_number = released.serial_number AND active.state != 'released')
               AND NOT EXISTS (SELECT 1 FROM motogene_scratch_card_replacement blocked
                    WHERE blocked.old_serial = released.serial_number)
             ORDER BY released.id LIMIT 1
        ''', [program_id, prefix])
        reusable = self.env.cr.fetchone()
        if reusable:
            return reusable[0]
        self.env.cr.execute('SELECT old_serial FROM motogene_scratch_card_replacement')
        blocked = {row[0] for row in self.env.cr.fetchall()}
        while number <= maximum and '%s%d' % (prefix, number) in blocked:
            number += 1
        if number > maximum:
            raise ScratchSerialShortage(_('Scratch card type %s has no serial numbers remaining.') % prefix)
        self.env.cr.execute('UPDATE motogene_scratch_serial_pool SET next_number = %s WHERE id = %s',
                            [number + 1, self.id])
        self.invalidate_recordset(['next_number'])
        return '%s%d' % (prefix, number)

    def _scratch_available_count(self):
        self.ensure_one()
        self.env['motogene.scratch.picking.card'].flush_model()
        self.env['motogene.scratch.card.replacement'].flush_model()
        self.invalidate_recordset(['next_number', 'last_number'])
        start, end, prefix, program_id = self.next_number, self.last_number, self.prefix, self.program_id.id
        self.env.cr.execute('''
            SELECT COUNT(DISTINCT released.serial_number)
              FROM motogene_scratch_picking_card released
             WHERE released.program_id = %s AND released.prefix = %s AND released.state = 'released'
               AND NOT EXISTS (SELECT 1 FROM motogene_scratch_picking_card active
                    WHERE active.serial_number = released.serial_number AND active.state != 'released')
               AND NOT EXISTS (SELECT 1 FROM motogene_scratch_card_replacement blocked
                    WHERE blocked.old_serial = released.serial_number)
        ''', [program_id, prefix])
        reusable = self.env.cr.fetchone()[0]
        self.env.cr.execute('SELECT old_serial FROM motogene_scratch_card_replacement')
        blocked = {row[0] for row in self.env.cr.fetchall()}
        unavailable = sum(1 for serial in blocked
                          if serial.startswith(prefix) and serial[len(prefix):].isdigit()
                          and serial == '%s%d' % (prefix, int(serial[len(prefix):]))
                          and start <= int(serial[len(prefix):]) <= end)
        return max(0, end - start + 1 - unavailable) + reusable


class ScratchReplaceableCard(models.Model):
    _inherit = 'motogene.scratch.picking.card'

    @api.model_create_multi
    def create(self, vals_list):
        serials = [v.get('serial_number') for v in vals_list if v.get('serial_number')]
        if serials:
            self.env['motogene.scratch.card.replacement'].flush_model(['old_serial'])
            self.env.cr.execute('SELECT old_serial FROM motogene_scratch_card_replacement WHERE old_serial IN %s',
                                [tuple(serials)])
            if self.env.cr.fetchone():
                raise UserError(_('A replaced serial is blocked and cannot be allocated again.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'serial_number' in vals:
            History = self.env['motogene.scratch.card.replacement'].sudo()
            if History.search_count(['|', ('card_id', 'in', self.ids),
                                     ('old_serial', '=', vals['serial_number'])]):
                raise UserError(_('Use Replace Scratch Card. Replaced serials and their history cannot be restored or edited manually.'))
        return super().write(vals)

    def _lock_for_replacement(self):
        self.ensure_one()
        order, picking = self.sale_id, self.picking_id
        pool = self.program_id.scratch_serial_pool_ids.filtered(lambda p: p.prefix == self.prefix)
        if len(pool) != 1 or not picking:
            raise UserError(_('The card must have a delivery and exactly one matching serial pool.'))
        for records in (order, picking, pool, self):
            records.flush_recordset()
            self.env.cr.execute('SELECT id FROM %s WHERE id = %%s FOR UPDATE' % records._table, [records.id])
            records.invalidate_recordset()
        if (self.sale_id != order or self.picking_id != picking
                or self.program_id != pool.program_id or self.prefix != pool.prefix):
            raise UserError(_('The card allocation has changed. Refresh the delivery before replacing it.'))
        return pool

    def _check_replacement(self):
        self.ensure_one()
        picking, order = self.picking_id, self.sale_id
        if self.state not in ('reserved', 'sent') or self.redemption_id or self.return_picking_id or self.undelivered_released_at:
            raise UserError(_('Only allocated or dispatched cards without redemption, return or reuse history can be replaced.'))
        if (not picking or picking.state == 'cancel' or picking.picking_type_code != 'outgoing'
                or picking.scratch_return_source_id or order.state != 'sale'
                or picking.sale_id != order or picking.company_id != order.company_id
                or (self.program_id.company_id and self.program_id.company_id != order.company_id)):
            raise UserError(_('Select a card on the original outgoing delivery of a confirmed Sales Order in the same company.'))
        if (self.state == 'sent') != (picking.state == 'done'):
            raise UserError(_('Card and delivery statuses do not match. Refresh the delivery.'))
        if self.env['motogene.scratch.redemption'].sudo().search_count([('card_id', '=', self.id)]):
            raise UserError(_('This card has redemption history or an open redemption. It cannot be replaced using this workflow.'))
        if self.env['stock.picking'].sudo().search_count([
                ('scratch_return_card_ids', 'in', self.ids), ('state', '!=', 'cancel')]):
            raise UserError(_('This card is selected under Physical Cards Received. Resolve that return before replacing it.'))

    def _replacement_access(self):
        self.ensure_one()
        if not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_('Only Operations users can replace scratch cards.'))
        for record in (self, self.picking_id):
            record.check_access_rights('write')
            record.check_access_rule('write')
        self.sale_id.check_access_rights('read')
        self.sale_id.check_access_rule('read')
        if self.sale_id.company_id not in self.env.companies:
            raise UserError(_('Switch to the delivery company before replacing a card.'))

    def _post_replacement_note(self, history):
        body = _('Scratch card replaced: %(old)s → %(new)s. Reason: %(reason)s. Notes: %(notes)s. Recorded by: %(user)s. Print the updated Picking Operations document.') % {
            'old': history.old_serial, 'new': history.new_serial,
            'reason': dict(REASONS)[history.reason], 'notes': history.notes,
            'user': self.env.user.display_name}
        # Operations can annotate this already-authorized source order without
        # needing permission to edit its prices or commercial terms.
        for record in (self.sale_id.sudo(), self.picking_id):
            record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')

    def _replace_scratch_card(self, reason, notes, not_handed_over, expected_serial):
        self._replacement_access()
        if reason not in dict(REASONS) or not (notes or '').strip() or not not_handed_over:
            raise UserError(_('Select a reason, enter remarks and confirm that the parcel has not been handed over.'))
        with self.env.cr.savepoint():
            pool = self._lock_for_replacement()
            if self.serial_number != expected_serial:
                raise UserError(_('The card serial has changed. Close this popup and refresh the delivery.'))
            self._check_replacement()
            old_serial = self.serial_number
            replacement = pool.reserve_serial()
            # Bypass only this extension's serial-edit guard, preserving all
            # existing card identity, redemption and reuse checks in the MRO.
            super(ScratchReplaceableCard, self).write({'serial_number': replacement})
            history = self.env['motogene.scratch.card.replacement']._record(self, old_serial, reason, notes)
            self._post_replacement_note(history)
        return history

    def _record_legacy_replacement(self, old_serial, reason, notes):
        """Odoo shell only: record a replacement already completed, issue nothing."""
        self.ensure_one()
        if not self.env.su:
            raise UserError(_('Legacy replacement recording requires an administrator shell.'))
        if reason not in dict(REASONS) or not (notes or '').strip():
            raise UserError(_('Select a valid reason and enter remarks.'))
        with self.env.cr.savepoint():
            pool = self._lock_for_replacement()
            if (not self.picking_id or self.picking_id.sale_id != self.sale_id
                    or self.picking_id.company_id != self.sale_id.company_id):
                raise UserError(_('Legacy replacement delivery and order do not match.'))
            suffix = (old_serial or '')[len(self.prefix):]
            if (not (old_serial or '').startswith(self.prefix) or not suffix.isdigit()
                    or old_serial != '%s%d' % (self.prefix, int(suffix))
                    or not pool.first_number <= int(suffix) < pool.next_number
                    or old_serial == self.serial_number):
                raise UserError(_('The original serial must be a different, previously issued serial in the same pool.'))
            if self.sudo().search_count([('serial_number', '=', old_serial)]):
                raise UserError(_('The original serial still has a card record. Inspect it before recording a legacy replacement.'))
            history = self.env['motogene.scratch.card.replacement']._record(self, old_serial, reason, notes, legacy=True)
            self._post_replacement_note(history)
        return history


class ScratchReplacementPicking(models.Model):
    _inherit = 'stock.picking'

    scratch_can_replace_card = fields.Boolean(compute='_compute_can_replace_card')
    scratch_replacement_history_ids = fields.One2many(
        'motogene.scratch.card.replacement', 'picking_id', string='Scratch Card Replacement History', readonly=True)

    @api.depends('state', 'picking_type_code', 'scratch_return_source_id', 'scratch_card_line_ids.state')
    def _compute_can_replace_card(self):
        for picking in self:
            picking.scratch_can_replace_card = bool(
                picking.state != 'cancel' and picking.picking_type_code == 'outgoing'
                and not picking.scratch_return_source_id
                and picking.scratch_card_line_ids.filtered(lambda c: c.state in ('reserved', 'sent')))

    def action_replace_scratch_card(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.env.user.has_group('stock.group_stock_user') or not self.scratch_can_replace_card:
            raise UserError(_('No replaceable scratch card is available on this delivery.'))
        wizard = self.env['motogene.scratch.replace.wizard'].create({'picking_id': self.id})
        return {'type': 'ir.actions.act_window', 'name': _('Replace Scratch Card'),
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new'}


class ScratchReplacementSale(models.Model):
    _inherit = 'sale.order'

    scratch_replacement_history_ids = fields.One2many(
        'motogene.scratch.card.replacement', 'sale_id', string='Scratch Card Replacement History', readonly=True)


class ScratchReplaceWizard(models.TransientModel):
    _name = 'motogene.scratch.replace.wizard'
    _description = 'Replace Scratch Card Before Parcel Handover'

    picking_id = fields.Many2one('stock.picking', required=True, readonly=True, string='Delivery Order')
    available_card_ids = fields.Many2many('motogene.scratch.picking.card',
        'mg_scratch_replace_available_rel', 'wizard_id', 'card_id', compute='_compute_available_cards')
    card_id = fields.Many2one('motogene.scratch.picking.card', string='Card to Replace')
    serial_to_replace = fields.Char(string='Original Serial', readonly=True)
    # The server creates an empty wizard before the user fills the form.
    # Required input is enforced by the view and again in action_confirm.
    reason = fields.Selection(REASONS)
    notes = fields.Text(string='Remarks')
    not_handed_over = fields.Boolean(string='Parcel has not been handed over to the courier or customer')

    @api.depends('picking_id', 'picking_id.scratch_card_line_ids.state')
    def _compute_available_cards(self):
        for wizard in self:
            wizard.available_card_ids = wizard.picking_id.scratch_card_line_ids.filtered(
                lambda c: c.state in ('reserved', 'sent') and not c.redemption_id and not c.return_picking_id)

    @api.onchange('card_id')
    def _onchange_card_id(self):
        self.serial_to_replace = self.card_id.serial_number

    def action_confirm(self):
        self.ensure_one()
        if not self.card_id or self.card_id.picking_id != self.picking_id:
            raise UserError(_('Select a card belonging to this delivery.'))
        self.card_id._replace_scratch_card(self.reason, self.notes, self.not_handed_over, self.serial_to_replace)
        return {'type': 'ir.actions.client', 'tag': 'reload'}
