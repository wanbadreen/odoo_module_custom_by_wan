import base64
import io
import json
import logging
import uuid

import requests
from odoo import api, fields, models, _
from odoo.modules.registry import Registry
from odoo.exceptions import AccessError, UserError
from .config import DEMO, PRODUCTION
from .pdf_labels import convert_to_thermal

_logger = logging.getLogger(__name__)


class GdexConsignment(models.Model):
    _name = 'motogene.gdex.consignment'
    _description = 'GDEX Normal Consignment Attempt'
    _order = 'id desc'

    picking_id = fields.Many2one('stock.picking', required=True, index=True, ondelete='restrict')
    state = fields.Selection([
        ('draft', 'Draft'), ('success', 'Success'),
        ('failed', 'Failed'), ('uncertain', 'Unknown - Verify in Portal'),
        ('cancel_pending', 'Cancellation Unknown - Verify in Portal'),
        ('cancelled', 'Cancelled'),
    ], default='draft', required=True, index=True, readonly=True)
    awb = fields.Char(readonly=True, copy=False)
    api_environment = fields.Selection([('sandbox', 'Sandbox'), ('production', 'Production')],
                                       default='sandbox', required=True, readonly=True, index=True)
    receiver_name = fields.Char(readonly=True)
    receiver_mobile = fields.Char(readonly=True)
    receiver_address1 = fields.Char(readonly=True)
    receiver_postcode = fields.Char(readonly=True)
    weight = fields.Float(default=1.0, readonly=True)
    pieces = fields.Integer(default=1, readonly=True)
    error_message = fields.Text(readonly=True)
    request_reference = fields.Char(readonly=True, copy=False)
    created_by_id = fields.Many2one('res.users', readonly=True)
    pdf_attachment_id = fields.Many2one('ir.attachment', readonly=True, copy=False)
    note1 = fields.Char(readonly=True)
    note2 = fields.Char(readonly=True)
    note_source = fields.Selection([('none','No Note'),('manual','Manual'),('do','DO Note')], readonly=True)
    cancellation_reason = fields.Char(string='Cancellation Reason')
    cancelled_by_id = fields.Many2one('res.users', readonly=True)
    cancelled_at = fields.Datetime(readonly=True)
    mps_numbers = fields.Text(string='MPS Child Numbers (JSON)', readonly=True)
    mps_status = fields.Selection([('not_needed', 'Single Piece'),('pending', 'Needs Retrieval'),('ready', 'MPS Retrieved')], default='not_needed', readonly=True)
    pdf_ready = fields.Boolean(string='PDF Ready', compute='_compute_pdf_ready', store=True)

    @api.depends('pdf_attachment_id')
    def _compute_pdf_ready(self):
        for rec in self:
            rec.pdf_ready = bool(rec.pdf_attachment_id)


    def init(self):
        # Database-level safeguard against concurrent duplicate requests.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS motogene_gdex_active_picking_uniq
            ON motogene_gdex_consignment (picking_id)
            WHERE state IN ('uncertain', 'success', 'cancel_pending')
        """)

    @api.model
    def _check_operator(self):
        if not (self.env.user.has_group('motogene_gdex_normal.group_gdex_normal_user')
                or self.env.user.has_group('base.group_system')):
            raise AccessError(_('You are not allowed to create GDEX consignments.'))

    @api.model
    def _credentials(self, environment=None, require_creation=False):
        params = self.env['ir.config_parameter'].sudo()
        active = params.get_param('motogene_gdex_normal.environment', 'sandbox')
        environment = environment or active
        if environment not in ('sandbox', 'production'):
            raise UserError(_('Invalid GDEX environment.'))
        if require_creation and environment != active:
            raise UserError(_('GDEX environment changed; reload the wizard.'))
        if environment == 'production':
            if require_creation and params.get_param('motogene_gdex_normal.production_enabled', 'False') != 'True':
                raise UserError(_('Production AWB creation is disabled by administrator.'))
            endpoint = PRODUCTION
            token = params.get_param('motogene_gdex_normal.production_token')
            key = params.get_param('motogene_gdex_normal.production_key')
        else:
            endpoint = DEMO
            token = params.get_param('motogene_gdex_normal.sandbox_token')
            key = params.get_param('motogene_gdex_normal.sandbox_key')
        if not token or not key:
            raise UserError(_('Configure GDEX %s credentials first.') % environment)
        return ({
            'ApiToken': token, 'Subscription-Key': key,
            'Content-Type': 'application/json-patch+json',
            'Accept': 'application/json',
        }, params.get_param('motogene_gdex_normal.account_no'), endpoint, environment)

    @api.model
    def create_from_wizard_line(self, line):
        """Reserve in a dedicated committed transaction; never commit the UI cursor.

        The reservation is permanently uncertain BEFORE external API transmission.
        A crashed/timeout request therefore blocks retries until manual verification.
        """
        self._check_operator()
        vals = {
            'picking_id': line.picking_id.id,
            'receiver_name': (line.receiver_name or '').strip(),
            'receiver_mobile': (line.receiver_mobile or '').strip(),
            'receiver_address1': (line.receiver_address1 or '').strip(),
            'receiver_postcode': (line.receiver_postcode or '').strip(),
            'weight': line.weight,
            'pieces': line.pieces,
            'note_source': line.note_source or 'none',
            'note1': (line.note1 or '').strip(),
            'note2': (line.note2 or '').strip(),
        }
        if len(vals['note1']) > 50 or len(vals['note2']) > 50:
            return False, _('Each GDEX note must be 50 characters or fewer.')
        picking = line.picking_id
        if not all(vals[k] for k in ('receiver_name','receiver_mobile','receiver_address1','receiver_postcode')):
            return False, _('Missing receiver details.')
        if vals['weight'] <= 0 or not 1 <= vals['pieces'] <= 30:
            return False, _('Weight must be positive; pieces must be between 1 and 30.')
        if not picking.partner_id.country_id or picking.partner_id.country_id.code != 'MY':
            return False, _('Only Malaysian domestic destinations are supported.')
        try:
            headers, account, endpoint, environment = self._credentials(require_creation=True)  # Validate before reserving.
        except UserError as exc:
            return False, str(exc)

        dbname = self.env.cr.dbname
        user_id = self.env.uid
        attempt_id = None
        # Separate cursor prevents manual commits of unrelated Odoo work.
        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, user_id, dict(self.env.context))
            cr.execute('SELECT id FROM stock_picking WHERE id=%s FOR UPDATE', (picking.id,))
            p = other['stock.picking'].browse(picking.id).exists()
            if not p or p.picking_type_code != 'outgoing' or p.state != 'assigned' or p.x_studio_order_type != 'Normal':
                return False, _('Blocked: Ready outgoing Normal Delivery only.')
            if p.mori_awb_no:
                return False, _('Blocked: an AWB already exists on the Delivery Order.')
            attempts = other['motogene.gdex.consignment'].sudo()
            if attempts.search_count([('picking_id','=',p.id), ('state','in',('uncertain','success','cancel_pending'))]):
                return False, _('Blocked: successful or unresolved GDEX request exists.')
            attempt = attempts.create({**vals, 'api_environment': environment,
                'state': 'uncertain',
                'mps_status': 'pending' if vals['pieces'] > 1 else 'not_needed',
                'error_message': 'Reserved: check GDEX portal before retry if interrupted.',
                'request_reference': str(uuid.uuid4()), 'created_by_id': user_id,
            })
            attempt_id = attempt.id
            cr.commit()   # ONLY this private, isolated reservation transaction.

        payload = [{
            'shipmentType': 'Parcel', 'totalPiece': vals['pieces'], 'shipmentWeight': vals['weight'],
            'receiverName': vals['receiver_name'], 'receiverMobile': vals['receiver_mobile'],
            'receiverAddress1': vals['receiver_address1'], 'receiverPostcode': vals['receiver_postcode'],
            'receiverCountry': 'Malaysia', 'isDangerousGoods': False,
            'IsInsurance': False, 'isCod': False, 'orderID': picking.name[:60],
            'note1': vals['note1'], 'note2': vals['note2'],
        }]
        outcome = {'state': 'uncertain', 'error_message': 'Response not confirmed; verify in GDEX portal before retry.'}
        try:
            response = requests.post(endpoint+'/CreateConsignment',
                params={'accountNo': account} if account else None,
                headers=headers, json=payload, timeout=(10, 35))
            try:
                data = response.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            awbs = data.get('r')
            if (response.status_code == 200 and data.get('s') == 'success'
                    and isinstance(awbs, list) and len(awbs) == 1 and awbs[0]):
                outcome = {'state': 'success', 'awb': str(awbs[0]), 'error_message': False}
            elif (response.status_code in (400, 401, 403)
                  and data.get('s') == 'fail'):
                outcome = {'state': 'failed', 'error_message': str(data.get('e') or 'API rejected request')[:500]}
            # All ambiguous gateway/server errors intentionally remain uncertain.
        except requests.RequestException as exc:
            outcome = {'state': 'uncertain',
                       'error_message': 'Network issue (%s). Check GDEX portal; do not retry.' % type(exc).__name__}

        # Finalize in a dedicated transaction so successful AWBs are durable even
        # if a later DO in the wizard fails or the HTTP response to Odoo is lost.
        try:
            with Registry(dbname).cursor() as cr:
                other = api.Environment(cr, user_id, dict(self.env.context))
                cr.execute('SELECT id FROM stock_picking WHERE id=%s FOR UPDATE', (picking.id,))
                p = other['stock.picking'].browse(picking.id)
                attempt = other['motogene.gdex.consignment'].sudo().browse(attempt_id)
                if outcome['state'] == 'success':
                    if p.mori_awb_no:
                        outcome = {'state': 'uncertain', 'awb': outcome['awb'],
                                   'error_message': 'AWB received but DO field already occupied; review manually.'}
                    else:
                        p.sudo().write({'mori_awb_no': outcome['awb']})
                attempt.write(outcome)
                cr.commit()
        except Exception:
            _logger.exception('GDEX attempt %s result could not be finalized', attempt_id)
            return attempt_id, _('UNKNOWN: database finalization failed; check GDEX portal and attempt history.')
        return attempt_id, (outcome['state'].upper() + ': ' + str(outcome.get('awb') or outcome.get('error_message') or ''))[:400]

    def action_cancel_consignment(self):
        """Lock the DO before the API PUT; never release an AWB on ambiguity."""
        self.ensure_one()
        self._check_operator()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can cancel a GDEX consignment.'))
        if self.state != 'success' or not self.awb:
            raise UserError(_('Only a confirmed, successful AWB can be cancelled.'))
        if not (self.cancellation_reason or '').strip():
            raise UserError(_('Enter a cancellation reason before cancelling.'))
        headers, account, endpoint, environment = self._credentials(environment=self.api_environment)
        dbname, uid = self.env.cr.dbname, self.env.uid
        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM stock_picking WHERE id=%s FOR UPDATE', (self.picking_id.id,))
            attempt = other[self._name].sudo().browse(self.id)
            p = other['stock.picking'].browse(self.picking_id.id)
            if attempt.state != 'success' or p.mori_awb_no != attempt.awb:
                raise UserError(_('AWB state changed; refresh the record.'))
            attempt.write({'state': 'cancel_pending',
                           'error_message': 'Cancellation sent or pending; verify in GDEX before retry.'})
            cr.commit()

        outcome = 'cancel_pending'
        message = 'Cancellation response unknown: verify in myGDEX Prime.'
        try:
            response = requests.put(endpoint + '/CancelConsignment',
                                    headers=headers, params={'consignmentNumber': self.awb},
                                    timeout=(10, 35))
            try:
                data = response.json()
            except ValueError:
                data = {}
            if isinstance(data, dict) and response.status_code == 200 and data.get('s') == 'success':
                outcome, message = 'cancelled', 'Confirmed cancelled by GDEX.'
            elif (isinstance(data, dict) and response.status_code in (400, 401, 403)
                  and data.get('s') == 'fail'):
                outcome = 'success'
                message = str(data.get('e') or 'GDEX refused cancellation')[:500]
        except requests.RequestException as exc:
            message = 'Cancellation network error (%s). Check portal.' % type(exc).__name__

        with Registry(dbname).cursor() as cr:
            other = api.Environment(cr, uid, dict(self.env.context))
            cr.execute('SELECT id FROM stock_picking WHERE id=%s FOR UPDATE', (self.picking_id.id,))
            attempt = other[self._name].sudo().browse(self.id)
            p = other['stock.picking'].browse(self.picking_id.id)
            if attempt.state != 'cancel_pending':
                raise UserError(_('Cancellation status changed; investigate in GDEX.'))
            changes = {'state': outcome, 'error_message': message}
            if outcome == 'cancelled':
                if p.mori_awb_no != attempt.awb:
                    raise UserError(_('DO AWB mismatch; manual review required.'))
                p.sudo().write({'mori_awb_no': False})
                changes.update({'cancelled_by_id': uid, 'cancelled_at': fields.Datetime.now()})
            attempt.write(changes)
            cr.commit()
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('GDEX Cancellation'),
                           'message': message, 'type': 'success' if outcome == 'cancelled' else 'warning',
                           'sticky': True}}

    def action_get_mps(self):
        """Retrieve child CNs for already-created MPS, never CreateConsignment."""
        self.ensure_one()
        self._check_operator()
        if self.state != 'success' or self.pieces <= 1:
            raise UserError(_('MPS retrieval requires a successful multi-piece AWB.'))
        headers, account, endpoint, environment = self._credentials(environment=self.api_environment)
        try:
            response = requests.post(endpoint + '/GetPrimeMPS', headers=headers,
                                     json=[self.awb], timeout=(10, 35))
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise UserError(_('MPS retrieval failed (%s). Parent AWB remains unchanged.') %
                            type(exc).__name__) from exc
        records = data.get('r') if isinstance(data, dict) else None
        result = next((x for x in records if isinstance(x, dict) and
                       x.get('consignmentNote') == self.awb), None) if isinstance(records, list) else None
        numbers = result.get('mpsNumbers') if result else None
        if (response.status_code != 200 or data.get('s') != 'success'
                or not isinstance(numbers, list) or not all(isinstance(n, str) for n in numbers)):
            raise UserError(_('GDEX did not confirm MPS numbers. Parent AWB remains unchanged.'))
        self.write({'mps_numbers': json.dumps(numbers), 'mps_status': 'ready'})
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('GDEX MPS Retrieved'),
                           'message': _('%s child MPS numbers saved.') % len(numbers),
                           'type': 'success', 'sticky': False}}

    def _ensure_pdf(self):
        self.ensure_one()
        self._check_operator()
        if self.state != 'success' or not self.awb:
            raise UserError(_('Only successful consignments may be printed.'))
        if self.pdf_attachment_id:
            return base64.b64decode(self.pdf_attachment_id.datas)
        headers, account, endpoint, environment = self._credentials(environment=self.api_environment)
        try:
            response = requests.get(endpoint+'/GetConsignmentNotePDF',
                headers=headers, params={'consignmentNo': self.awb, 'paperPrintingSize': 'A6',
                                        **({'accountNo': account} if account else {})}, timeout=(10,35))
        except requests.RequestException as exc:
            raise UserError(_('GDEX PDF request failed (%s).') % type(exc).__name__) from exc
        if response.status_code != 200 or not response.content.startswith(b'%PDF'):
            raise UserError(_('GDEX did not return a PDF (HTTP %s).') % response.status_code)
        try:
            out = convert_to_thermal(response.content)
        except Exception as exc:
            _logger.exception('Could not convert GDEX PDF for AWB %s', self.awb)
            raise UserError(_('Unable to convert GDEX PDF to 100 × 150 mm.')) from exc
        att = self.env['ir.attachment'].sudo().create({
            'name': self.awb+'_100x150.pdf', 'type': 'binary',
            'datas': base64.b64encode(out), 'mimetype': 'application/pdf',
            'res_model': self._name, 'res_id': self.id,
        })
        self.pdf_attachment_id = att.id
        return out

    def action_print_single(self):
        self.ensure_one()
        self._ensure_pdf()
        return {'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % self.pdf_attachment_id.id,
                'target': 'self'}

    def action_print_bulk(self):
        """Download one PDF for explicitly selected successful consignments.

        Cached labels are reused; missing labels use GetConsignmentNotePDF
        only. Never call CreateConsignment from this print workflow.
        """
        self._check_operator()
        if not self:
            raise UserError(_('Select at least one GDEX consignment to print.'))
        invalid = self.filtered(lambda rec: rec.state != 'success' or not rec.awb)
        if invalid:
            raise UserError(_('Selection contains %s unconfirmed consignments. Select only successful AWBs.') % len(invalid))

        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        lines = self.sorted(key=lambda rec: (rec.picking_id.name or '', rec.id))
        for rec in lines:
            try:
                label = rec._ensure_pdf()
                reader = PdfReader(io.BytesIO(label))
                for page in reader.pages:
                    writer.add_page(page)
            except Exception as exc:
                _logger.exception('GDEX bulk reprint interrupted for consignment %s', rec.id)
                raise UserError(
                    _('Could not prepare label for %s (DO %s). No combined PDF was generated. '
                      'Other saved labels remain available. Error: %s')
                    % (rec.awb, rec.picking_id.name, str(exc)[:200])
                ) from exc

        stream = io.BytesIO()
        writer.write(stream)
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'GDEX_Bulk_Reprint_%s_labels_100x150.pdf' % len(lines),
            'type': 'binary',
            'datas': base64.b64encode(stream.getvalue()),
            'mimetype': 'application/pdf',
            'res_model': 'res.users',
            'res_id': self.env.uid,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }
