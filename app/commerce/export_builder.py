"""Allowlisted catalog-to-Shopify mappings and deterministic multi-image CSV rows."""
from decimal import Decimal, InvalidOperation
from html import escape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlsplit

from app.commerce.presentation import product_details
from commerce_hub.export_files import csv_file
from commerce_hub.export_mapping import MappingError, validate

FIELDS={
    'name':'Product name','product_id':'Product ID / SKU','description':'Description text',
    'key_features':'Key Features text','specifications':'Specifications text',
    'category_path':'Category path','price':'Price amount','vendor_name':'Vendor name',
    'source_url':'Product page URL','stock_notice':'Availability text',
    'captured_at':'Last captured timestamp','image_count':'Number of images',
    'quantity_pricing':'Quantity pricing text','price_observations':'All observed price labels/text',
    'image_url':'All product images','image_position':'Image position','image_alt':'Image alt text',
    'on_sale':'Sale flag','sold_assorted':'Sold assorted flag',
    **{f'option_{i}_{key}':f'Option {i} {label}' for i in range(1,4)
       for key,label in [('name','name'),('values','choices (not variant SKUs)')]},
}
AUTOMATIC={'handle':'URL handle from product title','description':'Description + features + specifications',
           'type':'Leaf category','tags':'Category tags','seo_title':'SEO title from name',
           'seo_description':'SEO summary from description','option_name':'Default option name (Title)',
           'option_value':'Default option value (Default Title)'}
DEFAULT_VALUE_DETAILS={
    'handle':('Lowercases the product title and replaces spaces and punctuation with dashes.','URL handle'),
    'description':('Combines the captured product tabs, preserving headings, bullet points and tables. The sample below shows their text.','Description'),
    'type':('Uses the last category in the vendor category path; it does not translate Shopify taxonomy.','Type'),
    'tags':('Creates comma-separated category tags, excluding Home and removing duplicates.','Tags'),
    'seo_title':('Uses the captured product name, limited to its first 70 characters.','SEO title'),
    'seo_description':('Tidies the captured description text and uses its first 320 characters. This is not an AI-written summary.','SEO description'),
    'option_name':('Outputs the literal word Title for a simple product without selectable variants.','Option1 name'),
    'option_value':('Outputs the literal text Default Title, paired with the default option name.','Option1 value'),
}
DEFAULT_VALUE_EXAMPLES={
    'handle':'Leather Saddle Pad becomes leather-saddle-pad.',
    'description':'Description: Soft blanket; Key Features: Warm lining; Specifications: Size 60 x 80. These appear together as formatted sections.',
    'type':'Home > Gift Items > Kitchen Accessories becomes Kitchen Accessories.',
    'tags':'Home > Gift Items > Kitchen Accessories becomes Gift Items, Kitchen Accessories.',
    'seo_title':'Leather Saddle Pad stays Leather Saddle Pad. A name longer than 70 characters is cut at 70.',
    'seo_description':'"Soft   blanket for cold nights." becomes "Soft blanket for cold nights." Longer descriptions stop at 320 characters.',
    'option_name':'A single cutting board with no Size or Color choices exports Title as the option name.',
    'option_value':'The same cutting board exports Default Title as the option value, paired with Title.',
}
REQUIRED={'Title','URL handle','SKU','Price','Option1 name','Option1 value'}
BOOLEAN={'Published on online store','Charge tax','Requires shipping','Gift card','Google Shopping / Custom product'}
NUMERIC={'Price','Compare-at price','Cost per item','Weight value (grams)','Inventory quantity',
         'Unit price total measure','Unit price base measure','Packed product length','Packed product width','Packed product height'}
IMAGE_TARGETS={'Product image URL','Image position','Image alt text'}


def specification_fields(products):
    names=set()
    for product in products:
        for tab in product.attributes.get('tabs',[]):
            for block in tab.get('blocks',[]):
                for pair in block.get('label_value_pairs',[]):
                    name=str(pair.get('label','')).strip().rstrip(':').strip()
                    if name and len(name)<=150:names.add(name)
    return {('spec:'+name):('Specification: '+name) for name in sorted(names)[:200]}


def defaults(columns, *, has_options=False):
    sources={'Title':'name','SKU':'product_id','Price':'price','Vendor':'vendor_name',
             'Product image URL':'image_url','Image position':'image_position','Image alt text':'image_alt'}
    automatic={'URL handle':'handle','Description':'description','Type':'type','Tags':'tags',
               'SEO title':'seo_title','SEO description':'seo_description',
               'Option1 name':'option_name','Option1 value':'option_value'}
    constants={'Status':'draft','Published on online store':'FALSE','Requires shipping':'TRUE',
               'Fulfillment service':'manual','Gift card':'FALSE'}
    result=[]
    for column in columns:
        name=column['name']
        if name in sources:result.append({'target':name,'source':['product',sources[name]]})
        elif name in automatic:result.append({'target':name,'source':['product','auto.'+automatic[name]]})
        elif name in constants:result.append({'target':name,'value':constants[name]})
    if has_options:
        options={'Option1 name':{'value':'Size'},'Option1 value':{'source':['product','option_1_values']},
                 'Option2 name':{'value':'Color'},'Option2 value':{'source':['product','option_2_values']}}
        existing={rule['target']:rule for rule in result}
        existing.update({target:{'target':target,**rule} for target,rule in options.items()})
        result=[existing[column['name']] for column in columns if column['name'] in existing]
    return result


def validate_rules(columns,rules,fields):
    validate(columns,rules)
    selected={r['target'] for r in rules}
    missing=(REQUIRED | {c['name'] for c in columns if c.get('required')})-selected
    if missing:raise MappingError('Include and map: '+', '.join(sorted(missing)))
    if not rules:raise MappingError('Select fields to include.')
    for rule in rules:
        if set(rule)-{'target','source','value'}:raise MappingError('Unsupported mapping rule. Review and save it in the editor.')
        if 'source' in rule:
            path=rule['source']
            if len(path)!=2 or path[0]!='product' or path[1] not in set(fields)|{'auto.'+key for key in AUTOMATIC}:
                raise MappingError('Select an available catalog field for '+rule['target']+'.')
        elif 'value' not in rule or not str(rule['value']).strip():
            raise MappingError('Enter a fixed value for '+rule['target']+'.')
        if rule['target']=='Product category' and rule.get('source')==['product','category_path']:
            raise MappingError('Shopify Product category needs a Shopify taxonomy value. Use vendor categories for Type or Tags.')
        # Array/gallery expansion has an explicit meaning, not a scalar JSON coercion.
        if rule.get('source')==['product','image_url'] and rule['target']!='Product image URL':
            raise MappingError('Map All product images to Product image URL.')
    if ('Image position' in selected or 'Image alt text' in selected) and 'Product image URL' not in selected:
        raise MappingError('Include Product image URL when mapping image position or alt text.')
    dimensions={'Packed product length','Packed product width','Packed product height','Packed product dimension unit'}
    if selected & dimensions and not dimensions<=selected:raise MappingError('Include all four packed product dimension fields, or none.')
    for number in (2,3):
        pair={f'Option{number} name',f'Option{number} value'}
        if selected & pair and not pair<=selected:
            raise MappingError(f'Include both Option{number} name and Option{number} value, or neither.')


def editor_rows(columns,rules):
    saved={r['target']:r for r in rules}
    result=[]
    for i,column in enumerate(columns):
        rule=saved.get(column['name'],{})
        path=rule.get('source',[])
        key=path[1] if len(path)==2 and path[0]=='product' else ''
        automatic=key.startswith('auto.')
        result.append({'index':i,'name':column['name'],'required':column['name'] in REQUIRED or column.get('required'),
            'included':bool(rule),'mode':'automatic' if automatic else ('field' if path else 'constant' if rule else 'field'),
            'field':key if not automatic else '', 'automatic':key[5:] if automatic else '',
            'constant':str(rule.get('value',''))})
    return result


def form_rules(columns,form):
    result=[]
    for i,column in enumerate(columns):
        if form.get('include_'+str(i))!='on':continue
        mode=form.get('mode_'+str(i),'')
        rule={'target':column['name']}
        if mode=='field':rule['source']=['product',form.get('field_'+str(i),'')]
        elif mode=='automatic':rule['source']=['product','auto.'+form.get('automatic_'+str(i),'')]
        elif mode=='constant':rule['value']=str(form.get('constant_'+str(i),'')).strip()[:2000]
        else:raise MappingError('Choose a value source for '+column['name']+'.')
        result.append(rule)
    return result


def blocks_html(blocks,depth=0):
    if depth>8:raise MappingError('Description nesting is too deep.')
    result=[]
    for block in blocks:
        text=escape(str(block.get('text','')))
        if block.get('type')=='list':
            tag='ol' if block.get('ordered') else 'ul'
            result.append('<'+tag+'>'+''.join('<li>'+escape(str(item.get('text','')))+blocks_html(item.get('children',[]),depth+1)+'</li>' for item in block.get('items',[]))+'</'+tag+'>')
        elif block.get('type')=='table':
            rows=[]
            for row in block.get('rows',[]):
                cells=[]
                for cell in row.get('cells',[]):
                    tag='th' if cell.get('header') else 'td'
                    def span(key):
                        try:return max(1,min(100,int(cell.get(key,1))))
                        except (ValueError,TypeError):return 1
                    cells.append(f'<{tag} rowspan="{span("rowspan")}" colspan="{span("colspan")}">'+escape(str(cell.get('text','')))+f'</{tag}>')
                rows.append('<tr>'+''.join(cells)+'</tr>')
            result.append('<table>'+''.join(rows)+'</table>')
        else:
            tag='h3' if block.get('type')=='heading' else 'p'
            result.append('<'+tag+'>'+text+'</'+tag+'>')
    return ''.join(result)


def title_handle(title):
    text=unicodedata.normalize('NFKD',str(title)).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','-',text).strip('-')


def normalized(product,vendor):
    detail=product_details(product,vendor.code)
    attributes=product.attributes
    tabs=attributes.get('tabs',[])
    def tab_text(fragment):return '\n'.join(str(t.get('text','')) for t in tabs if fragment in str(t.get('label','')).casefold())
    sections=[]
    for tab in tabs:
        body=blocks_html(tab.get('blocks',[])) or '<p>'+escape(str(tab.get('text','')))+'</p>'
        sections.append('<h2>'+escape(str(tab.get('label') or 'Details'))+'</h2>'+body)
    description=''.join(sections) or '<p>'+escape(str(attributes.get('description_text') or ''))+'</p>'
    category=attributes.get('category_path') or ''
    levels=attributes.get('category_levels') or [part.strip() for part in re.split(r'\s*(?:>|\u00bb)\s*',category) if part.strip() and part.strip().casefold()!='home']
    handle=title_handle(detail['name'])
    result={'name':detail['name'],'product_id':detail['sku'],'price':detail['price'] or '',
        'description':attributes.get('description_text') or tab_text('description'),
        'key_features':tab_text('feature'),'specifications':tab_text('spec'),
        'category_path':category,'vendor_name':vendor.name,'source_url':product.source_url or '',
        'stock_notice':attributes.get('stock_notice') or '',
        'captured_at':product.extracted_at.isoformat() if product.extracted_at else '',
        'image_count':str(len(detail['images'])),
        'quantity_pricing':'\n'.join(str(tier.get('quantity',''))+': '+str(tier.get('price','')) for tier in attributes.get('quantity_pricing',[])),
        'price_observations':'\n'.join(str(entry.get('class',''))+': '+str(entry.get('text','')) for entry in attributes.get('price_fields',[])),
        'on_sale':str('onsale' in attributes.get('flags',[])).upper(),
        'sold_assorted':str('assorted' in attributes.get('flags',[])).upper(),
        'auto.handle':handle,'auto.description':description,'auto.type':levels[-1] if levels else '',
        'auto.tags':', '.join(dict.fromkeys(levels)), 'auto.seo_title':detail['name'][:70],
        'auto.seo_description':' '.join(str(attributes.get('description_text') or tab_text('description')).split())[:320],
        'auto.option_name':'Title','auto.option_value':'Default Title'}
    for i,option in enumerate(attributes.get('dropdowns',[])[:3],1):
        result[f'option_{i}_name']=option.get('label') or ''
        result[f'option_{i}_values']=', '.join(str(o.get('label','')) for o in option.get('options',[]) if o.get('value') not in ('',None) and not o.get('is_placeholder'))
    for tab in tabs:
        for block in tab.get('blocks',[]):
            for pair in block.get('label_value_pairs',[]):
                name=str(pair.get('label','')).strip().rstrip(':').strip()
                key='spec:'+name
                value=str(pair.get('value',''))
                if key in result and result[key]!=value:result[key]=''  # Ambiguous specifications aren't guessed.
                else:result[key]=value
    return result,detail['images']


def mapping_sample(products,vendor,fields,product_id=None,strict=False):
    """A real published observation, chosen by populated mapping values, never fabricated."""
    selected=next((product for product in products if product.source_key==product_id),None) if product_id else None
    if strict and not selected:raise MappingError('Product ID was not found in the current published catalog.')
    if not products:return None
    def sample_values(product):
        values,images=normalized(product,vendor)
        values.update(image_url='\n'.join(image['url'] for image in images),
                      image_position=', '.join(str(i+1) for i in range(len(images))),
                      image_alt='\n'.join(image.get('alt') or values['name'] for image in images))
        return values
    if selected is not None:values=sample_values(selected)
    else:
        # Stable ties follow the source-key ordering of the publication manifest.
        selected,values=max(((product,sample_values(product)) for product in products),
                            key=lambda pair:sum(bool(str(value).strip()) for value in pair[1].values()))
    class TextPreview(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
        def handle_data(self,text):self.parts.append(text)
        def handle_starttag(self,tag,attrs):
            if tag in ('p','h2','h3','li','tr','br'):self.parts.append('\n')
            elif tag in ('td','th'):self.parts.append(' | ')
        def handle_endtag(self,tag):
            if tag in ('p','h2','h3','li','tr'):self.parts.append('\n')
    parser=TextPreview();parser.feed(values.get('auto.description',''))
    values['auto.description']='\n'.join(line.strip(' |') for line in ''.join(parser.parts).splitlines() if line.strip(' |'))
    labels=fields|{'auto.'+key:label for key,label in AUTOMATIC.items()}
    priority=['name','product_id','vendor_name','category_path','auto.type','auto.tags','price','image_count']
    keys=priority+[key for key in labels if key not in priority]
    rows=[]
    for key in keys:
        value=str(values.get(key,'') or '')
        if len(value)>8000:value=value[:8000]+' ... (preview shortened)'
        rows.append({'key':key,'label':labels[key],'value':value})
    samples={row['key']:row['value'] for row in rows}
    default_values=[{'option':label,'description':DEFAULT_VALUE_DETAILS[key][0],
                     'example':DEFAULT_VALUE_EXAMPLES[key],
                     'target':DEFAULT_VALUE_DETAILS[key][1],'value':samples.get('auto.'+key,'')}
                    for key,label in AUTOMATIC.items()]
    return {'product_id':selected.source_key,'name':values['name'],'rows':rows,'default_values':default_values}


def cell_value(target,value):
    value='' if value is None else str(value).strip()
    if target in NUMERIC and value:
        raw=value
        if target in {'Price','Compare-at price','Cost per item'}:
            raw=re.sub(r'^\s*price\s*:\s*','',raw,flags=re.I).lstrip('$').strip()
            if re.fullmatch(r'\d{1,3}(,\d{3})+(\.\d+)?',raw):raw=raw.replace(',','')
        try:
            number=Decimal(raw)
            if not number.is_finite() or number<0:raise ValueError
            if target in {'Weight value (grams)','Inventory quantity'} and number!=number.to_integral_value():raise ValueError
        except (InvalidOperation,ValueError):raise MappingError(target+' requires a non-negative number.') from None
        value=format(number,'f')
    if target in BOOLEAN and value:
        if value.lower() not in ('true','false'):raise MappingError(target+' must be TRUE or FALSE.')
        value=value.upper()
    if target=='Status' and value.lower() not in ('active','draft','archived'):raise MappingError('Status must be draft, active or archived.')
    if target=='Status':value=value.lower()
    if target=='Continue selling when out of stock' and value.lower() not in ('deny','continue'):raise MappingError('Choose deny or continue for out-of-stock selling.')
    if target=='URL handle' and not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',value):raise MappingError('URL handle must use lowercase letters, numbers and hyphens.')
    if target in {'Product image URL','Variant image URL'} and value:
        try:
            url=urlsplit(value)
            valid=url.scheme=='https' and bool(url.hostname) and not url.username and not url.password
        except ValueError:valid=False
        if not valid:raise MappingError(target+' requires a public HTTPS image URL.')
    if target=='Image position' and value and (not value.isdigit() or int(value)<1):raise MappingError('Image position must be a positive whole number.')
    if target=='Weight unit for display' and value not in ('','g','kg','lb','oz'):raise MappingError('Choose g, kg, lb or oz for weight display.')
    if target=='Packed product dimension unit' and value not in ('','cm','in'):raise MappingError('Packed dimension unit must be cm or in.')
    limit={'SEO title':70,'SEO description':320,'Image alt text':512}.get(target)
    if limit and len(value)>limit:raise MappingError(target+' exceeds '+str(limit)+' characters.')
    return value


def build_csv(products,vendor,columns,rules):
    fields=FIELDS|specification_fields(products)
    validate_rules(columns,rules,fields)
    if not products:raise MappingError('No published products are available.')
    if len(products)>10000:raise MappingError('Export limit is 10,000 products.')
    unresolved=[p.source_key for p in products if p.attributes.get('dropdowns')]
    if unresolved:raise MappingError('Variant SKUs/combinations need verification for: '+', '.join(unresolved[:10])+('.' if len(unresolved)<=10 else ' and more.'))
    chosen={r['target']:r for r in rules}
    names=[c['name'] for c in columns if c['name'] in chosen]
    rows=[];handles=set();skus=set();image_count=0;size=0
    for product in products:
        values,images=normalized(product,vendor)
        title_rule=chosen['Title']
        title=values.get(title_rule['source'][1],'') if 'source' in title_rule else title_rule.get('value','')
        values['auto.handle']=title_handle(title)
        gallery=images if chosen.get('Product image URL',{}).get('source')==['product','image_url'] else []
        if len(gallery)>250:raise MappingError('Product '+product.source_key+' exceeds 250 images.')
        gallery=gallery or [None]
        positions=set()
        for index,image in enumerate(gallery):
            values.update(image_url=image['url'] if image else '',image_position=str(index+1) if image else '',
                          image_alt=(image.get('alt') or values['name']) if image else '')
            row={}
            for name in names:
                if index and name not in IMAGE_TARGETS|{'URL handle'}:row[name]='';continue
                rule=chosen[name]
                value=values.get(rule['source'][1],'') if 'source' in rule else rule.get('value','')
                row[name]=cell_value(name,value)
            if not index:
                missing=[name for name in REQUIRED if not row.get(name)]
                if missing:raise MappingError('Product '+product.source_key+' is missing: '+', '.join(sorted(missing)))
                if row['URL handle'] in handles or row['SKU'] in skus:raise MappingError('Mapped handles and SKUs must be unique across products.')
                handles.add(row['URL handle']);skus.add(row['SKU'])
                if row.get('Compare-at price') and Decimal(row['Compare-at price'])<=Decimal(row['Price']):raise MappingError('Compare-at price must exceed Price.')
                dimensions=[row.get(key,'') for key in ('Packed product length','Packed product width','Packed product height','Packed product dimension unit')]
                if any(dimensions) and not all(dimensions):raise MappingError('Product '+product.source_key+' needs all four packed dimension values.')
                if row.get('Option1 name')!='Title' or row.get('Option1 value')!='Default Title':
                    raise MappingError('Simple products require Title / Default Title options until variant data is verified.')
            if image and row.get('Image position'):
                if row['Image position'] in positions:raise MappingError('Image positions must be unique within product '+product.source_key+'.')
                positions.add(row['Image position'])
            rows.append(row)
            size+=sum(len(value.encode('utf-8')) for value in row.values())
            if size>40*1024*1024:raise MappingError('Export content exceeds 40 MB before CSV encoding.')
            if image:image_count+=1
            if len(rows)>100000:raise MappingError('Export limit is 100,000 rows.')
    content=csv_file(names,rows,lineterminator='\n')
    if len(content)>50*1024*1024:raise MappingError('Export exceeds 50 MB.')
    return content,{'products':len(products),'rows':len(rows),'images':image_count}
