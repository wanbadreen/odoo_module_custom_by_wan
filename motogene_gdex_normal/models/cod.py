"""GDEX COD consignments: one AWB per parcel with durable attempt reservations."""
import base64
import io
import logging
import uuid

import requests

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.modules.registry import Registry

from .config import DEMO, PRODUCTION
from .pdf_labels import convert_to_thermal

_logger = logging.getLogger(__name__)


class GdexCodConsignment(models.Model):
    _name = 'motogene.gdex.cod.consignment'
    _description = 'GDEX COD Parcel / AWB'
    _order = 'picking_id, parcel_no'
    _sql_constraints = [
        ('cod_parcel_unique', 'unique(picking_id, parcel_no)',
         'This COD parcel number already has an attempt. Verify it before retrying.'),
    ]

    picking_id = fields.Many2one('stock.picking', required=True, ondelete='restrict', index=True)
    parcel_no = fields.Integer(required=True)
    cod_amount = fields.Monetary(required=True, currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', required=True, readonly=True,
                                  default=lambda self: self.env.company.currency_id)
    weight = fields.Float(readonly=True, default=1.0)
    receiver_name = fields.Char(readonly=True)
    receiver_mobile = fields.Char(readonly=True)
    receiver_address1 = fields.Char(readonly=True)
    receiver_postcode = fields.Char(readonly=True)
    note1 = fields.Char(readonly=True)
    note2 = fields.Char(readonly=True)
    change_reason = fields.Char(readonly=True)
    awb = fields.Char(readonly=True, copy=False, index=True)
    state = fields.Selection([
        ('uncertain', 'Creation Uncertain'), ('success', 'Success'),
        ('failed', 'Failed'), ('cancelled', 'Cancelled'),
    ], default='uncertain', required=True, readonly=True, index=True)
    api_environment = fields.Selection([('sandbox', 'Sandbox'), ('production', 'Production')],
                                      required=True, readonly=True)
    request_reference = fields.Char(readonly=True)
    error_message = fields.Char(readonly=True)
    created_by_id = fields.Many2one('res.users', readonly=True)
    pdf_attachment_id = fields.Many2one('ir.attachment', readonly=True)

    @api.model
    def _check_operator(self):
        self.env['motogene.gdex.consignment']._check_operator()

    @api.model
    def _cod_credentials(self):
        params = self.env['ir.config_parameter'].sudo()
        environment = params.get_param('motogene_gdex_normal.cod_environment', 'sandbox')
        if environment not in ('sandbox', 'production'):
            raise UserError(_('Invalid GDEX COD environment.'))
        prefix = 'motogene_gdex_normal.cod_' + environment + '_'
        token = params.get_param(prefix + 'token')
        key = params.get_param(prefix + 'key')
        account = params.get_param(prefix + 'account')
        if not token or not key or not account:
            raise UserError(_('Configure GDEX COD %s token, subscription key and account first.') % environment)
        if environment == 'production':
            normal_account = params.get_param('motogene_gdex_normal.account_no')
            if normal_account and normal_account.strip() == account.strip():
                raise UserError(_('COD Production Account Number must be different from the GDEX Normal account.'))
        if environment == 'production' and params.get_param(
                'motogene_gdex_normal.cod_production_enabled', 'False') != 'True':
            raise UserError(_('Production GDEX COD creation is disabled in Settings.'))
        headers = {
            'ApiToken': token, 'Subscription-Key': key,
            'Content-Type': 'application/json-patch+json', 'Accept': 'application/json',
        }
        return headers, account, PRODUCTION if environment == 'production' else DEMO, environment

    @api.model
    def create_from_cod_line(self, wizard, line):
        """Reserve one parcel on a separate cursor before any GDEX request.

        Errors and timeouts never permit automatic retry of the same parcel.
        A partially completed multi-parcel order must be reviewed manually.
        """
        self._check_operator()
        p = wizard.picking_id
        vals = {
            'picking_id': p.id, 'parcel_no': line.parcel_no,
            'cod_amount': line.cod_amount, 'currency_id': wizard.currency_id.id,
            'weight': line.weight,
            'receiver_name': (wizard.receiver_name or '').strip(),
            'receiver_mobile': (wizard.receiver_mobile or '').strip(),
            'receiver_address1': (wizard.receiver_address1 or '').strip(),
            'receiver_postcode': (wizard.receiver_postcode or '').strip(),
            'note1': (line.note1 or '').strip(),
            'note2': (line.note2 or '').strip(),
            'change_reason': wizard.change_reason or '',
            'request_reference': str(uuid.uuid4()), 'created_by_id': self.env.uid,
        }
        if any(not vals[k] for k in ('receiver_name','receiver_mobile',
                                      'receiver_address1','receiver_postcode')):
            raise UserError(_('Missing COD receiver details.'))
        if len(vals['receiver_mobile']) > 25:
            raise UserError(_('GDEX receiver mobile must be 25 characters or fewer.'))
        if len(vals['note1']) > 50 or len(vals['note2']) > 50:
            raise UserError(_('Each GDEX note must be 50 characters or fewer.'))
        if not 0.01 <= vals['cod_amount'] <= 1500 or vals['weight'] <= 0:
            raise UserError(_('COD per parcel must be RM0.01–RM1,500 and weight must be positive.'))
        if p.partner_id.country_id.code != 'MY':
            raise UserError(_('Only Malaysian domestic deliveries are supported.'))
        headers, account, endpoint, environment = self._cod_credentials()
        dbname, uid = self.env.cr.dbname, self.env.uid

        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', (p.id,))
            current = other['stock.picking'].browse(p.id)
            if (current.state != 'assigned' or current.picking_type_code != 'outgoing'
                    or current.x_studio_order_type != 'COD'):
                raise UserError(_('COD creation requires a Ready outgoing COD Delivery Order.'))
            # Existing AWBs, including manually entered references, must not be overwritten.
            if current.mori_awb_no:
                raise UserError(_('The DO already has an AWB. Verify it before creating COD AWBs.'))
            already = other[self._name].sudo().search_count([
                ('picking_id', '=', p.id), ('parcel_no', '=', line.parcel_no)])
            if already:
                raise UserError(_('This COD parcel already has an attempt. '
                                  'Verify in GDEX before any retry.'))
            rec = other[self._name].sudo().create({
                **vals, 'api_environment': environment, 'state': 'uncertain',
                'error_message': 'Reserved before GDEX request. Verify portal if interrupted.',
            })
            attempt_id = rec.id
            cr.commit()

        payload = [{
            'shipmentType': 'Parcel', 'totalPiece': 1, 'shipmentWeight': vals['weight'],
            'receiverName': vals['receiver_name'], 'receiverMobile': vals['receiver_mobile'],
            'receiverAddress1': vals['receiver_address1'], 'receiverPostcode': vals['receiver_postcode'],
            'receiverCountry': 'Malaysia', 'isDangerousGoods': False, 'IsInsurance': False,
            'isCod': True, 'codAmount': round(vals['cod_amount'], 2),
            'note1': vals['note1'], 'note2': vals['note2'],
        }]
        outcome = {'state': 'uncertain', 'error_message': 'Unconfirmed response; check COD GDEX portal.'}
        try:
            response = requests.post(
                endpoint + '/CreateConsignment',
                params={'accountNo': account}, headers=headers, json=payload,
                timeout=(10, 35))
            try:
                data = response.json()
            except ValueError:
                data = {}
            result = data.get('r') if isinstance(data, dict) else None
            if (response.status_code == 200 and isinstance(data, dict)
                    and data.get('s') == 'success' and isinstance(result, list)
                    and len(result) == 1 and result[0]):
                outcome = {'state': 'success', 'awb': str(result[0]), 'error_message': False}
            elif (response.status_code in (400, 401, 403)
                    and isinstance(data, dict) and data.get('s') == 'fail'):
                outcome = {'state': 'failed', 'error_message': str(data.get('e') or 'GDEX rejected COD')[:500]}
        except requests.RequestException as exc:
            outcome['error_message'] = 'Network error (%s). Verify portal; do not retry.' % type(exc).__name__

        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM motogene_gdex_cod_consignment WHERE id = %s FOR UPDATE', (attempt_id,))
            other[self._name].sudo().browse(attempt_id).write(outcome)
            cr.commit()
        return attempt_id, outcome

    @api.model
    def sync_do_cod_awbs(self, picking_id):
        """Mirror successful COD AWBs to DO AWB No without losing parcel records.

        Refuse to overwrite a manually entered or unrelated reference.
        This runs in an isolated transaction because CreateConsignment has
        already committed external success/attempts on separate cursors.
        """
        self._check_operator()
        dbname, uid = self.env.cr.dbname, self.env.uid
        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM stock_picking WHERE id = %s FOR UPDATE', (picking_id,))
            picking = other['stock.picking'].sudo().browse(picking_id).exists()
            if not picking or picking.x_studio_order_type != 'COD':
                raise UserError(_('The selected Delivery Order is not COD.'))
            attempts = other[self._name].sudo().search(
                [('picking_id', '=', picking_id)], order='parcel_no, id')
            active = attempts.filtered(lambda c: c.state == 'success' and c.awb)
            if not active:
                return False
            desired = '/'.join(active.mapped('awb'))
            existing = (picking.mori_awb_no or '').strip()
            known = set(active.mapped('awb'))
            current_parts = [piece.strip() for piece in existing.split('/') if piece.strip()]
            # Allow a blank DO, the exact current summary, or a previous
            # partial summary consisting solely of this DO's confirmed COD AWBs.
            if existing and (not current_parts or not set(current_parts).issubset(known)):
                raise UserError(_('DO AWB No has a different/manual value. '
                                  'It was NOT overwritten; reconcile manually.'))
            if existing != desired:
                picking.write({'mori_awb_no': desired})
                cr.commit()
            return desired

    def action_sync_do_cod_awbs(self):
        self._check_operator()
        for picking in self.mapped('picking_id'):
            self.sync_do_cod_awbs(picking.id)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_print_bulk_cod(self):
        """Merge existing/newly retrieved official COD PDFs, without creating AWBs."""
        self._check_operator()
        selected = self.exists().sorted(key=lambda c: (c.picking_id.id, c.parcel_no, c.id))
        if not selected or len(selected) > 60:
            raise UserError(_('Select between 1 and 60 COD consignments to print.'))
        if any(c.state != 'success' or not c.awb for c in selected):
            raise UserError(_('Only successful COD AWBs may be bulk printed.'))
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for rec in selected:
            # Single print is also the safe fetch-and-cache path.
            rec.action_print_cod()
            pdf = base64.b64decode(rec.pdf_attachment_id.sudo().datas)
            reader = PdfReader(io.BytesIO(pdf))
            if len(reader.pages) != 1:
                raise UserError(_('COD label %s must contain exactly one page.') % rec.awb)
            writer.add_page(reader.pages[0])
        result = io.BytesIO()
        writer.write(result)
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'GDEX_COD_Bulk_%s_labels.pdf' % len(selected),
            'type': 'binary', 'datas': base64.b64encode(result.getvalue()),
            'mimetype': 'application/pdf',
            'res_model': self._name, 'res_id': selected[0].id,
        })
        return {'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % attachment.id,
                'target': 'new'}

    def action_print_cod(self):
        self.ensure_one()
        self._check_operator()
        if self.state != 'success':
            raise UserError(_('Only a confirmed COD AWB can be printed.'))
        if not self.pdf_attachment_id:
            # No new consignment creation during printing. Read back via COD credentials.
            params = self.env['ir.config_parameter'].sudo()
            prefix = 'motogene_gdex_normal.cod_' + self.api_environment + '_'
            token = params.get_param(prefix + 'token')
            key = params.get_param(prefix + 'key')
            account = params.get_param(prefix + 'account')
            if not all((token, key, account)):
                raise UserError(_('COD credentials are not configured.'))
            endpoint = PRODUCTION if self.api_environment == 'production' else DEMO
            try:
                response = requests.get(
                    endpoint + '/GetConsignmentNotePDF',
                    params={'consignmentNo': self.awb, 'paperPrintingSize': 'A6',
                            'accountNo': account},
                    headers={'ApiToken': token, 'Subscription-Key': key},
                    timeout=(10, 35))
                if response.status_code != 200 or not response.content.startswith(b'%PDF'):
                    raise UserError(_('GDEX COD label PDF was not returned.'))
                pdf = convert_to_thermal(response.content)
            except requests.RequestException as exc:
                raise UserError(_('COD label retrieval failed (%s).') % type(exc).__name__) from exc
            att = self.env['ir.attachment'].sudo().create({
                'name': 'GDEX_COD_%s.pdf' % self.awb,
                'type': 'binary', 'datas': base64.b64encode(pdf),
                'mimetype': 'application/pdf', 'res_model': self._name, 'res_id': self.id,
            })
            self.sudo().write({'pdf_attachment_id': att.id})
        return {'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.pdf_attachment_id.id,
                'target': 'new'}
