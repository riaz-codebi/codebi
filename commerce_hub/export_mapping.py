"""Versioned declarative mappings. Never evaluate Python, expressions or templates."""
from decimal import Decimal, InvalidOperation


class MappingError(ValueError):
    pass


def validate(columns, rules):
    if not isinstance(columns, list) or not 1 <= len(columns) <= 500:
        raise MappingError('Template requires 1–500 columns.')
    names = []
    for col in columns:
        if not isinstance(col, dict) or set(col) - {'name','required'} or not isinstance(col.get('name'), str) or not col['name'].strip() or len(col['name'])>200:
            raise MappingError('Invalid template column definition.')
        names.append(col['name'])
    if len(set(names)) != len(names):
        raise MappingError('Template column names must be unique.')
    if not isinstance(rules, list) or len(rules)>500:
        raise MappingError('Invalid mapping rules.')
    seen = set()
    for rule in rules:
        if not isinstance(rule,dict) or set(rule)-{'target','source','value','default','transform','factor'}:
            raise MappingError('Unsupported mapping configuration.')
        if rule.get('target') not in names or rule['target'] in seen:
            raise MappingError('Mapping targets must be unique template columns.')
        seen.add(rule['target'])
        path = rule.get('source')
        if path is not None and (not isinstance(path,list) or not 1<=len(path)<=10 or path[0] not in ('product','inventory') or any(not isinstance(p,str) or len(p)>200 for p in path)):
            raise MappingError('Source must be an explicit product/inventory key path.')
        if path is not None and 'value' in rule:
            raise MappingError('Use a source or a constant, not both.')
        if rule.get('transform','identity') not in ('identity','text','strip','upper','lower','decimal','multiply'):
            raise MappingError('Unsupported transformation.')
        if rule.get('transform') == 'multiply':
            try:
                if not Decimal(str(rule.get('factor'))).is_finite():
                    raise ValueError
            except (ValueError,InvalidOperation):
                raise MappingError('Multiply requires a finite factor.') from None
        for key in ('value','default'):
            if key in rule and not isinstance(rule[key], (str,int,float,bool,type(None))):
                raise MappingError('Constants/defaults must be scalar values.')


def map_row(product, inventory, columns, rules):
    validate(columns, rules)
    source = {'product': product, 'inventory': inventory or {}}
    result = {c['name']: '' for c in columns}
    for rule in rules:
        value = source
        if 'source' in rule:
            for key in rule['source']:
                value = value.get(key) if isinstance(value,dict) else None
        else:
            value = rule.get('value')
        if value is None or value == '':
            value = rule.get('default','')
        operation = rule.get('transform','identity')
        if isinstance(value,(dict,list)):
            raise MappingError('A scalar destination value is required.')
        try:
            if operation in ('decimal','multiply') and value != '':
                value = Decimal(str(value))
                if operation == 'multiply':
                    value *= Decimal(str(rule['factor']))
                if not value.is_finite():
                    raise ValueError
                value = format(value, 'f')
            elif operation in ('text','strip','upper','lower'):
                value = str(value)
                if operation != 'text':
                    value = getattr(value, operation)()
        except (ValueError,InvalidOperation):
            raise MappingError('Numeric transformation failed.') from None
        result[rule['target']] = value
    missing = [c['name'] for c in columns if c.get('required') and result[c['name']] in ('',None)]
    if missing:
        raise MappingError('Missing required fields: ' + ', '.join(missing))
    return result


def match_skus(products, inventory):
    """Exact, case-sensitive string match. Do not trim, invent or zero-fill SKUs."""
    by_sku = {}
    for row in inventory:
        sku = row.get('vendor_sku')
        if sku:
            by_sku.setdefault(sku, []).append(row)
    product_counts = {}
    for row in products:
        sku = row.get('vendor_sku')
        if sku:
            product_counts[sku] = product_counts.get(sku,0)+1
    matched, errors = [], []
    for product in products:
        sku = product.get('vendor_sku')
        candidates = by_sku.get(sku, [])
        if not sku:
            errors.append({'source_key':product['source_key'],'code':'missing_sku'})
        elif product_counts[sku]!=1 or len(candidates)>1:
            errors.append({'source_key':product['source_key'],'code':'ambiguous_sku'})
        elif not candidates:
            errors.append({'source_key':product['source_key'],'code':'unmatched_sku'})
        else:
            matched.append((product,candidates[0]))
    return matched, errors
