"""Smart GDEX bulk review: Normal and COD are always routed separately.

This is a *review-first* batch wizard. It never calls an external API while
loading or previewing selected DOs. Every shipment is delegated to the
existing durable, per-AWB reservation/create path.
"""
from collections import defaultdict
import json
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from odoo import api, fields, models, _
from odoo.exceptions import UserError


def _cents(value):
    return int((Decimal(str(value or 0)) * Decimal('100')).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP))


class GdexSmartBulkWizard(models.TransientModel):
    _name = 'motogene.gdex.smart.bulk.wizard'
    _description = 'GDEX Smart Bulk Normal and COD Review'

    line_ids = fields.One2many('motogene.gdex.smart.bulk.line', 'wizard_id',
                               string='Reviewed Shipments')
    selected_picking_ids = fields.Many2many('stock.picking', readonly=True)
    created_normal_ids_json = fields.Text(readonly=True, default='[]')
    created_cod_ids_json = fields.Text(readonly=True, default='[]')
    processed = fields.Boolean(readonly=True)
    cod_picking_to_add = fields.Many2one('stock.picking', string='Add Parcel for COD DO')
    review_summary = fields.Text(readonly=True)
    results = fields.Text(readonly=True)

    @api.model
    def action_open_for_pickings(self, pickings):
        self.env['motogene.gdex.consignment']._check_operator()
        pickings = pickings.exists()
        if not pickings:
            raise UserError(_('Select at least one Delivery Order.'))
        if len(pickings) > 80:
            raise UserError(_('Select no more than 80 Delivery Orders per batch.'))
        normal_model = self.env['motogene.gdex.consignment'].sudo()
        cod_model = self.env['motogene.gdex.cod.consignment'].sudo()
        lines = []
        reasons = []
        for p in pickings.sorted(key=lambda x: x.name):
            dtype = p.x_studio_order_type
            route = 'normal' if dtype == 'Normal' else (
                'cod' if dtype in ('COD', 'Cash On Delivery', 'Cash on Delivery') else 'skip')
            issue = False
            if p.picking_type_code != 'outgoing' or p.state != 'assigned':
                issue = _('Only Ready outgoing Delivery Orders are allowed.')
            elif route == 'skip':
                issue = _('This Delivery Type is not GDEX Normal or COD.')
            elif p.mori_awb_no:
                issue = _('Delivery Order already contains an AWB.')
            elif normal_model.search_count([('picking_id', '=', p.id)]):
                issue = _('A Normal GDEX attempt already exists; check its result.')
            elif cod_model.search_count([('picking_id', '=', p.id)]):
                issue = _('A COD GDEX attempt already exists; check its result.')
            elif p.partner_id.country_id.code != 'MY':
                issue = _('Destination is not Malaysia.')
            sale = p.sale_id or p.move_ids.sale_line_id.order_id[:1]
            if not issue and route == 'cod' and (not sale or sale.currency_id.name != 'MYR'):
                issue = _('COD requires a linked MYR Sales Order.')
            partner = p.partner_id
            receiver = {
                'receiver_name': partner.name or '',
                'receiver_mobile': partner.mobile or partner.phone or '',
                'receiver_address1': ', '.join(v for v in (
                    partner.street, partner.street2, partner.city) if v),
                'receiver_postcode': partner.zip or '',
            }
            if not issue and any(not x for x in receiver.values()):
                issue = _('Incomplete receiver contact or delivery address.')
            if issue:
                lines.append((0, 0, {
                    'picking_id': p.id, 'route': route,
                    'eligible': False, 'skip_reason': issue, 'parcel_no': 1,
                    **receiver,
                }))
                reasons.append('%s: %s' % (p.name, issue))
                continue
            if route == 'normal':
                lines.append((0, 0, {
                    'picking_id': p.id, 'route': 'normal', 'eligible': True,
                    'parcel_no': 1, 'weight': 1.0, 'pieces': 1,
                    'currency_id': self.env.company.currency_id.id,
                    'note_source': 'none', **receiver,
                }))
            else:
                total = _cents(sale.amount_total)
                if total < 1 or total > 50 * 150000:
                    lines.append((0, 0, {
                        'picking_id': p.id, 'route': 'cod', 'eligible': False,
                        'skip_reason': _('Invalid COD amount or more than 50 parcels required.'),
                        'parcel_no': 1, **receiver,
                    }))
                    continue
                remain = total
                no = 0
                while remain:
                    no += 1
                    part = min(remain, 150000)
                    remain -= part
                    lines.append((0, 0, {
                        'picking_id': p.id, 'route': 'cod', 'eligible': True,
                        'parcel_no': no, 'cod_amount': part / 100.0,
                        'total_cod': sale.amount_total, 'sales_amount': sale.amount_total,
                        'currency_id': sale.currency_id.id, 'weight': 1.0,
                        **receiver,
                    }))
        wizard = self.create({
            'selected_picking_ids': [(6, 0, pickings.ids)],
            'line_ids': lines,
            'review_summary': _('%s DO(s) selected. Review Normal/COD allocations and skipped rows. '
                                'Nothing is sent until confirmation.') % len(pickings),
        })
        return {'type': 'ir.actions.act_window',
                'name': _('GDEX Bulk — Review Normal & COD'),
                'res_model': wizard._name, 'res_id': wizard.id,
                'view_mode': 'form', 'target': 'new'}

    def action_add_cod_parcel(self):
        self.ensure_one()
        if self.processed:
            raise UserError(_('This batch has already been processed.'))
        p = self.cod_picking_to_add
        if not p or p.id not in self.selected_picking_ids.ids:
            raise UserError(_('Select one of the reviewed COD Delivery Orders.'))
        existing = self.line_ids.filtered(
            lambda line: line.picking_id == p and line.route == 'cod' and line.eligible)
        if not existing:
            raise UserError(_('The selected Delivery Order is not eligible COD.'))
        if len(existing) >= 50:
            raise UserError(_('A COD Delivery Order cannot exceed 50 parcel rows.'))
        first = existing[0]
        new_no = max(existing.mapped('parcel_no')) + 1
        # A new row starts with zero allocation. Operations must deliberately
        # redistribute the amount; preflight blocks zero or mismatching sums.
        self.env['motogene.gdex.smart.bulk.line'].create({
            'wizard_id': self.id, 'picking_id': p.id, 'route': 'cod',
            'eligible': True, 'parcel_no': new_no,
            'currency_id': first.currency_id.id,
            'sales_amount': first.sales_amount, 'total_cod': first.total_cod,
            'cod_amount': 0.0, 'weight': 1.0,
            'receiver_name': first.receiver_name, 'receiver_mobile': first.receiver_mobile,
            'receiver_address1': first.receiver_address1,
            'receiver_postcode': first.receiver_postcode,
            'change_reason': first.change_reason,
        })
        self.cod_picking_to_add = False
        return {'type': 'ir.actions.act_window',
                'name': _('GDEX Bulk — Review Normal & COD'), 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def _preflight(self):
        """All edits validated before *first* external call."""
        self.ensure_one()
        if self.processed:
            raise UserError(_('This batch has already been processed.'))
        self.env['motogene.gdex.consignment']._check_operator()
        if not self.line_ids:
            raise UserError(_('There are no shipment rows.'))
        if len(self.line_ids) > 160:
            raise UserError(_('Maximum 160 shipment rows per batch.'))
        normal = self.env['motogene.gdex.consignment']
        cod = self.env['motogene.gdex.cod.consignment']
        chosen = set(self.selected_picking_ids.ids)
        eligible = self.line_ids.filtered('eligible')
        if not eligible:
            raise UserError(_('No eligible shipments. Nothing has been sent.'))
        if len(set(eligible.mapped('picking_id').ids) - chosen):
            raise UserError(_('An unselected Delivery Order was added.'))
        issues = []
        groups = defaultdict(list)
        for line in eligible:
            p = line.picking_id
            expected = 'normal' if p.x_studio_order_type == 'Normal' else (
                'cod' if p.x_studio_order_type in ('COD','Cash On Delivery','Cash on Delivery') else 'skip')
            if (line.route != expected or p.state != 'assigned'
                    or p.picking_type_code != 'outgoing' or p.mori_awb_no
                    or p.partner_id.country_id.code != 'MY'):
                issues.append('%s: DO type/state/AWB changed' % p.name)
            if normal.sudo().search_count([('picking_id', '=', p.id)]) or cod.sudo().search_count([
                    ('picking_id', '=', p.id)]):
                issues.append('%s: prior GDEX attempt exists' % p.name)
            if not all((line.receiver_name, line.receiver_mobile,
                        line.receiver_address1, line.receiver_postcode)):
                issues.append('%s: receiver incomplete' % p.name)
            if len(line.receiver_mobile or '') > 25 or len(line.note1 or '') > 50 or len(line.note2 or '') > 50:
                issues.append('%s: GDEX mobile/note length exceeded' % p.name)
            if line.route == 'normal' and line.note_source == 'do':
                from odoo.tools import html2plaintext
                if len(html2plaintext(p.note or '').strip()) > 100:
                    issues.append('%s: DO note exceeds 100 characters; choose Manual notes' % p.name)
            if line.weight <= 0:
                issues.append('%s: nonpositive parcel weight' % p.name)
            groups[p.id].append(line)
        for pid, rows in groups.items():
            if any(row.route == 'normal' for row in rows):
                if len(rows) != 1 or rows[0].pieces < 1 or rows[0].pieces > 30:
                    issues.append('%s: Normal must have exactly one line and 1-30 pieces' % rows[0].picking_id.name)
                continue
            p = rows[0].picking_id
            sale = p.sale_id or p.move_ids.sale_line_id.order_id[:1]
            if not sale or sale.currency_id.name != 'MYR':
                issues.append('%s: MYR Sales Order missing' % p.name)
                continue
            values = [r.parcel_no for r in rows]
            if len(rows) > 50 or min(values) < 1 or len(values) != len(set(values)):
                issues.append('%s: invalid or duplicate parcel numbers' % p.name)
            if any(_cents(r.cod_amount) < 1 or _cents(r.cod_amount) > 150000 for r in rows):
                issues.append('%s: each COD parcel must be RM0.01-RM1500' % p.name)
            entered_totals = {_cents(r.total_cod) for r in rows}
            if len(entered_totals) != 1 or _cents(sum(r.cod_amount for r in rows)) != next(iter(entered_totals)):
                issues.append('%s: COD allocation must equal Total COD' % p.name)
            if next(iter(entered_totals)) != _cents(sale.amount_total) and not all(
                    (r.change_reason or '').strip() for r in rows):
                issues.append('%s: all COD rows require change reason when amount differs from Sales Order' % p.name)
            if any(r.currency_id != sale.currency_id for r in rows):
                issues.append('%s: currency changed' % p.name)
        if issues:
            raise UserError(_('Bulk validation failed; no requests were sent:\n%s') %
                            '\n'.join(issues[:15]))
        if any(r.route == 'normal' for r in eligible):
            normal._credentials(require_creation=True)
        if any(r.route == 'cod' for r in eligible):
            cod._cod_credentials()
        return eligible, groups

    def action_create(self):
        self.ensure_one()
        eligible, groups = self._preflight()
        normal = self.env['motogene.gdex.consignment']
        cod = self.env['motogene.gdex.cod.consignment']
        results = []
        created_normal = []
        created_cod = []
        # Persist the operator's intent before external API work.
        self.write({'processed': True, 'review_summary':
                    _('Confirmed. External attempts may have completed; NEVER re-submit this batch.')})
        # Each existing create method reserves the individual AWB on a
        # dedicated committed cursor, then finalizes success/uncertainty.
        for pid in sorted(groups, key=lambda k: groups[k][0].picking_id.name):
            rows = sorted(groups[pid], key=lambda r: r.parcel_no)
            p = rows[0].picking_id
            if rows[0].route == 'normal':
                try:
                    attempt_id, result = normal.create_from_wizard_line(rows[0])
                    if attempt_id:
                        created_normal.append(attempt_id)
                    results.append('%s [Normal] %s' % (p.name, result))
                except Exception as exc:
                    results.append('%s [Normal] STOPPED: %s' % (p.name, type(exc).__name__))
                continue
            # Re-check existing attempts after any previous external request.
            for row in rows:
                try:
                    if (p.mori_awb_no or cod.sudo().search_count([
                            ('picking_id', '=', pid), ('parcel_no', '=', row.parcel_no)])):
                        results.append('%s [COD parcel %s] BLOCKED: existing AWB/attempt' %
                                       (p.name, row.parcel_no))
                        break
                    adapter = SimpleNamespace(
                        picking_id=p, currency_id=row.currency_id,
                        receiver_name=row.receiver_name, receiver_mobile=row.receiver_mobile,
                        receiver_address1=row.receiver_address1,
                        receiver_postcode=row.receiver_postcode,
                        change_reason=row.change_reason)
                    attempt_id, outcome = cod.create_from_cod_line(adapter, row)
                    if attempt_id:
                        created_cod.append(attempt_id)
                    results.append('%s [COD parcel %s] %s %s' % (
                        p.name, row.parcel_no, outcome.get('state'),
                        outcome.get('awb') or outcome.get('error_message') or ''))
                    if outcome.get('state') != 'success':
                        break
                except Exception as exc:
                    results.append('%s [COD parcel %s] STOPPED: %s' %
                                   (p.name, row.parcel_no, type(exc).__name__))
                    break
            try:
                cod.sync_do_cod_awbs(pid)
            except UserError:
                results.append('%s [COD] DO AWB conflict — manual reconciliation required' % p.name)
            self.env.invalidate_all()
        skipped = self.line_ids.filtered(lambda x: not x.eligible)
        for line in skipped:
            results.append('%s [Skipped] %s' % (line.picking_id.name, line.skip_reason))
        self.write({
            'results': '\n'.join(results),
            'created_normal_ids_json': json.dumps(created_normal),
            'created_cod_ids_json': json.dumps(created_cod),
        })
        return {'type': 'ir.actions.act_window',
                'name': _('GDEX Bulk Results'), 'res_model': self._name,
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_print_normal_results(self):
        self.ensure_one()
        recs = self.env['motogene.gdex.consignment'].browse(json.loads(self.created_normal_ids_json or '[]')).exists().filtered(lambda c: c.state == 'success' and c.awb)
        if not recs:
            raise UserError(_('No successful Normal labels in the selection.'))
        return recs.action_print_bulk()

    def action_print_cod_results(self):
        self.ensure_one()
        recs = self.env['motogene.gdex.cod.consignment'].browse(json.loads(self.created_cod_ids_json or '[]')).exists().filtered(lambda c: c.state == 'success' and c.awb)
        if not recs:
            raise UserError(_('No successful COD labels in the selection.'))
        return recs.action_print_bulk_cod()


class GdexSmartBulkLine(models.TransientModel):
    _name = 'motogene.gdex.smart.bulk.line'
    _description = 'Reviewed GDEX Normal/COD shipment line'
    _order = 'picking_id, parcel_no, id'

    wizard_id = fields.Many2one('motogene.gdex.smart.bulk.wizard',
                                required=True, ondelete='cascade')
    picking_id = fields.Many2one('stock.picking', required=True, readonly=True)
    route = fields.Selection([('normal', 'Normal'), ('cod', 'COD'), ('skip','Skip')],
                             required=True, readonly=True)
    eligible = fields.Boolean(default=True, readonly=True)
    skip_reason = fields.Char(readonly=True)
    parcel_no = fields.Integer(default=1)
    receiver_name = fields.Char()
    receiver_mobile = fields.Char()
    receiver_address1 = fields.Char()
    receiver_postcode = fields.Char()
    weight = fields.Float(default=1.0)
    pieces = fields.Integer(default=1)
    currency_id = fields.Many2one('res.currency', readonly=True)
    sales_amount = fields.Monetary(currency_field='currency_id', readonly=True)
    total_cod = fields.Monetary(currency_field='currency_id')
    cod_amount = fields.Monetary(currency_field='currency_id')
    change_reason = fields.Char()
    note_source = fields.Selection([('none','No Note'), ('manual','Manual'), ('do','DO Note')],
                                   default='none')
    note1 = fields.Char()
    note2 = fields.Char()

    @api.onchange('note_source')
    def _onchange_note_source(self):
        for row in self:
            if row.note_source == 'none':
                row.note1 = False
                row.note2 = False
            elif row.note_source == 'do':
                from odoo.tools import html2plaintext
                note = html2plaintext(row.picking_id.note or '').strip()
                row.note1, row.note2 = note[:50], note[50:100]
