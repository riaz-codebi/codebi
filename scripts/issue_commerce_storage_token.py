"""Optional owner-run bootstrap for projects accepting legacy HS256 JWTs.

Stores a verified bucket-scoped worker token, never the signing secret, in ignored .env.
Do not change project signing keys to use this helper. It is not needed for connectors
or database setup. Run interactively from the owner's terminal, never paste keys in chat.
"""
import argparse
import base64
from getpass import getpass
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys
import time

from dotenv import dotenv_values,set_key
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,default=7,choices=range(1,31),metavar='1..30')
    args=parser.parse_args()
    if subprocess.run(['git','check-ignore','--quiet','.env'],cwd=ROOT).returncode!=0:
        parser.error('.env must be ignored.')
    values=dotenv_values(ROOT/'.env')
    if not values.get('SUPABASE_URL') or not values.get('SUPABASE_KEY'):
        parser.error('Supabase configuration is missing.')
    secret=getpass('Existing Supabase legacy HS256 signing secret (not saved): ')
    if len(secret)<32:
        parser.error('Expected the existing project signing secret, not an API or publishable key.')
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).rstrip(b'=')
    issued=int(time.time())
    message=encode({'alg':'HS256','typ':'JWT'})+b'.'+encode({'role':'commerce_worker','iss':'supabase','iat':issued,'exp':issued+args.days*86400})
    token=(message+b'.'+base64.urlsafe_b64encode(hmac.new(secret.encode(),message,hashlib.sha256).digest()).rstrip(b'=')).decode()
    try:
        response=httpx.post(values['SUPABASE_URL'].rstrip('/')+'/storage/v1/object/list/commerce-artifacts',
            headers={'apikey':values['SUPABASE_KEY'],'Authorization':'Bearer '+token},
            json={'prefix':'','limit':1},timeout=15)
        if not response.is_success:
            print('Token was not accepted. Nothing saved; do not change project signing keys. Review scoped Storage authentication.')
            return 1
        set_key(str(ROOT/'.env'),'COMMERCE_STORAGE_TOKEN',token)
        print(f'Verified bucket-scoped worker token saved to ignored .env. Expires in {args.days} days; renew before then.')
        print('Signing secret was not saved or printed. Restart the worker to load the new token.')
        return 0
    except httpx.HTTPError:
        print('Storage verification failed. No token or signing secret was saved.')
        return 1


if __name__=='__main__':
    sys.exit(main())
