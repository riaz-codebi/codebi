"""Shared client-form validation/choices for administration and registration."""
import re
from app.services import CLIENT_FIELDS
EMAIL = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def validate_client(values):
    errors = {}
    if not values['name']:
        errors['name'] = 'Enter the client name.'
    for field in CLIENT_FIELDS:
        if len(values[field]) > (254 if field in ('email', 'contact_email') else 200):
            errors[field] = 'This value is too long.'
    for field in ('email', 'contact_email'):
        if values[field] and not EMAIL.fullmatch(values[field]):
            errors[field] = 'Enter a valid email address.'
    if values['website'] and not re.match(r'^https?://[^\s/]+', values['website']):
        errors['website'] = 'Use a complete https:// or http:// website address.'
    if values['country_code'] and not re.fullmatch(r'[A-Za-z]{2}', values['country_code']):
        errors['country_code'] = 'Use a two-letter country code, such as US.'
    return errors



def catalog_choices(industries, catalogs, values, existing=None):
    existing = existing or {}
    legacy_state = existing.get('state_region') if not existing.get('state_region_code') else None
    industries = [i for i in industries if i['is_active'] or i['id'] == values['industry_id']]
    choices = {
        'industry_id': [{'value': i['id'], 'label': i['name']} for i in industries],
        'country_code': [{'value': c['code'], 'label': c['name']} for c in catalogs['countries']],
        'state_region_code': [{'value': r['code'], 'label': r['name']} for r in catalogs['regions']],
        'timezone': [{'value': t['name'], 'label': t['name']} for t in catalogs['timezones']],
    }
    for key in ('country_code', 'timezone'):
        if values[key] and values[key] == existing.get(key) and not any(o['value'] == values[key] for o in choices[key]):
            choices[key].append({'value': values[key], 'label': values[key] + ' (saved value)'})
    if legacy_state:
        choices['state_region_code'].insert(0, {'value': '__legacy__', 'label': legacy_state + ' (saved value)'})
    return choices
