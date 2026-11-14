"""Create initial local configuration with random secrets, preserving existing values."""
import argparse
from pathlib import Path
import secrets

from dotenv import dotenv_values, set_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true')
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / '.env'
    values = dotenv_values(destination, encoding='utf-8-sig') if destination.exists() else {}
    destination.touch(exist_ok=True)
    for key in ['ADMIN_PASSWORD', 'ADMIN_SESSION_SECRET', 'POSTGRES_PASSWORD', 'TRUSTED_PROXY_SECRET']:
        if not values.get(key) or values[key].startswith('replace-'):
            set_key(destination, key, secrets.token_hex(32), quote_mode='never')
    defaults = {
        'SOURCE_ALLOWED_HOSTS': 'www.bashopera.ru,bashopera.ru,rusdram.ru,www.culture.ru,culture.ru,bashgf.ru,api.timepad.ru,pro.culture.ru,rusdramteatr.ru,ufa.kassy.ru,sterlitamak.kassy.ru',
        'API_URL': 'http://api:8000' if options.docker else 'http://127.0.0.1:8000',
        'API_INTERNAL_URL': 'http://api:8000' if options.docker else 'http://127.0.0.1:8000',
        'WEB_URL': 'http://localhost:3000',
    }
    if not options.docker:
        (root / '.local').mkdir(exist_ok=True)
        defaults['DATABASE_URL'] = 'sqlite:///' + (root / '.local/catalog.db').as_posix()
    for key, value in defaults.items():
        if not values.get(key):
            set_key(destination, key, value, quote_mode='never')
    print('Configuration ready in .env; secrets were not printed.')


if __name__ == '__main__':
    main()
