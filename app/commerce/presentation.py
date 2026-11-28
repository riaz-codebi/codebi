"""Client-facing catalog display; never render vendor HTML or technical JSON."""
import re
from urllib.parse import urlsplit, urlunsplit, unquote
from commerce_hub.product_identity import catalog_sku


def product_row(product,vendor_code):
    attributes=product.attributes
    price=next((entry.get('text') for entry in attributes.get('price_fields',[])
                if isinstance(entry,dict) and entry.get('class')=='detailprice'),None)
    if isinstance(price,str):price=re.sub(r'^\s*price\s*:\s*','',price,flags=re.IGNORECASE).strip()
    return {'id':product.id,'key':product.source_key,'sku':catalog_sku(product.source_key,product.vendor_sku,vendor_code),
            'name':attributes.get('name') or 'Unnamed product','price':price,'attributes':attributes,
            'category':attributes.get('category_path') or '', 'extracted_at':product.extracted_at,
            'image_count':len({url for entry in attributes.get('images',[]) if isinstance(entry,dict)
                               if (url:=image_url(entry.get('url'),vendor_code))})}


def image_url(value,vendor_code):
    if vendor_code!='shiloh_tack' or not isinstance(value,str):return None
    try:
        url=urlsplit(value)
        path=unquote(url.path)
        if (url.scheme!='https' or url.hostname not in ('www.shilohtack.com','shilohtack.com')
            or url.username or url.password or url.port not in (None,443)
            or not path.startswith('/prodimages/') or '..' in path.split('/')
            or '\\' in path or any(ord(c)<32 for c in path)):
            return None
        return urlunsplit(('https','www.shilohtack.com',url.path,'',''))
    except ValueError:
        return None


def product_details(product,vendor_code):
    data=product_row(product,vendor_code)
    attributes=product.attributes
    images=[];seen=set()
    for entry in attributes.get('images',[]):
        if not isinstance(entry,dict):continue
        url=image_url(entry.get('url'),vendor_code)
        if url and url not in seen:
            seen.add(url);images.append({'url':url,'alt':entry.get('alt') or data['name']})
    tabs=[]
    source=attributes.get('tabs',[])
    for tab in source:
        if not isinstance(tab,dict) or not (tab.get('text') or tab.get('blocks')):continue
        label=tab.get('label') or 'Additional information'
        if label.startswith('Unmapped'):label='Additional information'
        tabs.append({'label':label,'text':tab.get('text',''),'blocks':tab.get('blocks',[])})
    if not tabs and attributes.get('description_text'):
        tabs=[{'label':'Description','text':attributes['description_text'],'blocks':[]}]
    options=[]
    for entry in attributes.get('dropdowns',[]):
        if not isinstance(entry,dict):continue
        choices=[str(o.get('label','')) for o in entry.get('options',[])
                 if isinstance(o,dict) and not o.get('is_placeholder') and o.get('value') not in ('',None)]
        if choices:options.append({'label':entry.get('label') or 'Options','choices':choices})
    data.update(images=images,tabs=tabs,options=options,category=attributes.get('category_path',''),
                quantity_pricing=attributes.get('quantity_pricing',[]),availability=attributes.get('stock_notice'),
                badges=[label for flag,label in [('onsale','Sale'),('assorted','Sold assorted')] if flag in attributes.get('flags',[])])
    return data


def run_statistics(summary,selection=None):
    def count(key):
        value=summary.get(key,0)
        return value if isinstance(value,(int,float)) else 0
    category=(selection or {}).get('mode')=='category'
    removed=('Removed from category',count('category_memberships_removed')) if category else ('Removed from catalog',count('removed_products'))
    metrics=[('Products captured',count('record_count')),('Images',count('image_count')),
             ('New products',count('new_products')),('Updated products',count('changed_products')),
             removed,('Products with options',count('products_with_dropdowns'))]
    def distribution(key):
        values=summary.get(key,{})
        return sorted(values.items(),key=lambda pair:int(pair[0]) if str(pair[0]).isdigit() else 0) if isinstance(values,dict) else []
    return {'metrics':metrics,'categories':sorted(summary.get('category_paths',{}).items()),
            'images':distribution('image_distribution'),'tabs':distribution('tab_count_distribution'),
            'category_coverage':count('category_coverage'),'total':count('record_count'),
            'quantity_pricing':count('products_with_quantity_pricing')}
