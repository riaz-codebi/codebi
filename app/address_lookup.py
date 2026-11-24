"""Click-triggered Google Places lookup; credentials and usage controls stay in Flask."""
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from flask import current_app, g, jsonify, request

from app.auth import check_csrf
from app.database import transaction
from app.models import Country, StateRegion, Timezone
from sqlalchemy.exc import SQLAlchemyError


def reserve_request(actor):
    """Atomically count attempts across workers. No address data is stored here."""
    app = current_app
    path = app.config.get('ADDRESS_USAGE_PATH') or Path(app.extensions['auth_store'].path).with_name('address_usage.sqlite3')
    now = datetime.now(timezone.utc)
    buckets = [(now.strftime('month:%Y-%m'), app.config['GOOGLE_PLACES_MONTHLY_LIMIT']),
               (now.strftime('minute:%Y-%m-%dT%H:%M:') + hashlib.sha256(actor.encode()).hexdigest(),
                app.config['GOOGLE_PLACES_PER_MINUTE_LIMIT'])]
    with closing(sqlite3.connect(str(path), timeout=5)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS address_usage (bucket TEXT PRIMARY KEY, requests INTEGER NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        for bucket, limit in buckets:
            row = db.execute('SELECT requests FROM address_usage WHERE bucket = ?', (bucket,)).fetchone()
            if (row[0] if row else 0) >= limit:
                return False
        for bucket, _limit in buckets:
            db.execute('INSERT INTO address_usage VALUES (?, 1) ON CONFLICT(bucket) DO UPDATE SET requests = requests + 1', (bucket,))
        db.execute("DELETE FROM address_usage WHERE bucket LIKE 'minute:%' AND bucket < ?", (now.strftime('minute:%Y-%m-%dT%H:%M:'),))
    return True


def unavailable(message='Address lookup is unavailable. Enter the address manually.', status=503):
    return jsonify({'error': message}), status


def suggestions():
    check_csrf()
    key = current_app.config.get('GOOGLE_PLACES_API_KEY')
    if not key:
        return unavailable()
    actor = str(g.portal_auth_user_id if hasattr(g, 'portal_auth_user_id') else g.registration_identity.auth_id)
    store = current_app.extensions['auth_store']
    action = request.form.get('action', 'search')
    country = request.form.get('country', '').upper()
    if country and country not in ('US', 'CA'):
        return unavailable('Lookup supports US and Canadian addresses. Enter other addresses manually.', 400)
    if action == 'search':
        text = request.form.get('text', '').strip()
        if not 5 <= len(text) <= 250:
            return unavailable('Enter between 5 and 250 characters to find an address.', 400)
    elif action == 'details':
        choice = store.load(request.form.get('selection'), consume=True)
        if not choice or choice.get('kind') != 'address-choice' or choice.get('actor') != actor:
            return unavailable('This selection expired. Find the address again.', 400)
    else:
        return unavailable('Invalid lookup action.', 400)
    try:
        if not reserve_request(actor):
            return unavailable('Address lookup limit reached. Enter the address manually.', 429)
        headers = {'X-Goog-Api-Key': key}
        with httpx.Client(timeout=8) as client:
            if action == 'search':
                response = client.post('https://places.googleapis.com/v1/places:autocomplete', headers=headers,
                    json={'input': text, 'includedRegionCodes': [country.lower()] if country else ['us', 'ca'],
                          'languageCode': 'en'})
                response.raise_for_status()
                result = []
                for row in response.json().get('suggestions', [])[:5]:
                    prediction = row.get('placePrediction', {})
                    place_id = prediction.get('placeId')
                    label = prediction.get('text', {}).get('text')
                    if isinstance(place_id, str) and isinstance(label, str) and len(place_id) <= 255:
                        selection = store.create({'kind': 'address-choice', 'actor': actor,
                                                  'place_id': place_id, 'country': country}, 600)
                        result.append({'label': label[:500], 'selection': selection})
                return jsonify(result)
            headers['X-Goog-FieldMask'] = 'addressComponents,formattedAddress,attributions,timeZone'
            response = client.get('https://places.googleapis.com/v1/places/' + quote(choice['place_id'], safe=''),
                                  headers=headers, params={'languageCode': 'en'})
            response.raise_for_status()
            data = response.json()
        parts = data.get('addressComponents', [])
        def part(kind, short=False):
            return next((str(p.get('shortText' if short else 'longText') or '')[:200]
                         for p in parts if kind in p.get('types', [])), '')
        code = part('country', True).upper()
        if code not in ('US', 'CA') or (choice['country'] and code != choice['country']):
            return unavailable('The result does not match the selected country. Enter the address manually.', 400)
        with transaction() as db:
            known_country = db.get(Country, code)
            region_code = code + '-' + part('administrative_area_level_1', True).upper()
            region = db.get(StateRegion, region_code)
            if not known_country or not known_country.is_active:
                return unavailable()
            state = region_code if region and region.is_active else ''
            zone = (data.get('timeZone') or {}).get('id', '')
            known_zone = db.get(Timezone, zone) if isinstance(zone, str) and zone else None
            zone = zone if known_zone and known_zone.is_active else ''
        attributions = []
        for item in data.get('attributions', []):
            uri = str(item.get('uri') or '')
            if uri.startswith('https://'):
                attributions.append({'name': str(item.get('provider') or 'Data provider')[:200], 'uri': uri[:2000]})
        return jsonify({'address_line_1': ' '.join(filter(None, [part('street_number'), part('route')]))[:200],
                        'city': part('locality') or part('postal_town') or part('sublocality_level_1'),
                        'postal_code': part('postal_code'), 'country_code': code, 'state_region_code': state,
                        'timezone': zone, 'attributions': attributions})
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError, sqlite3.Error, SQLAlchemyError):
        return unavailable()
