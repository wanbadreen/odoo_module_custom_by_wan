"""Automatic hourly GDEX tracking sync.

Read-only towards GDEX. Avoids re-fetching delivered consignments with a
complete history, batches the status API, and loads detailed scans only when
the status or last scan changes (or no history has yet been imported).
"""
import logging
from datetime import datetime, timedelta

import requests

from odoo import api, fields, models

from .tracking import _scan_datetime

_logger = logging.getLogger(__name__)


class GdexAutoTracking(models.Model):
    _inherit = 'motogene.gdex.consignment'

    @api.model
    def _cron_sync_tracking(self):
        """Hourly cron: at most 15 AWBs per run, oldest unsynced first."""
        Cons = self.sudo()
        cutoff = fields.Datetime.to_string(datetime.utcnow() - timedelta(minutes=55))
        # A prior failure is checked again in the next hourly cycle.
        domain = [
            ('state', '=', 'success'),
            ('awb', '!=', False),
            '|',
                ('tracking_checked_at', '=', False),
                ('tracking_checked_at', '<=', cutoff),
            '|', '|',
                ('tracking_status', '=', False),
                ('tracking_enum', '!=', 4),
                ('tracking_event_ids', '=', False),
        ]
        batch = Cons.search(domain, order='tracking_checked_at asc, id asc', limit=15)
        if not batch:
            _logger.info('GDEX auto tracking: no outstanding AWBs to refresh')
            return

        updated = 0
        errors = 0
        for environment in ('production', 'sandbox'):
            group = batch.filtered(lambda c: c.api_environment == environment)
            if not group:
                continue
            try:
                headers, account_no, endpoint, resolved_env = Cons._credentials(
                    environment=environment)
                response = requests.post(
                    endpoint + '/GetLastShipmentStatus',
                    json=group.mapped('awb'), headers=headers, timeout=(10, 25),
                )
                data = response.json()
                if (response.status_code != 200 or not isinstance(data, dict)
                        or data.get('s') != 'success'
                        or not isinstance(data.get('r'), list)):
                    raise ValueError('Invalid bulk tracking response')
                results = {
                    item.get('consignmentNote'): item
                    for item in data['r']
                    if isinstance(item, dict) and item.get('consignmentNote')
                }
            except (requests.RequestException, ValueError, TypeError) as exc:
                _logger.warning('GDEX automatic %s tracking batch error: %s',
                                environment, type(exc).__name__)
                for rec in group:
                    rec.write({
                        'tracking_checked_at': fields.Datetime.now(),
                        'tracking_error': 'Bulk status request failed: %s' % type(exc).__name__,
                    })
                    errors += 1
                continue

            for rec in group:
                try:
                    item = results.get(rec.awb)
                    if not item or item.get('isValid') is False:
                        raise ValueError('AWB missing or invalid in tracking response')
                    raw_status = str(item.get('consignmentNoteStatus') or '')[:200]
                    enum = int(item.get('enumStatus') or 0)
                    scan_at = _scan_datetime(item.get('latestScanDateTime'))
                    # Compare normalized timestamps instead of string vs datetime.
                    prior_scan = fields.Datetime.to_string(rec.tracking_latest_scan) if rec.tracking_latest_scan else False
                    needs_detail = (
                        not rec.tracking_status
                        or rec.tracking_status != raw_status
                        or rec.tracking_enum != enum
                        or prior_scan != scan_at
                        or not rec.tracking_event_ids
                    )

                    changes = {
                        'tracking_status': raw_status,
                        'tracking_enum': enum,
                        'tracking_location': str(item.get('location') or '')[:200],
                        'tracking_latest_scan': scan_at,
                        'tracking_checked_at': fields.Datetime.now(),
                        'tracking_error': False,
                    }
                    if needs_detail:
                        detail = requests.get(
                            endpoint + '/GetShipmentStatusDetail',
                            params={'consignmentNumber': rec.awb},
                            headers=headers, timeout=(10, 25),
                        )
                        detail_data = detail.json()
                        record = detail_data.get('r') if isinstance(detail_data, dict) else None
                        if (detail.status_code != 200 or not isinstance(detail_data, dict)
                                or detail_data.get('s') != 'success'
                                or not isinstance(record, dict)
                                or record.get('consignmentNote') != rec.awb
                                or not isinstance(record.get('cnDetailStatusList'), list)):
                            raise ValueError('Invalid detailed tracking response')
                        lines = [(5, 0, 0)]
                        for item_scan in record['cnDetailStatusList'][:500]:
                            if not isinstance(item_scan, dict):
                                raise ValueError('Malformed tracking scan')
                            lines.append((0, 0, {
                                'scan_at': _scan_datetime(item_scan.get('dateScan')),
                                'enum_status': int(item_scan.get('enumStatus') or 0),
                                'status': str(item_scan.get('consignmentNoteStatus') or '')[:200],
                                'status_detail': str(item_scan.get('statusDetail') or '')[:250],
                                'location': str(item_scan.get('location') or '')[:200],
                                'undel_code': str(item_scan.get('undelCode') or '')[:80],
                            }))
                        changes['tracking_event_ids'] = lines
                    # Isolate writes from one another to avoid a single malformed
                    # shipment preventing updates to other AWBs.
                    with self.env.cr.savepoint():
                        rec.write(changes)
                    updated += 1
                except (requests.RequestException, ValueError, TypeError, OverflowError) as exc:
                    errors += 1
                    _logger.warning('GDEX automatic tracking failed for %s: %s',
                                    rec.awb, type(exc).__name__)
                    with self.env.cr.savepoint():
                        rec.write({
                            'tracking_checked_at': fields.Datetime.now(),
                            'tracking_error': 'Automatic sync: %s' % type(exc).__name__,
                        })

        _logger.info('GDEX auto tracking finished: updated=%s, errors=%s, batch=%s',
                     updated, errors, len(batch))
