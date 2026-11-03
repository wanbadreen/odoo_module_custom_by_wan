"""Read-only per-parcel COD tracking via the separate COD API credentials."""
import logging
from datetime import datetime, timedelta

import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .config import DEMO, PRODUCTION
from .tracking import _scan_datetime

_logger = logging.getLogger(__name__)


class GdexCodTrackingEvent(models.Model):
    _name = 'motogene.gdex.cod.tracking.event'
    _description = 'GDEX COD Shipment Scan'
    _order = 'scan_at desc, id desc'

    consignment_id = fields.Many2one('motogene.gdex.cod.consignment', required=True,
                                      ondelete='cascade', index=True)
    scan_at = fields.Datetime(readonly=True)
    enum_status = fields.Integer(readonly=True)
    status = fields.Char(readonly=True)
    status_detail = fields.Char(readonly=True)
    location = fields.Char(readonly=True)
    undel_code = fields.Char(readonly=True)


class GdexCodTracking(models.Model):
    _inherit = 'motogene.gdex.cod.consignment'

    tracking_status = fields.Char(readonly=True, copy=False)
    tracking_enum = fields.Integer(readonly=True, copy=False)
    tracking_location = fields.Char(readonly=True, copy=False)
    tracking_latest_scan = fields.Datetime(readonly=True, copy=False)
    tracking_checked_at = fields.Datetime(readonly=True, copy=False)
    tracking_error = fields.Char(readonly=True, copy=False)
    tracking_event_ids = fields.One2many('motogene.gdex.cod.tracking.event',
                                         'consignment_id', readonly=True)

    def _tracking_credentials(self):
        self.ensure_one()
        params = self.env['ir.config_parameter'].sudo()
        prefix = 'motogene_gdex_normal.cod_' + self.api_environment + '_'
        token, key = params.get_param(prefix + 'token'), params.get_param(prefix + 'key')
        if not token or not key:
            raise UserError(_('GDEX COD %s credentials are missing.') % self.api_environment)
        return ({'ApiToken': token, 'Subscription-Key': key,
                 'Content-Type': 'application/json-patch+json',
                 'Accept': 'application/json'},
                PRODUCTION if self.api_environment == 'production' else DEMO)

    def _refresh_cod_tracking_one(self, last_item=None):
        self.ensure_one()
        if self.state != 'success' or not self.awb:
            raise ValueError('COD AWB is not successful')
        headers, endpoint = self._tracking_credentials()
        if last_item is None:
            r = requests.post(endpoint + '/GetLastShipmentStatus',
                              headers=headers, json=[self.awb], timeout=(10, 25))
            data = r.json()
            rows = data.get('r') if isinstance(data, dict) else None
            if r.status_code != 200 or data.get('s') != 'success' or not isinstance(rows, list):
                raise ValueError('COD last tracking status not confirmed')
            last_item = next((x for x in rows
                              if isinstance(x, dict) and x.get('consignmentNote') == self.awb), None)
        if not last_item or last_item.get('isValid') is False:
            raise ValueError('COD AWB missing from GDEX tracking response')
        status = str(last_item.get('consignmentNoteStatus') or '')[:200]
        enum = int(last_item.get('enumStatus') or 0)
        scan_at = _scan_datetime(last_item.get('latestScanDateTime'))
        prior_scan = (fields.Datetime.to_string(self.tracking_latest_scan)
                      if self.tracking_latest_scan else False)
        refresh_history = (not self.tracking_status or self.tracking_status != status
                           or self.tracking_enum != enum or prior_scan != scan_at
                           or not self.tracking_event_ids)
        values = {
            'tracking_status': status, 'tracking_enum': enum,
            'tracking_location': str(last_item.get('location') or '')[:200],
            'tracking_latest_scan': scan_at,
            'tracking_checked_at': fields.Datetime.now(), 'tracking_error': False,
        }
        if refresh_history:
            response = requests.get(
                endpoint + '/GetShipmentStatusDetail',
                headers=headers, params={'consignmentNumber': self.awb}, timeout=(10, 25))
            details = response.json()
            record = details.get('r') if isinstance(details, dict) else None
            if (response.status_code != 200 or not isinstance(details, dict)
                    or details.get('s') != 'success' or not isinstance(record, dict)
                    or record.get('consignmentNote') != self.awb
                    or not isinstance(record.get('cnDetailStatusList'), list)):
                raise ValueError('COD shipment tracking history not confirmed')
            commands = [(5, 0, 0)]
            for scan in record['cnDetailStatusList'][:500]:
                if not isinstance(scan, dict):
                    raise ValueError('Malformed COD tracking scan')
                commands.append((0, 0, {
                    'scan_at': _scan_datetime(scan.get('dateScan')),
                    'enum_status': int(scan.get('enumStatus') or 0),
                    'status': str(scan.get('consignmentNoteStatus') or '')[:200],
                    'status_detail': str(scan.get('statusDetail') or '')[:250],
                    'location': str(scan.get('location') or '')[:200],
                    'undel_code': str(scan.get('undelCode') or '')[:80],
                }))
            values['tracking_event_ids'] = commands
        self.sudo().write(values)

    def action_refresh_cod_tracking(self):
        self._check_operator()
        if not self or len(self) > 25:
            raise UserError(_('Select between 1 and 25 COD consignments.'))
        failures, completed = [], 0
        for rec in self:
            try:
                rec._refresh_cod_tracking_one()
                completed += 1
            except (requests.RequestException, ValueError, TypeError, OverflowError, UserError) as exc:
                failures.append('%s: %s' % (rec.awb or rec.id, type(exc).__name__))
                rec.sudo().write({'tracking_error': str(type(exc).__name__),
                                  'tracking_checked_at': fields.Datetime.now()})
        if not failures:
            return {'type': 'ir.actions.client', 'tag': 'reload'}
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('GDEX COD Tracking'),
                           'message': _('%s updated, %s unsuccessful: %s') %
                                      (completed, len(failures), '; '.join(failures[:4])),
                           'type': 'warning', 'sticky': True}}

    @api.model
    def _cron_sync_cod_tracking(self):
        """Hourly read-only COD sync in small batches; never touch stock picking status."""
        cutoff = fields.Datetime.to_string(datetime.utcnow() - timedelta(minutes=55))
        domain = [
            ('state', '=', 'success'), ('awb', '!=', False),
            '|', ('tracking_checked_at', '=', False), ('tracking_checked_at', '<=', cutoff),
            '|', '|', ('tracking_status', '=', False), ('tracking_enum', '!=', 4),
            ('tracking_event_ids', '=', False),
        ]
        batch = self.sudo().search(domain, order='tracking_checked_at asc, id asc', limit=15)
        for environment in ('production', 'sandbox'):
            recs = batch.filtered(lambda c: c.api_environment == environment)
            if not recs:
                continue
            try:
                headers, endpoint = recs[0]._tracking_credentials()
                response = requests.post(
                    endpoint + '/GetLastShipmentStatus', headers=headers,
                    json=recs.mapped('awb'), timeout=(10, 25))
                data = response.json()
                if (response.status_code != 200 or not isinstance(data, dict)
                        or data.get('s') != 'success' or not isinstance(data.get('r'), list)):
                    raise ValueError('COD bulk tracking status not confirmed')
                results = {x.get('consignmentNote'): x for x in data['r']
                           if isinstance(x, dict) and x.get('consignmentNote')}
            except (requests.RequestException, ValueError, TypeError, UserError) as exc:
                _logger.warning('GDEX COD tracking batch failed (%s): %s',
                                environment, type(exc).__name__)
                recs.write({'tracking_error': 'COD bulk sync: %s' % type(exc).__name__,
                            'tracking_checked_at': fields.Datetime.now()})
                continue
            for rec in recs:
                try:
                    with self.env.cr.savepoint():
                        rec._refresh_cod_tracking_one(last_item=results.get(rec.awb))
                except (requests.RequestException, ValueError, TypeError, OverflowError, UserError) as exc:
                    _logger.warning('GDEX COD tracking failed (%s): %s', rec.awb, type(exc).__name__)
                    with self.env.cr.savepoint():
                        rec.write({'tracking_error': 'COD sync: %s' % type(exc).__name__,
                                   'tracking_checked_at': fields.Datetime.now()})
