"""Product representation: detail-only fields, rich blocks, distinct gallery items."""
from html import escape
import re
from urllib.parse import urljoin,urlsplit
from commerce_hub.vendors.shiloh_tack.html import Node,tree,describe_detail,first_class,field


def inline_text(node):
    if isinstance(node,str):return node
    if node.tag in ('script','style','ul','ol','table'):return ''
    if node.tag=='br':return '\n'
    return ''.join(inline_text(child) for child in node.children)


def blocks(node):
    result=[];pending=[]
    def flush():
        text=' '.join(''.join(pending).split());pending.clear()
        if text:result.append({'type':'paragraph','text':text})
    def emit(child):
        if isinstance(child,str):pending.append(child);return
        if child.tag in ('script','style','iframe','input','button'):return
        if child.tag in ('ul','ol'):
            flush();items=[]
            for li in child.children:
                if not isinstance(li,Node) or li.tag!='li':continue
                nested=[block for item in li.children if isinstance(item,Node) and item.tag in ('ul','ol','table') for block in blocks(NodeWrapper(item))]
                items.append({'text':' '.join(inline_text(li).split()),'children':nested})
            result.append({'type':'list','ordered':child.tag=='ol','start':child.attrs.get('start','1'),'items':items,'html':child.html()});return
        if child.tag=='table':
            flush();rows=[]
            for row in child.nodes():
                if row.tag!='tr':continue
                nearest=row.parent
                while nearest is not None and nearest.tag!='table':nearest=nearest.parent
                if nearest is not child:continue
                cells=[{'text':cell.text(),'html':cell.html(),'header':cell.tag=='th',
                        'rowspan':cell.attrs.get('rowspan','1'),'colspan':cell.attrs.get('colspan','1')}
                       for cell in row.children if isinstance(cell,Node) and cell.tag in ('td','th')]
                rows.append({'cells':cells})
            pairs=[{'label':row['cells'][0]['text'],'value':row['cells'][1]['text']} for row in rows
                   if len(row['cells'])==2 and not all(cell['header'] for cell in row['cells'])]
            result.append({'type':'table','rows':rows,'label_value_pairs':pairs,'html':child.html()});return
        if child.tag=='p' or re.fullmatch(r'h[1-6]',child.tag):
            flush()
            if any(n.tag in ('ul','ol','table') for n in child.children if isinstance(n,Node)):
                for inner in child.children:emit(inner)
                flush()
            elif child.text():result.append({'type':'heading' if child.tag.startswith('h') else 'paragraph','text':child.text(),'html':child.html()})
            return
        if child.tag in ('div','section','article','dl','dt','dd'):
            flush()
            for inner in child.children:emit(inner)
            flush();return
        if child.tag=='br':pending.append('\n');return
        pending.append(inline_text(child))
    for child in node.children:emit(child)
    flush()
    return result


class NodeWrapper:
    def __init__(self,node):self.children=[node]


def gallery(product):
    def image_url(value):
        parsed=urlsplit(urljoin('https://www.shilohtack.com/',value or ''))
        if parsed.scheme!='https' or parsed.hostname!='www.shilohtack.com' or not parsed.path.startswith('/prodimages/'):return None
        return 'https://www.shilohtack.com'+parsed.path
    nodes=list(product.nodes());items=[];variants={}
    def add(url,alt,source):
        if url and not any(item['url']==url for item in items):
            items.append({'position':len(items)+1,'url':url,'alt':alt or '', 'source':source})
    for anchor in nodes:
        if anchor.tag!='a':continue
        original=image_url(anchor.attrs.get('href'))
        if not original:continue
        alt=next((n.attrs.get('alt','') for n in anchor.nodes() if n.tag=='img'),'')
        add(original,alt,'detail_gallery_original')
        for image in anchor.nodes():
            if image.tag!='img':continue
            for value in (image.attrs.get('src'),image.attrs.get('data-src')):
                if image_url(value):variants[image_url(value)]=original
            for value in (image.attrs.get('srcset') or '').split(','):
                if value.strip() and image_url(value.strip().split()[0]):variants[image_url(value.strip().split()[0])]=original
    for image in nodes:
        if image.tag!='img':continue
        candidate=image_url(image.attrs.get('data-src')) or image_url(image.attrs.get('src'))
        if candidate not in variants:add(candidate,image.attrs.get('alt',''),'detail_image_no_original_link')
    return items,variants


def detail_record(html,path):
    parsed=tree(html);product=first_class(parsed,'proddetail')
    if product is None:raise ValueError('Detail product container missing')
    record=describe_detail(html,{'id':None,'detail_path':path})
    record.pop('listing_id',None)
    if not record.get('id'):raise ValueError('Detail product identifier missing')
    for tab in record['tabs']:
        fragment=tree(tab['html']);tab['blocks']=blocks(fragment)
    record['images'],record['display_to_original_image_map']=gallery(product)
    record['category_path']=field(product,'catnavigation')
    levels=[value.strip() for value in re.split(r'\s*\u00bb\s*',record['category_path'] or '') if value.strip()]
    if levels and levels[0].casefold()=='home':levels=levels[1:]
    record['category_levels']=levels
    nodes=list(product.nodes())
    for dropdown in record['dropdowns']:
        select=next((node for node in nodes if node.tag=='select' and node.attrs.get('id')==dropdown['id']),None)
        label=None;source=None
        if select is not None:
            explicit=next((node for node in nodes if node.tag=='label' and node.attrs.get('for')==select.attrs.get('id') and node.text()),None)
            if explicit:label,source=explicit.text(),'label_for'
            elif select.attrs.get('aria-label'):label,source=select.attrs['aria-label'],'aria_label'
            elif select.attrs.get('aria-labelledby'):
                ids=select.attrs['aria-labelledby'].split()
                texts=[node.text() for name in ids for node in nodes if node.attrs.get('id')==name]
                if texts:label,source=' '.join(texts),'aria_labelledby'
            if label is None:
                parent=select.parent
                while parent is not None:
                    if 'optioncontainer' in parent.classes or 'detailoptioncontainer' in parent.classes:
                        selects=[node for node in parent.nodes() if node.tag=='select']
                        text=next((node for node in parent.nodes() if node.tag=='label' and node.text()),None)
                        if len(selects)==1 and text:label,source=text.text(),'nearest_option_container_label'
                        break
                    parent=parent.parent
        dropdown['label']=label;dropdown['label_source']=source
        for option in dropdown['options']:option['is_placeholder']=option['value']==''
    record['flags']=[value for value in ('onsale','assorted') if value in product.classes]
    return record
