"""Private Supabase object transport. No public URLs or project-wide service keys."""
from urllib.parse import quote
import httpx


class StorageUnavailable(RuntimeError):
    pass


def request(method, origin, api_key, bearer, path, **kwargs):
    if not origin or not api_key or not bearer:
        raise StorageUnavailable('storage_not_configured')
    try:
        response=httpx.request(method,origin.rstrip('/')+'/storage/v1/'+path,
            headers={'apikey':api_key,'Authorization':'Bearer '+bearer,**kwargs.pop('headers',{})},
            timeout=30,**kwargs)
        if not response.is_success:
            raise StorageUnavailable('storage_request_failed')
        return response
    except httpx.HTTPError:
        raise StorageUnavailable('storage_request_failed') from None


def upload(settings, path, content, content_type):
    if len(content)>50*1024*1024:
        raise StorageUnavailable('artifact_too_large')
    request('POST',settings.get('SUPABASE_URL'),settings.get('SUPABASE_KEY'),settings.get('COMMERCE_STORAGE_TOKEN'),
        'object/commerce-artifacts/'+quote(path,safe='/'),content=content,
        headers={'Content-Type':content_type,'x-upsert':'false'})


def download(origin,key,bearer,path):
    return request('GET',origin,key,bearer,'object/authenticated/commerce-artifacts/'+quote(path,safe='/')).content
