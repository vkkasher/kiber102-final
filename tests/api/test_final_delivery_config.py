"""A fresh installation must permit the enabled ticket enrichment sources."""
import importlib.util
from pathlib import Path
import sys

from dotenv import dotenv_values
from apps.api.app.security import validate_source_url


def test_new_local_configuration_allows_enabled_ticket_sources(tmp_path, monkeypatch):
    spec=importlib.util.spec_from_file_location('isolated_configuration',Path('scripts/configure.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,'__file__',str(tmp_path/'scripts/configure.py'))
    monkeypatch.setattr(sys,'argv',['configure.py'])
    module.main()
    settings=dotenv_values(tmp_path/'.env')
    monkeypatch.setenv('SOURCE_ALLOWED_HOSTS',settings['SOURCE_ALLOWED_HOSTS'])
    monkeypatch.delenv('SOURCE_ALLOWLIST',raising=False)
    for host in ['ufa.kassy.ru','sterlitamak.kassy.ru']:
        validate_source_url(f'https://{host}/events/teatr/2-1/')
