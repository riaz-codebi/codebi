"""Bounded HTML parsing for Shiloh catalog listings and product details."""
from collections import Counter
from html import escape, unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit, urljoin, parse_qs, unquote

VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


class Node:
    def __init__(self,tag='',attrs=None,parent=None):
        self.tag=tag;self.attrs=dict(attrs or []);self.parent=parent;self.children=[]
    @property
    def classes(self):return set((self.attrs.get('class') or '').split())
    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child,Node):yield from child.nodes()
    def text(self):
        if self.tag in ('script','style'):return ''
        return ' '.join(' '.join(c.text() if isinstance(c,Node) else c for c in self.children).split())
    def sanitized(self):
        if self.tag in ('script','style','iframe') or (self.tag=='input' and (self.attrs.get('type') or '').lower() in ('hidden','password')):return None
        allowed={'id','class','role','href','src','alt','title','name','type','value','selected','disabled','checked','aria-controls','aria-labelledby','style','srcset','data-src','data-target','data-toggle','for','aria-label','rowspan','colspan','start'}
        attrs={key:value for key,value in self.attrs.items() if key in allowed and not re.search(r'password|token|hash|cookie|csrf|session',key,re.I)}
        if self.tag=='input' and re.search(r'email|pass|auth|login|cookie|token|csrf|session',attrs.get('name') or '',re.I):return None
        children=[]
        for child in self.children:
            clean=child.sanitized() if isinstance(child,Node) else child
            if clean is not None:children.append(clean)
        return {'tag':self.tag,'attrs':attrs,'children':children}
    def html(self):
        clean=self.sanitized()
        def render(value):
            if isinstance(value,str):return escape(value)
            attrs=''.join(' '+key+'="'+escape(val or '',quote=True)+'"' for key,val in value['attrs'].items())
            if value['tag'] in VOID:return '<'+value['tag']+attrs+'>'
            return '<'+value['tag']+attrs+'>'+''.join(render(child) for child in value['children'])+'</'+value['tag']+'>'
        return render(clean) if clean else ''


class Tree(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.root=Node('root');self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs,self.stack[-1]);self.stack[-1].children.append(node)
        if tag not in VOID:self.stack.append(node)
    def handle_data(self,data):self.stack[-1].children.append(data)
    def handle_endtag(self,tag):
        index=next((i for i in range(len(self.stack)-1,0,-1) if self.stack[i].tag==tag),None)
        if index is not None:del self.stack[index:]


def tree(text):parser=Tree();parser.feed(text);return parser.root
def first_class(root,name):return next((n for n in root.nodes() if name in n.classes),None)
def field(root,name):
    node=first_class(root,name);return node.text() if node else None
def path_of(value):
    if not isinstance(value,str) or len(value)>1500:return None
    parsed=urlsplit(urljoin('https://www.shilohtack.com/',unescape(value or '')))
    if parsed.scheme!='https' or parsed.hostname!='www.shilohtack.com' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment:return None
    decoded=unquote(parsed.path)
    if '..' in decoded or '\\' in decoded or any(ord(c)<32 for c in decoded) or decoded.startswith('//'):return None
    if decoded in ('/TEST','/TESTDB') or decoded.startswith(('/TEST/','/TESTDB/')):return None
    if re.match(r'/(?:cart|clientlogin|checkout|vsadmin|admin|account|logout|signout|login|register)(?:[./]|$)',decoded,re.I):return None
    if parsed.path!='/proddetail.php' and ('/' in decoded[1:] or '.php' in decoded.lower()):return None
    query=parse_qs(parsed.query,keep_blank_values=True)
    if query and (parsed.path!='/proddetail.php' or set(query)-{'prod'}):return None
    return parsed.path+('?' + parsed.query if parsed.query else '')


def pagination(root):
    links=[];regions=[]
    for node in root.nodes():
        marker=(node.attrs.get('class') or '')+' '+(node.attrs.get('id') or '')
        if re.search(r'pagenum|pagination|pagenav',marker,re.I):
            regions.append({'tag':node.tag,'class':node.attrs.get('class'),'text':node.text()[:500]})
        if node.tag=='a' and node.attrs.get('href'):
            parsed=urlsplit(urljoin('https://www.shilohtack.com/',node.attrs['href']))
            query=parse_qs(parsed.query)
            if parsed.hostname=='www.shilohtack.com' and parsed.path=='/search.php' and query.get('pg',[''])[0].isdigit():
                links.append({'page':int(query['pg'][0]),'text':node.text(),'title':node.attrs.get('title','')})
    last=[link['page'] for link in links if re.search(r'last|final',link['text']+' '+link['title'],re.I)]
    return {'regions':regions[:15],'links':links,'explicit_last_page':max(last) if last else None}


def product_cards(root):
    result=[]
    for card in root.nodes():
        if 'product' not in card.classes:continue
        name=first_class(card,'prodname')
        link=next((path_of(n.attrs['href']) for n in (name.nodes() if name else []) if n.tag=='a' and n.attrs.get('href') and path_of(n.attrs['href'])),None)
        if not link:
            link=next((path_of(n.attrs['href']) for n in card.nodes() if n.tag=='a' and n.attrs.get('href') and path_of(n.attrs['href'])),None)
        result.append({'id':field(card,'prodcustom2'),'name':name.text() if name else None,'price':field(card,'price'),'detail_path':link,'flags':sorted(card.classes-{'product'})})
    return result


def describe_detail(html,card):
    root=tree(html);product=first_class(root,'proddetail')
    if product is None:return {'id':card['id'],'path':card['detail_path'],'error':'product_container_not_found'}
    nodes=list(product.nodes());controls=[];panels=[]
    for node in nodes:
        label=node.text();attrs=node.attrs
        panel_marker=attrs.get('role')=='tabpanel' or re.search(r'tab[-_]?content|tab[-_]?pane',attrs.get('class') or '',re.I)
        if not panel_marker and (attrs.get('role')=='tab' or re.search(r'tab',attrs.get('class') or '',re.I)) and label and len(label)<=90:
            if node.tag in ('a','button','span','li','div') and not any(child.attrs.get('role')=='tab' for child in node.children if isinstance(child,Node)):
                controls.append({'label':label,'id':attrs.get('id'),'target':attrs.get('aria-controls') or (attrs.get('href','')[1:] if attrs.get('href','').startswith('#') else None),'class':attrs.get('class')})
        if panel_marker:
            panels.append(node)
    panels=[panel for panel in panels if not any(ancestor is other for other in panels for ancestor in ancestors(panel))]
    tabs=[]
    for index,panel in enumerate(panels):
        control=next((item for item in controls if item['target'] and item['target']==panel.attrs.get('id')),None)
        if control is None and len(controls)==len(panels):control=controls[index]
        label=control['label'] if control else panel.attrs.get('aria-label') or panel.attrs.get('id') or 'Unmapped tab '+str(index+1)
        tables=[]
        for table in panel.nodes():
            if table.tag=='table':
                tables.append([[cell.text() for cell in row.children if isinstance(cell,Node) and cell.tag in ('td','th')] for row in table.nodes() if row.tag=='tr'])
        tabs.append({'label':label,'panel_id':panel.attrs.get('id'),'text':panel.text(),'html':panel.html(),'tables':tables,'label_match':'target' if control and control['target'] else 'position_or_unmapped'})
    desc=first_class(product,'detaildescription')
    images=[]
    def add_image(value,source):
        absolute=urljoin('https://www.shilohtack.com/',unescape(value or ''))
        parsed=urlsplit(absolute)
        if parsed.scheme!='https' or parsed.hostname!='www.shilohtack.com' or not parsed.path.startswith('/prodimages/'):return
        url='https://www.shilohtack.com'+parsed.path
        existing=next((item for item in images if item['url']==url),None)
        if existing:
            if source not in existing['sources']:existing['sources'].append(source)
        else:images.append({'url':url,'sources':[source]})
    for node in nodes:
        if node.tag=='img':
            add_image(node.attrs.get('src'),'img src');add_image(node.attrs.get('data-src'),'img data-src')
            for part in (node.attrs.get('srcset') or '').split(','):
                if part.strip():add_image(part.strip().split()[0],'srcset')
        if node.tag=='a':add_image(node.attrs.get('href'),'gallery link')
        if node.tag=='script':
            code=''.join(child for child in node.children if isinstance(child,str))
            for match in re.finditer(r'''["']((?:https://www\.shilohtack\.com/|/)?prodimages/[^"'\r\n]+)["']''',code):add_image(match.group(1),'product image script')
    dropdowns=[]
    for node in nodes:
        if node.tag!='select':continue
        dropdowns.append({'name':node.attrs.get('name'),'id':node.attrs.get('id'),'classes':node.attrs.get('class'),
            'options':[{'label':option.text(),'value':option.attrs.get('value'),'disabled':'disabled' in option.attrs,'selected':'selected' in option.attrs} for option in node.nodes() if option.tag=='option']})
    prices=[]
    for node in nodes:
        if re.search(r'price|saving|discount',node.attrs.get('class') or '',re.I) and node.text():
            item={'class':node.attrs.get('class'),'text':node.text()}
            if item not in prices:prices.append(item)
    quantity_pricing=[]
    for row in nodes:
        if 'detailqprow' in row.classes:
            quantity_pricing.append({'quantity':field(row,'detailqpquant'),'price':field(row,'detailqpprice')})
    displayed_id=field(product,'detailcustom2')
    product_id=re.sub(r'^Product\s+ID\s*:\s*','',displayed_id,flags=re.I) if displayed_id else card['id']
    return {'id':product_id,'displayed_product_id':displayed_id,'listing_id':card['id'],'name':field(product,'detailname'),
        'path':card['detail_path'],'price_fields':prices,'tabs':tabs,'tab_controls':controls,
        'quantity_pricing':quantity_pricing,'stock_notice':field(product,'detailoutofstock'),
        'description_text':desc.text() if desc else None,'description_html':desc.html() if desc else None,
        'images':images,'dropdowns':dropdowns,'classes':Counter(c for node in nodes for c in node.classes).most_common(35),
        'product_dom':product.sanitized()}


def ancestors(node):
    parent=node.parent
    while parent is not None:yield parent;parent=parent.parent
