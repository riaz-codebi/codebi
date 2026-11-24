"""Make one Places lookup from the caller's network without displaying credentials."""
from pathlib import Path
import sys

from dotenv import dotenv_values
import httpx


def main():
    root = Path(__file__).resolve().parents[1]
    key = dotenv_values(root / '.env').get('GOOGLE_PLACES_API_KEY')
    if not key:
        print('GOOGLE_PLACES_API_KEY is missing or empty in the project .env.')
        return 1
    print('Key loaded from .env (value hidden). Sending one lookup request...')
    try:
        response = httpx.post(
            'https://places.googleapis.com/v1/places:autocomplete',
            headers={'X-Goog-Api-Key': key},
            json={'input': '1600 Pennsylvania Avenue NW Washington DC',
                  'includedRegionCodes': ['us', 'ca']},
            timeout=15,
        )
        if response.status_code == 403:
            print('HTTP status: 403')
            print('Full 403 response body (API key redacted):')
            print(response.text.replace(key, '[REDACTED]'))
            return 1
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Exception strings may contain request details; print only the type.
        print('Request failed:', type(exc).__name__)
        return 1
    print('HTTP status:', response.status_code)
    if response.is_success:
        print('Suggestions returned:', len(data.get('suggestions', [])))
        return 0
    error = data.get('error', {})
    print('Google status:', str(error.get('status', 'Unknown')).replace(key, '[REDACTED]'))
    print('Message:', str(error.get('message', 'No message')).replace(key, '[REDACTED]'))
    for detail in error.get('details', []):
        if detail.get('reason'):
            print('Reason:', str(detail['reason']).replace(key, '[REDACTED]'))
    return 1


if __name__ == '__main__':
    sys.exit(main())
