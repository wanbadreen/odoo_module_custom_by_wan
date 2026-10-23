import base64
import io
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

    def init(self):
        # Database-level safeguard against concurrent duplicate requests.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS motogene_gdex_active_picking_uniq
            ON motogene_gdex_consignment (picking_id)
            WHERE state IN ('uncertain', 'success')
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
        }
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
            if attempts.search_count([('picking_id','=',p.id), ('state','in',('uncertain','success'))]):
                return False, _('Blocked: successful or unresolved GDEX request exists.')
            attempt = attempts.create({**vals, 'api_environment': environment,
                'state': 'uncertain', 'error_message': 'Reserved: check GDEX portal before retry if interrupted.',
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
        self._check_operator()
        from pypdf import PdfReader, PdfWriter
        lines = self.filtered(lambda rec: rec.state == 'success')
        if not lines:
            raise UserError(_('No successful GDEX AWBs selected.'))
        writer = PdfWriter()
        for rec in lines:
            reader = PdfReader(io.BytesIO(rec._ensure_pdf()))
            for page in reader.pages:
                writer.add_page(page)
        stream = io.BytesIO()
        writer.write(stream)
        attachment = self.env['ir.attachment'].sudo().create({
            'name': 'GDEX_Bulk_AWB_100x150.pdf', 'type': 'binary',
            'datas': base64.b64encode(stream.getvalue()), 'mimetype': 'application/pdf',
            'res_model': 'res.users', 'res_id': self.env.uid,
        })
        return {'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % attachment.id, 'target': 'self'}
