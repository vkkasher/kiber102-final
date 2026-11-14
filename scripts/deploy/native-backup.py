"""Consistent SQLite + environment backup of the dedicated temporary deployment."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import os

base = Path('/opt/kiber102')
if base.resolve() != base or base.is_symlink() or not (base / 'INSTALLED').is_file():
    raise SystemExit('Expected isolated /opt/kiber102 installation')
os.umask(0o077)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
target = base / 'backups' / f'kiber102-{stamp}.tar.gz'
with tempfile.TemporaryDirectory(dir=base / 'backups') as temporary:
    stage = Path(temporary)
    for name in ['catalog.db', 'bot.sqlite']:
        source = base / 'app/.local' / name
        if source.is_file():
            with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(stage / name)) as backup_db:
                source_db.backup(backup_db)
                assert backup_db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    shutil.copyfile(base / 'app/.env', stage / '.env')
    with tarfile.open(target, 'x:gz') as archive:
        for path in stage.iterdir():
            archive.add(path, arcname=path.name)
        archive.add(base / 'app/.local/sources', arcname='sources')
        archive.add(base / 'deployment.json', arcname='deployment.json')
print(target)
