from functools import wraps
from io import BytesIO
import json

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import check_csrf, login_required
from app.services import Forbidden, InvalidDetails, NotFound
from app.commerce import services
from app.commerce import export_services
from commerce_hub.export_files import headers
from commerce_hub.export_mapping import MappingError
from commerce_hub.artifact_storage import StorageUnavailable, download

commerce=Blueprint('commerce',__name__,url_prefix='/portal')


def protected(view):
    @wraps(view)
    def wrapper(*args,**kwargs):
        if not current_app.config.get('COMMERCE_HUB_ENABLED'):
            abort(404)
        return view(*args,**kwargs)
    return login_required(wrapper)


@commerce.errorhandler(Forbidden)
def denied(_error):
    return 'Access denied.',403


@commerce.errorhandler(NotFound)
def missing(_error):
    return 'Not found.',404


@commerce.errorhandler(SQLAlchemyError)
@commerce.errorhandler(StorageUnavailable)
def unavailable(error):
    current_app.logger.warning('Commerce Hub unavailable (%s).',type(error).__name__)
    return render_template('portal/commerce/unavailable.html',portal_user=g.portal_user),503


def render(name,**kwargs):
    return render_template('portal/commerce/'+name,portal_user=g.portal_user,features=services.FEATURES,**kwargs)


@commerce.get('/commerce')
@protected
def index():
    rows=services.registry(g.portal_auth_user_id)
    if not g.portal_user['is_super_admin'] and len(rows)==1:
        return redirect(url_for('commerce.home',client_id=rows[0]['id']))
    query=request.args.get('q','').strip()[:200]
    feature=request.args.get('feature','')
    if feature not in ('catalog','inventory'):
        feature=''
    all_clients=[row for row in rows if row['enabled']]
    clients=[row for row in all_clients if query.casefold() in row['name'].casefold()
             and (not feature or feature in row['features'])]
    available=[row for row in rows if row['client_active'] and not row['enabled']] if g.portal_user['is_super_admin'] else []
    return render('index.html',clients=clients,all_clients=all_clients,available_clients=available,query=query,
                  selected_feature=feature,feature_labels=services.FEATURE_LABELS)


@commerce.post('/commerce/access/<uuid:client_id>')
@protected
def access(client_id):
    check_csrf()
    try:
        services.set_access(g.portal_auth_user_id,str(client_id),request.form.get('enabled')=='on',request.form.getlist('features'))
        flash('Commerce Hub access updated.','success')
    except InvalidDetails as exc:
        flash(str(exc),'error')
    return redirect(url_for('commerce.index'))


@commerce.get('/clients/<uuid:client_id>/commerce')
@protected
def home(client_id):
    tab=request.args.get('tab')
    if tab not in ('connections','jobs','exports'):
        tab='jobs' if any(key in request.args for key in ('job_search','job_period','job_page')) else 'connections'
    data=services.overview(g.portal_auth_user_id,str(client_id),request.args)
    destinations=export_services.overview(g.portal_auth_user_id,str(client_id)) if tab=='exports' else None
    return render('home.html',data=data,options=services.export_options(g.portal_auth_user_id,str(client_id)),destinations=destinations,client_id=client_id,active_tab=tab)


@commerce.route('/clients/<uuid:client_id>/commerce/export-destinations/<uuid:destination_id>/mapping',methods=['GET','POST'])
@protected
def export_mapping(client_id,destination_id):
    if request.method=='POST':check_csrf()
    try:
        data=export_services.editor(g.portal_auth_user_id,str(client_id),str(destination_id),
                                    request.form if request.method=='POST' else None,save=request.method=='POST',
                                    sample_product_id=(request.form if request.method=='POST' else request.args).get('sample_product_id'))
    except IntegrityError:
        flash('This mapping changed in another window. Reload and try again.','error')
        return redirect(url_for('commerce.export_mapping',client_id=client_id,destination_id=destination_id))
    except InvalidDetails as exc:
        flash(str(exc),'error')
        return redirect(url_for('commerce.home',client_id=client_id,tab='exports'))
    if request.method=='POST' and not data['error']:
        flash('Export mapping saved.','success')
        return redirect(url_for('commerce.home',client_id=client_id,tab='exports'))
    return render('mapping.html',data=data,client_id=client_id,active_tab='exports'),422 if data['error'] else 200


@commerce.get('/clients/<uuid:client_id>/commerce/export-destinations/<uuid:destination_id>/sample')
@protected
def export_mapping_sample(client_id,destination_id):
    try:sample=export_services.sample_product(g.portal_auth_user_id,str(client_id),str(destination_id),request.args.get('product_id'))
    except InvalidDetails:return 'Sample unavailable.',422
    response=current_app.make_response(render('mapping_sample.html',sample=sample))
    response.headers['Cache-Control']='no-store'
    return response


@commerce.post('/clients/<uuid:client_id>/commerce/export-destinations')
@protected
def add_export_destination(client_id):
    check_csrf()
    try:
        export_services.set_destination(g.portal_auth_user_id,str(client_id),request.form.get('vendor_id'),request.form.get('template_id'))
        flash('Export enabled.','success')
    except (InvalidDetails,IntegrityError):flash('Select a valid vendor and template.','error')
    return redirect(url_for('commerce.home',client_id=client_id,tab='exports'))


@commerce.post('/clients/<uuid:client_id>/commerce/export-destinations/<uuid:destination_id>/download')
@protected
def download_catalog(client_id,destination_id):
    check_csrf()
    try:content,filename=export_services.download(g.portal_auth_user_id,str(client_id),str(destination_id))
    except InvalidDetails as exc:
        flash(str(exc),'error')
        return redirect(url_for('commerce.home',client_id=client_id,tab='exports'))
    response=send_file(BytesIO(content),as_attachment=True,download_name=filename,mimetype='text/csv',max_age=0)
    response.headers['Cache-Control']='no-store, private'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


@commerce.get('/clients/<uuid:client_id>/commerce/jobs')
@protected
def job_history(client_id):
    data=services.job_history(g.portal_auth_user_id,str(client_id),request.args)
    response=current_app.make_response(render('jobs_table.html',data=data,client_id=client_id))
    response.headers['Cache-Control']='no-store'
    return response


@commerce.route('/clients/<uuid:client_id>/commerce/connections/<uuid:connection_id>',methods=['GET','POST'])
@protected
def connection(client_id,connection_id):
    if request.method=='POST':
        check_csrf()
    data=services.connection_form(g.portal_auth_user_id,str(client_id),str(connection_id))
    error=None
    username=data['connection']['username']
    portal_url=data['connection']['portal_url']
    if request.method=='POST':
        username=request.form.get('username','').strip()
        if data['internal']:
            portal_url=request.form.get('portal_url','').strip()
        try:
            services.save_connection(g.portal_auth_user_id,str(client_id),str(connection_id),username,
                password=request.form.get('password') or None,url=portal_url if data['internal'] else None)
            flash('Connection saved.','success')
            return redirect(url_for('commerce.home',client_id=client_id))
        except InvalidDetails as exc:
            error=str(exc)
    # Never pass request.form/password or a Vault reference to the template.
    return render('connection.html',data=data,client_id=client_id,username=username,portal_url=portal_url,error=error,active_tab='connections'),422 if error else 200


@commerce.post('/clients/<uuid:client_id>/commerce/connections/<uuid:connection_id>/run')
@protected
def run_connection(client_id,connection_id):
    check_csrf()
    tab='jobs'
    try:
        services.enqueue_connection(g.portal_auth_user_id,str(client_id),str(connection_id),request.form.get('kind'),
            catalog_mode=request.form.get('catalog_mode'),category_id=request.form.get('category_id',''),
            inventory_mode=request.form.get('inventory_mode'))
        flash('Job queued.','success')
    except InvalidDetails as exc:
        flash(str(exc),'error')
        tab='connections'
    except IntegrityError:
        flash('Check connection settings, or wait for its current job to finish.','error')
    return redirect(url_for('commerce.home',client_id=client_id,tab=tab))


@commerce.get('/clients/<uuid:client_id>/commerce/jobs/<uuid:job_id>')
@protected
def job(client_id,job_id):
    return jsonify(services.job_status(g.portal_auth_user_id,str(client_id),str(job_id)))


@commerce.get('/clients/<uuid:client_id>/commerce/jobs/<uuid:job_id>/view')
@protected
def job_detail(client_id,job_id):
    try:page=max(1,min(100000,int(request.args.get('page',1))))
    except ValueError:page=1
    data=services.job_detail(g.portal_auth_user_id,str(client_id),str(job_id),page)
    return render('results.html',data=data,client_id=client_id,page=page,active_tab='jobs')


@commerce.get('/clients/<uuid:client_id>/commerce/products/<uuid:product_id>')
@protected
def product_detail(client_id,product_id):
    product=services.product_detail(g.portal_auth_user_id,str(client_id),str(product_id))
    response=current_app.make_response(render('product_detail.html',product=product,client_id=client_id))
    response.headers['Cache-Control']='no-store'
    return response


@commerce.get('/clients/<uuid:client_id>/commerce/jobs/<uuid:job_id>/sample')
@protected
def connection_sample(client_id,job_id):
    return render('connection_sample.html',data=services.connection_sample(g.portal_auth_user_id,str(client_id),str(job_id)),
                  client_id=client_id,active_tab='jobs')


@commerce.get('/clients/<uuid:client_id>/commerce/jobs/<uuid:job_id>/inventory-preview')
@protected
def inventory_preview(client_id,job_id):
    return render('inventory_preview.html',data=services.inventory_preview(g.portal_auth_user_id,str(client_id),str(job_id)),
                  client_id=client_id,active_tab='jobs')


@commerce.post('/clients/<uuid:client_id>/commerce/jobs/<uuid:job_id>/cancel')
@protected
def cancel_job(client_id,job_id):
    check_csrf()
    try:
        services.cancel_job(g.portal_auth_user_id,str(client_id),str(job_id))
    except InvalidDetails as exc:
        flash(str(exc),'error')
    return redirect(url_for('commerce.home',client_id=client_id,tab='jobs'))


@commerce.get('/clients/<uuid:client_id>/commerce/results/<kind>')
@protected
def results(client_id,kind):
    try:
        page=max(1,min(100000,int(request.args.get('page',1))))
    except ValueError:
        page=1
    if kind not in ('catalog','inventory'):
        abort(404)
    data=services.results(g.portal_auth_user_id,str(client_id),kind,page,request.args.get('run_id'),request.args)
    return render('results.html',data=data,client_id=client_id,page=data.get('page',page),active_tab='products' if kind=='catalog' else 'inventory')


@commerce.post('/clients/<uuid:client_id>/commerce/jobs/<uuid:job_id>/resume')
@protected
def resume_job(client_id,job_id):
    check_csrf()
    try:
        services.resume_job(g.portal_auth_user_id,str(client_id),str(job_id))
        flash('Run queued to resume. Completed work will be preserved.','success')
    except (InvalidDetails,IntegrityError):
        flash('This run cannot be resumed now. Check the connection and active jobs.','error')
    return redirect(url_for('commerce.home',client_id=client_id,tab='jobs'))


@commerce.post('/clients/<uuid:client_id>/commerce/exports')
@protected
def export(client_id):
    check_csrf()
    try:
        services.enqueue_export(g.portal_auth_user_id,str(client_id),request.form.get('kind'),request.form.get('catalog_job_id'),
            request.form.get('inventory_job_id') or None,request.form.get('mapping_id') or None,'csv' if request.form.get('kind')=='export_shopify' else 'xlsx')
        flash('Export queued.','success')
    except InvalidDetails as exc:
        flash(str(exc),'error')
    return redirect(url_for('commerce.home',client_id=client_id,tab='exports'))


@commerce.get('/clients/<uuid:client_id>/commerce/exports/<uuid:export_id>')
@protected
def download_export(client_id,export_id):
    path,fmt=services.download(g.portal_auth_user_id,str(client_id),str(export_id))
    content=download(current_app.config['SUPABASE_URL'],current_app.config['SUPABASE_KEY'],g.portal_access_token,path)
    return send_file(BytesIO(content),as_attachment=True,download_name='commerce-export.'+fmt,
        mimetype='text/csv' if fmt=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',max_age=0)


@commerce.route('/commerce/configuration',methods=['GET','POST'])
@protected
def configuration():
    data=services.configuration(g.portal_auth_user_id)
    if request.method=='POST':
        check_csrf()
        try:
            if request.form.get('action')=='template':
                file=request.files.get('template')
                if not file:
                    raise InvalidDetails('Select a template file.')
                columns,digest=headers(file.stream.read(2*1024*1024+1),file.filename or '')
                required={s.strip() for s in request.form.get('required_columns','').splitlines() if s.strip()}
                if required-set(c['name'] for c in columns):
                    raise InvalidDetails('Required columns must exist in the uploaded template.')
                for col in columns:
                    col['required']=col['name'] in required
                services.register_template(g.portal_auth_user_id,'shopify',request.form.get('version',''),columns,digest)
            elif request.form.get('action')=='mapping':
                raw=request.form.get('rules','')
                if len(raw)>100000:
                    raise InvalidDetails('Mapping configuration is too large.')
                services.register_mapping(g.portal_auth_user_id,request.form.get('client_id'),request.form.get('vendor_id'),
                    request.form.get('template_id'),int(request.form.get('version','0')),json.loads(raw))
            else:
                raise InvalidDetails('Select a configuration action.')
            flash('New immutable configuration version saved.','success')
        except (InvalidDetails,MappingError,ValueError,IntegrityError):
            flash('Check the template, version, required fields or mapping rules. No version was overwritten.','error')
        return redirect(url_for('commerce.configuration'))
    return render('configuration.html',data=data)
