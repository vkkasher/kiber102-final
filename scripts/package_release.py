"""Package source and, only by explicit opt-in, private SQLite project data."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import zipfile

from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_info(path, name):
    return {'path':name, 'bytes':path.stat().st_size, 'sha256':digest(path)}


def source_files(root):
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=root, capture_output=True, check=True)
    excluded = {'.git', '.local', '.venv', 'node_modules', '__pycache__', '.pytest_cache',
                '.ruff_cache', 'test-results', 'playwright-report', '.vscode', '.recording',
                '.impeccable', '.taste-skill', '.playwright-cli', 'handoff'}
    for name in sorted(set(result.stdout.decode('utf8').strip('\0').split('\0'))):
        path = root/name
        if not name or not path.is_file():
            continue
        relative = Path(name)
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Source link points outside project')
        if (any(p in excluded or p.startswith('.next') for p in relative.parts)
                or relative.name in {'AGENTS.md', 'CLAUDE.md', 'GEMINI.md'}
                or (relative.name.startswith('.env') and relative.name != '.env.example')
                or relative.suffix in {'.db', '.sqlite', '.pyc', '.tsbuildinfo', '.log'}):
            continue
        yield name, path


def sqlite_snapshot(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True)) as original:
        with closing(sqlite3.connect(target)) as backup:
            original.backup(backup)


def private_archive(root, output, settings):
    url = make_url(settings.get('DATABASE_URL', ''))
    if not url.drivername.startswith('sqlite') or not url.database or url.database == ':memory:':
        raise ValueError('Private packaging requires a file-backed local SQLite database')
    database = (root/url.database).resolve()
    if not database.is_relative_to(root) or not database.is_file():
        raise ValueError('Private database must exist inside the project')
    with tempfile.TemporaryDirectory(prefix='kiber102-data-', dir=output) as temporary:
        stage = Path(temporary)
        shutil.copy2(root/'.env', stage/'.env.original')
        portable = dict(settings, DATABASE_URL='sqlite:///.local/catalog.db')
        (stage/'.env').write_text(''.join(f'{key}={json.dumps(value, ensure_ascii=False)}\n'
                                        for key,value in portable.items() if value is not None), encoding='utf8')
        sqlite_snapshot(database, stage/'.local/catalog.db')
        bot = (root/settings.get('BOT_STATE_PATH', '.local/bot.sqlite')).resolve()
        if not bot.is_relative_to(root):
            raise ValueError('Bot state must stay inside the project')
        if bot.is_file():
            sqlite_snapshot(bot, stage/'.local/bot.sqlite')
        raw = root/'.local/sources'
        if raw.is_dir():
            for path in raw.rglob('*'):
                if path.is_file():
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        raise ValueError('Source evidence link points outside project')
                    target = stage/path.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
        files = [file_info(p, p.relative_to(stage).as_posix()) for p in sorted(stage.rglob('*')) if p.is_file()]
        (stage/'PROJECT_DATA_MANIFEST.json').write_text(json.dumps({'files':files}, ensure_ascii=False, indent=2), encoding='utf8')
        destination = output/'kiber102-private-data.tar.gz'
        with tarfile.open(destination, 'x:gz') as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(stage).as_posix(), recursive=False)
    return {'file':destination.name, 'bytes':destination.stat().st_size, 'sha256':digest(destination), 'files':len(files)}


def package_release(root, output, include_private_data=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError('Delivery output must be outside the project')
    output.mkdir(parents=True, exist_ok=True)
    for name in ['kiber102-source.zip', 'kiber102-private-data.tar.gz', 'manifest.json']:
        if (output/name).exists():
            raise ValueError('Refusing to overwrite an existing delivery package')
    settings = dotenv_values(root/'.env', encoding='utf-8-sig', interpolate=False)
    secrets = [v.encode('utf8') for k,v in settings.items()
               if v and len(v) >= 12 and not v.startswith('replace-')
               and any(part in k for part in ['PASSWORD', 'SECRET', 'TOKEN', 'API_KEY'])]
    files = list(source_files(root))
    for name,path in files:
        if any(secret in path.read_bytes() for secret in secrets):
            raise ValueError('Project secret found in source file: ' + name)
    if include_private_data:
        url = make_url(settings.get('DATABASE_URL', ''))
        if not url.drivername.startswith('sqlite'):
            raise ValueError('Private packaging requires SQLite; use pg_dump for PostgreSQL')
    destination = output/'kiber102-source.zip'
    records = []
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name,path in files:
            compression = zipfile.ZIP_STORED if path.suffix.lower() in {'.webm','.jpg','.png','.woff2','.pdf'} else zipfile.ZIP_DEFLATED
            archive.write(path, arcname=name, compress_type=compression)
            records.append(file_info(path, name))
    commit = subprocess.run(['git','rev-parse','HEAD'], cwd=root, capture_output=True, text=True)
    status = subprocess.run(['git','status','--porcelain'], cwd=root, capture_output=True, text=True, check=True)
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
              'git_commit':commit.stdout.strip() if commit.returncode == 0 else None,
              'working_tree_dirty':bool(status.stdout.strip()),
              'source':{'file':destination.name, 'bytes':destination.stat().st_size, 'sha256':digest(destination), 'files':records},
              'private_data':private_archive(root, output, settings) if include_private_data else None}
    (output/'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include-private-data', action='store_true')
    arguments = parser.parse_args()
    result = package_release(Path(__file__).resolve().parents[1], arguments.output, arguments.include_private_data)
    print(json.dumps({'output':str(arguments.output.resolve()), 'source_files':len(result['source']['files']),
                      'private_data_included':bool(result['private_data'])}))
