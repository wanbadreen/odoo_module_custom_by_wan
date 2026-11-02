"""Read-only GDEX shipment tracking for successfully created consignments."""
from datetime import datetime, timezone

import requests

from odoo import fields, models, _
from odoo.exceptions import UserError


def _scan_datetime(value):
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return fields.Datetime.to_string(parsed)
    except (TypeError, ValueError, OverflowError):
        return False


class GdexTrackingEvent(models.Model):
    _name = 'motogene.gdex.tracking.event'
    _description = 'GDEX Tracking Scan'
    _order = 'scan_at desc, id desc'

    consignment_id = fields.Many2one('motogene.gdex.consignment', required=True,
                                      ondelete='cascade', index=True)
    scan_at = fields.Datetime(readonly=True)
    enum_status = fields.Integer(readonly=True)
    status = fields.Char(readonly=True)
    status_detail = fields.Char(readonly=True)
    location = fields.Char(readonly=True)
    undel_code = fields.Char(readonly=True)


class GdexTrackingConsignment(models.Model):
    _inherit = 'motogene.gdex.consignment'

    tracking_status = fields.Char(readonly=True, copy=False)
    tracking_enum = fields.Integer(readonly=True, copy=False)
    tracking_location = fields.Char(readonly=True, copy=False)
    tracking_latest_scan = fields.Datetime(readonly=True, copy=False)
    tracking_checked_at = fields.Datetime(readonly=True, copy=False)
    tracking_error = fields.Char(readonly=True, copy=False)
    tracking_event_ids = fields.One2many('motogene.gdex.tracking.event', 'consignment_id',
                                         readonly=True)

    def action_refresh_tracking(self):
        self._check_operator()
        if not self or len(self) > 25:
            raise UserError(_('Select 1 to 25 GDEX consignments for tracking refresh.'))
        completed = 0
        failures = []
        for consignment in self:
            if not consignment.awb or consignment.state not in ('success', 'cancelled'):
                failures.append('%s: no confirmed AWB' % consignment.picking_id.name)
                continue
            try:
                headers, _account, endpoint, _env = self._credentials(
                    environment=consignment.api_environment)
                last = requests.post(
                    endpoint + '/GetLastShipmentStatus',
                    json=[consignment.awb], headers=headers, timeout=(10, 25))
                obj = last.json()
                results = obj.get('r') if isinstance(obj, dict) else None
                item = next((v for v in results if isinstance(v, dict)
                             and v.get('consignmentNote') == consignment.awb),
                            None) if isinstance(results, list) else None
                if (last.status_code != 200 or not isinstance(obj, dict)
                        or obj.get('s') != 'success' or not item
                        or item.get('isValid') is False):
                    raise ValueError('GDEX did not confirm last status for this AWB')
                detail = requests.get(
                    endpoint + '/GetShipmentStatusDetail',
                    params={'consignmentNumber': consignment.awb},
                    headers=headers, timeout=(10, 25))
                data = detail.json()
                record = data.get('r') if isinstance(data, dict) else None
                if (detail.status_code != 200 or not isinstance(data, dict)
                        or data.get('s') != 'success'
                        or not isinstance(record, dict)
                        or record.get('consignmentNote') != consignment.awb):
                    raise ValueError('GDEX did not confirm shipment history')
                scans = record.get('cnDetailStatusList')
                if not isinstance(scans, list):
                    raise ValueError('Tracking history has unexpected format')
                commands = [(5, 0, 0)]
                for scan in scans[:500]:
                    if not isinstance(scan, dict):
                        raise ValueError('Tracking scan has unexpected format')
                    commands.append((0, 0, {
                        'scan_at': _scan_datetime(scan.get('dateScan')),
                        'enum_status': int(scan.get('enumStatus') or 0),
                        'status': str(scan.get('consignmentNoteStatus') or '')[:200],
                        'status_detail': str(scan.get('statusDetail') or '')[:250],
                        'location': str(scan.get('location') or '')[:200],
                        'undel_code': str(scan.get('undelCode') or '')[:80],
                    }))
                consignment.sudo().write({
                    'tracking_status': str(item.get('consignmentNoteStatus') or
                                           record.get('latestConsignmentNoteStatus') or '')[:200],
                    'tracking_enum': int(item.get('enumStatus') or 0),
                    'tracking_location': str(item.get('location') or '')[:200],
                    'tracking_latest_scan': _scan_datetime(item.get('latestScanDateTime')),
                    'tracking_checked_at': fields.Datetime.now(),
                    'tracking_error': False,
                    'tracking_event_ids': commands,
                })
                completed += 1
            except (requests.RequestException, ValueError, TypeError) as exc:
                failures.append('%s: %s' % (consignment.awb, type(exc).__name__))
                consignment.sudo().write({'tracking_error': str(type(exc).__name__)})
        if completed and not failures:
            # Reload the open form/list so the new scan rows are visible immediately.
            return {'type': 'ir.actions.client', 'tag': 'reload'}
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': _('GDEX Tracking Refresh'),
                       'message': _('%s updated; %s unsuccessful. %s') %
                                  (completed, len(failures), '; '.join(failures[:4])),
                       'type': 'success' if not failures else 'warning', 'sticky': bool(failures)},
        }


class GdexPickingTracking(models.Model):
    _inherit = 'stock.picking'

    def action_open_gdex_tracking(self):
        self.ensure_one()
        self.env['motogene.gdex.consignment']._check_operator()
        if not self.mori_awb_no:
            raise UserError(_('There is no GDEX AWB on this Delivery Order.'))
        rec = self.env['motogene.gdex.consignment'].search([
            ('picking_id', '=', self.id),
            ('awb', '=', self.mori_awb_no),
            ('state', 'in', ['success', 'cancelled']),
        ], limit=1)
        if not rec:
            raise UserError(_('GDEX consignment not found for the exact DO AWB. '
                              'Please review if the AWB was manually changed.'))
        return {'type': 'ir.actions.act_window', 'name': _('GDEX Shipment Tracking'),
                'res_model': 'motogene.gdex.consignment', 'res_id': rec.id,
                'view_mode': 'form', 'target': 'current'}
