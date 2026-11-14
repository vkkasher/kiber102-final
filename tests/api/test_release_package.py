"""Delivery archives must separate runnable source from project credentials/data."""
import sqlite3
import subprocess
import zipfile

import pytest

from scripts.package_release import package_release
from scripts.restore_project_data import restore


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    subprocess.run(['git', 'init', str(root)], capture_output=True, check=True)
    (root/'README.md').write_text('TEST_DATA project', encoding='utf8')
    (root/'.env.example').write_text('LLM_API_KEY=\n', encoding='utf8')
    (root/'.env').write_text('ADMIN_PASSWORD=TEST_DATA-private-password\nDATABASE_URL=sqlite:///.local/catalog.db\n', encoding='utf8')
    (root/'.local').mkdir()
    with sqlite3.connect(root/'.local/catalog.db') as db:
        db.execute('create table preserved (value text)')
        db.execute("insert into preserved values ('TEST_DATA retained')")
    (root/'node_modules').mkdir()
    (root/'node_modules/ignored.js').write_text('unused', encoding='utf8')
    (root/'AGENTS.md').write_text('old instructions', encoding='utf8')
    (root/'.gitignore').write_text('.env\n.local/\nnode_modules/\n', encoding='utf8')
    return root


def test_source_archive_excludes_private_data_dependencies_and_agent_instructions(project, tmp_path):
    (project/'handoff').mkdir()
    (project/'handoff/production.env').write_text('ADMIN_PASSWORD=TEST_DATA-private-password\n', encoding='utf8')
    (project/'handoff/production-data.tar.gz').write_bytes(b'TEST_DATA private archive')
    report = package_release(project, tmp_path/'delivery')
    with zipfile.ZipFile(tmp_path/'delivery'/report['source']['file']) as archive:
        assert 'README.md' in archive.namelist() and '.env.example' in archive.namelist()
        assert not any(p.startswith(('.git/', '.local/', 'node_modules/', 'handoff/')) or p in {'.env','AGENTS.md'} for p in archive.namelist())
    assert report['private_data'] is None


def test_private_archive_roundtrips_database_and_configuration(project, tmp_path):
    report = package_release(project, tmp_path/'delivery', include_private_data=True)
    restored = tmp_path/'restored'
    restore(tmp_path/'delivery'/report['private_data']['file'], restored)
    assert 'TEST_DATA-private-password' in (restored/'.env').read_text()
    assert (restored/'.env.original').read_bytes() == (project/'.env').read_bytes()
    with sqlite3.connect(restored/'.local/catalog.db') as db:
        assert db.execute('select value from preserved').fetchone()[0] == 'TEST_DATA retained'


def test_packaging_rejects_output_inside_project(project):
    with pytest.raises(ValueError, match='outside'):
        package_release(project, project/'release')


def test_source_secret_leak_aborts_package(project, tmp_path):
    (project/'leaked.txt').write_text('TEST_DATA-private-password', encoding='utf8')
    with pytest.raises(ValueError, match='secret'):
        package_release(project, tmp_path/'delivery')
    assert not list((tmp_path/'delivery').glob('*.zip'))
