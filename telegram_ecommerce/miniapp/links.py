"""Only publish a Mini App button when an HTTPS origin is configured."""
from os import environ
from urllib.parse import urlencode, urlsplit


def mini_app_url(**params):
    url = environ.get('MINI_APP_URL', '').strip().rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ''
    return url + ('/?' + urlencode({k: v for k, v in params.items() if v}) if any(params.values()) else '/')
