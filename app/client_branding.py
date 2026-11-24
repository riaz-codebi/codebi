"""Validate and store client branding using the signed-in user's Storage access."""
from io import BytesIO
from urllib.parse import quote

import httpx
from flask import current_app, g
from PIL import Image

from app.services import InvalidDetails

BUCKET = 'client-branding'
MAX_LOGO_SIZE = 2 * 1024 * 1024


class BrandingUnavailable(RuntimeError):
    pass


def validate_logo(upload):
    if not upload or not upload.filename:
        return None
    raw = upload.stream.read(MAX_LOGO_SIZE + 1)
    if len(raw) > MAX_LOGO_SIZE:
        raise InvalidDetails('Upload a PNG no larger than 2 MB.', 'logo')
    try:
        with Image.open(BytesIO(raw), formats=['PNG']) as image:
            if max(image.size) > 2048 or getattr(image, 'is_animated', False):
                raise ValueError
            # Re-encode pixel data to discard metadata and appended content.
            pixels = Image.new('RGBA', image.size)
            pixels.paste(image.convert('RGBA'))
            output = BytesIO()
            pixels.save(output, format='PNG', optimize=True)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise InvalidDetails('Upload a valid PNG logo up to 2,048 pixels on either side.', 'logo') from None
    if output.tell() > MAX_LOGO_SIZE:
        raise InvalidDetails('The processed PNG exceeds 2 MB. Choose a smaller image.', 'logo')
    return output.getvalue()


def storage_request(method, path, **kwargs):
    origin = current_app.config['SUPABASE_URL']
    key = current_app.config['SUPABASE_KEY']
    if not origin or not key:
        raise BrandingUnavailable('Supabase Storage is not configured.')
    headers = {'apikey': key, 'Authorization': f'Bearer {g.portal_access_token}', **kwargs.pop('headers', {})}
    try:
        url = f"{origin.rstrip('/')}/storage/v1/{path}"
        return httpx.request(method, url, headers=headers, timeout=10.0, **kwargs)
    except httpx.RequestError:
        raise BrandingUnavailable('Supabase Storage is unavailable.') from None


def save_branding(client_id, logo):
    filename = 'logo.png' if logo is not None else '.emptyFolderPlaceholder'
    image_type = 'image/png' if logo is not None else 'application/octet-stream'
    response = storage_request('POST', f'object/{BUCKET}/{client_id}/brand/{filename}',
        files={'file': (filename, logo if logo is not None else b'', image_type)},
        headers={'x-upsert': 'true'})
    if not response.is_success:
        raise BrandingUnavailable(f'Storage upload failed ({response.status_code}).')


def download_logo(path):
    response = storage_request('GET', f'object/authenticated/{BUCKET}/{quote(path, safe="/")}')
    if response.status_code in (400, 403, 404):
        return None
    if not response.is_success:
        raise BrandingUnavailable(f'Storage download failed ({response.status_code}).')
    return response.content
