# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .scratch_card import ScratchSerialShortage


REASONS = [
    ('damaged', 'Damaged / Already Scratched'),
    ('missing', 'Missing Card'),
    ('printing', 'Printing Defect'),
    ('courier', 'Courier / Delivery Issue'),
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
    replacement_mode = fields.Selection([
        ('before_handover', 'Before Parcel Handover'),
        ('courier_recall', 'Courier Recall — Replacement Shipment'),
    ], required=True, default='before_handover')
    original_status = fields.Selection([
        ('blocked', 'Permanently Blocked'),
        ('pending', 'On Hold — Return Pending'),
        ('reusable', 'Received — Released for Reuse'),
    ], required=True, default='blocked', index=True)
    replacement_picking_id = fields.Many2one('stock.picking', string='Replacement Delivery', ondelete='restrict')
    recovered_card_id = fields.Many2one('motogene.scratch.picking.card', readonly=True, ondelete='restrict')
    received_at = fields.Datetime(readonly=True)
    received_by_id = fields.Many2one('res.users', readonly=True, ondelete='restrict')
    recovery_notes = fields.Text(readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    resolved_by_id = fields.Many2one('res.users', readonly=True, ondelete='restrict')

    def init(self):
        self.env.cr.execute('''ALTER TABLE motogene_scratch_card_replacement
            DROP CONSTRAINT IF EXISTS motogene_scratch_card_replacement_blocked_serial_unique''')
        self.env.cr.execute('''CREATE UNIQUE INDEX IF NOT EXISTS mg_scratch_blocked_serial_unique
            ON motogene_scratch_card_replacement(old_serial)
            WHERE original_status != 'reusable' ''')

    @api.model_create_multi
    def create(self, vals_list):
        raise UserError(_('Use Replace Scratch Card to create replacement history.'))

    def write(self, vals):
        raise UserError(_('Replacement history and blocked serials cannot be edited.'))

    def unlink(self):
        raise UserError(_('Replacement history and blocked serials cannot be deleted.'))

    @api.model
    def _record(self, card, old_serial, reason, notes, legacy=False,
                mode='before_handover', source_picking=None):
        # Private server-side entry point; never exposed as an RPC workflow.
        return super(ScratchCardReplacement, self.sudo()).create({
            'card_id': card.id, 'sale_id': card.sale_id.id,
            'picking_id': (source_picking or card.picking_id).id, 'program_id': card.program_id.id,
            'old_serial': old_serial, 'new_serial': card.serial_number,
            'reason': reason, 'notes': notes.strip(),
            'replaced_by_id': self.env.user.id, 'legacy_entry': legacy,
            'replacement_mode': mode,
            'original_status': 'pending' if mode == 'courier_recall' else 'blocked',
            'replacement_picking_id': card.picking_id.id,
        })

    def action_receive_recalled_card(self):
        self.ensure_one()
        self._recovery_access()
        if self.original_status != 'pending' or self.replacement_mode != 'courier_recall':
            raise UserError(_('This original card is not awaiting courier return.'))
        wizard = self.env['motogene.scratch.recall.receipt'].create({'history_id': self.id})
        return {'type': 'ir.actions.act_window', 'name': _('Receive Recalled Scratch Card'),
                'res_model': wizard._name, 'res_id': wizard.id, 'view_mode': 'form', 'target': 'new'}

    def _recovery_access(self):
        self.ensure_one()
        if not self.env.user.has_group('stock.group_stock_user'):
            raise UserError(_('Only Operations users can reconcile recalled scratch cards.'))
        self.check_access_rights('read')
        self.check_access_rule('read')
        self.picking_id.check_access_rights('write')
        self.picking_id.check_access_rule('write')
        if self.company_id not in self.env.companies:
            raise UserError(_('Switch to the original delivery company.'))

    def _resolve_courier_return(self, disposition, physically_received, unscratched, notes):
        self._recovery_access()
        if disposition not in ('reuse', 'block') or not (notes or '').strip():
            raise UserError(_('Select a disposition and enter the inspection result or reason.'))
        if disposition == 'reuse' and (not physically_received or not unscratched):
            raise UserError(_('Reuse requires the physical original card back in the warehouse, unscratched and in good condition.'))
        with self.env.cr.savepoint():
            pool = self.program_id.scratch_serial_pool_ids.filtered(lambda p: p.prefix == self.old_serial[:1])
            if len(pool) != 1:
                raise UserError(_('The original serial pool is missing.'))
            for records in (self.sale_id, self.picking_id, pool, self):
                records.flush_recordset()
                self.env.cr.execute('SELECT id FROM %s WHERE id = %%s FOR UPDATE' % records._table, [records.id])
                records.invalidate_recordset()
            if self.replacement_mode != 'courier_recall' or self.original_status != 'pending':
                raise UserError(_('This courier return was already reconciled or is not pending.'))
            Card = self.env['motogene.scratch.picking.card']
            if Card.sudo().search_count([('serial_number', '=', self.old_serial), ('state', '!=', 'released')]):
                raise UserError(_('The original serial has an active card record. Inspect it before reconciliation.'))
            now = fields.Datetime.now()
            vals = {'original_status': 'reusable' if disposition == 'reuse' else 'blocked',
                    'recovery_notes': notes.strip(), 'resolved_at': now,
                    'resolved_by_id': self.env.user.id,
                    'received_at': now if physically_received else False,
                    'received_by_id': self.env.user.id if physically_received else False}
            super(ScratchCardReplacement, self.sudo()).write(vals)
            if disposition == 'reuse':
                # Released rows are history, not new customer entitlement. The
                # usual allocator creates a fresh allocation when reusing one.
                recovered = Card.create({
                    'sale_id': self.sale_id.id, 'picking_id': self.picking_id.id,
                    'program_id': self.program_id.id, 'prefix': pool.prefix,
                    'serial_number': self.old_serial, 'state': 'released',
                    'expiry_date': self.card_id.expiry_date,
                })
                super(ScratchCardReplacement, self.sudo()).write({'recovered_card_id': recovered.id})
            body = _('Courier-return card %(serial)s reconciled: %(result)s. Inspection/reason: %(notes)s.') % {
                'serial': self.old_serial,
                'result': _('Released for reuse') if disposition == 'reuse' else _('Permanently blocked'),
                'notes': notes.strip()}
            for record in [self.sale_id.sudo(), *(self.picking_id | self.replacement_picking_id)]:
                record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')
        return True


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
                    WHERE blocked.old_serial = released.serial_number AND blocked.original_status != 'reusable')
             ORDER BY released.id LIMIT 1
        ''', [program_id, prefix])
        reusable = self.env.cr.fetchone()
        if reusable:
            return reusable[0]
        self.env.cr.execute("SELECT old_serial FROM motogene_scratch_card_replacement WHERE original_status != 'reusable'")
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
                    WHERE blocked.old_serial = released.serial_number AND blocked.original_status != 'reusable')
        ''', [program_id, prefix])
        reusable = self.env.cr.fetchone()[0]
        self.env.cr.execute("SELECT old_serial FROM motogene_scratch_card_replacement WHERE original_status != 'reusable'")
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
            self.env.cr.execute("SELECT old_serial FROM motogene_scratch_card_replacement WHERE original_status != 'reusable' AND old_serial IN %s",
                                [tuple(serials)])
            if self.env.cr.fetchone():
                raise UserError(_('A replaced serial is blocked and cannot be allocated again.'))
        return super().create(vals_list)

    def write(self, vals):
        if 'serial_number' in vals:
            History = self.env['motogene.scratch.card.replacement'].sudo()
            if History.search_count(['|', ('card_id', 'in', self.ids),
                                     '&', ('old_serial', '=', vals['serial_number']), ('original_status', '!=', 'reusable')]):
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
        pickings = history.picking_id | history.replacement_picking_id
        for record in [self.sale_id.sudo(), *pickings]:
            record.message_post(body=body, message_type='comment', subtype_xmlid='mail.mt_note')

    def _replace_scratch_card(self, reason, notes, not_handed_over, expected_serial,
                             mode='before_handover', replacement_picking=None, recall_requested=False):
        self._replacement_access()
        if reason not in dict(REASONS) or not (notes or '').strip():
            raise UserError(_('Select a reason and enter remarks.'))
        if mode not in ('before_handover', 'courier_recall'):
            raise UserError(_('Select a valid replacement situation.'))
        if mode == 'before_handover' and not not_handed_over:
            raise UserError(_('Confirm that the parcel has not been handed over.'))
        if mode == 'courier_recall' and not recall_requested:
            raise UserError(_('Confirm the courier recall was requested and the original parcel has not been delivered to the customer.'))
        with self.env.cr.savepoint():
            pool = self._lock_for_replacement()
            if self.serial_number != expected_serial:
                raise UserError(_('The card serial has changed. Close this popup and refresh the delivery.'))
            self._check_replacement()
            source_picking = self.picking_id
            if mode == 'courier_recall':
                target = replacement_picking.exists() if replacement_picking else source_picking
                if len(target) != 1:
                    raise UserError(_('Select a valid replacement Delivery Order for the same Sales Order.'))
                target.check_access_rights('write')
                target.check_access_rule('write')
                self.env.cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', [target.id])
                target.invalidate_recordset()
                if (self.state != 'sent' or source_picking.state != 'done'
                        or target.sale_id != self.sale_id
                        or target.company_id != source_picking.company_id
                        or target.picking_type_code != 'outgoing' or target.scratch_return_source_id
                        or (target != source_picking and target.state in ('done', 'cancel'))):
                    raise UserError(_('Use the original completed delivery, or a different unvalidated outgoing delivery on the same Sales Order and company.'))
            old_serial = self.serial_number
            replacement = pool.reserve_serial()
            # Bypass only this extension's serial-edit guard, preserving all
            # existing card identity, redemption and reuse checks in the MRO.
            vals = {'serial_number': replacement}
            if mode == 'courier_recall':
                vals.update({'picking_id': target.id, 'state': 'sent' if target == source_picking else 'reserved'})
            super(ScratchReplaceableCard, self).write(vals)
            history = self.env['motogene.scratch.card.replacement']._record(
                self, old_serial, reason, notes, mode=mode, source_picking=source_picking)
            self._post_replacement_note(history)
            if mode == 'courier_recall':
                source_picking.message_post(body=_('Original card %s is On Hold — Return Pending. It cannot be redeemed or reused until its return is reconciled.') % old_serial,
                                            message_type='comment', subtype_xmlid='mail.mt_note')
            (source_picking | self.picking_id).invalidate_recordset(['scratch_card_line_ids'])
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
    scratch_incoming_replacement_history_ids = fields.One2many(
        'motogene.scratch.card.replacement', 'replacement_picking_id', string='Replacement Shipment Card History',
        domain=[('replacement_mode', '=', 'courier_recall')], readonly=True)

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
    replacement_mode = fields.Selection([
        ('before_handover', 'Before Parcel Handover'),
        ('courier_recall', 'Courier Recall — Replacement Shipment'),
    ], required=True, default='before_handover', string='Situation')
    sale_id = fields.Many2one(related='picking_id.sale_id')
    replacement_picking_id = fields.Many2one('stock.picking', string='Replacement Delivery (Optional)',
        help='Leave empty to keep the original DO. Select another unvalidated DO on the same SO only when a separate delivery is used.')
    recall_requested = fields.Boolean(string='Courier recall requested; the original parcel has not been delivered to the customer')

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
        self.card_id._replace_scratch_card(
            self.reason, self.notes, self.not_handed_over, self.serial_to_replace,
            mode=self.replacement_mode, replacement_picking=self.replacement_picking_id,
            recall_requested=self.recall_requested)
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class ScratchRecallReceipt(models.TransientModel):
    _name = 'motogene.scratch.recall.receipt'
    _description = 'Receive and Reconcile Courier-Recalled Scratch Card'

    history_id = fields.Many2one('motogene.scratch.card.replacement', required=True, readonly=True)
    old_serial = fields.Char(related='history_id.old_serial', string='Original Serial')
    new_serial = fields.Char(related='history_id.new_serial', string='Replacement Serial')
    disposition = fields.Selection([
        ('reuse', 'Release Cards for Reuse'), ('block', 'Permanently Block Original Card'),
    ], required=True, default='reuse')
    physically_received = fields.Boolean(string='Original physical card has been received back in the warehouse')
    unscratched = fields.Boolean(string='Original card is unscratched and in good condition')
    notes = fields.Text(string='Inspection / Reason')

    def action_confirm(self):
        self.ensure_one()
        self.history_id._resolve_courier_return(
            self.disposition, self.physically_received, self.unscratched, self.notes)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

