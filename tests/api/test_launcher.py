import json
import shutil
import subprocess
from pathlib import Path

import pytest


HELPERS = Path(__file__).resolve().parents[2] / 'scripts' / 'local_processes.ps1'
POWERSHELL = shutil.which('pwsh') or shutil.which('powershell')


def ps(code):
    if not POWERSHELL:
        pytest.skip('PowerShell is needed for Windows launcher verification')
    command = "$ErrorActionPreference='Stop'; . '" + str(HELPERS).replace("'", "''") + "'; " + code
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_service_ownership_rejects_recycled_or_foreign_pids():
    result = ps("$root='C:/fixture/project'; $process=[pscustomobject]@{CommandLine='C:/fixture/project/.venv/python.exe -m apps.worker.main'; CreationDate=[datetime]'2026-11-13T12:00:00Z'}; "
                "@((Test-ServiceCommand worker $process $root $null), (Test-ServiceCommand bot $process $root $null), "
                "(Test-ServiceCommand worker $process 'C:/other' $null), "
                "(Test-ServiceCommand worker $process $root '2026-11-12T12:00:00.0000000Z')) | ConvertTo-Json -Compress")
    assert json.loads(result) == [True, False, False, False]


def test_fingerprint_tracks_sources_env_but_ignores_runtime_logs(tmp_path):
    source = tmp_path / 'apps' / 'api' / 'app'
    source.mkdir(parents=True)
    file = source / 'main.py'
    file.write_text('first', encoding='utf-8')
    (tmp_path / '.env').write_text('PRIVATE_TOKEN=first', encoding='utf-8')
    root = str(tmp_path).replace("'", "''")
    first = ps(f"Get-ProjectFingerprint '{root}'")
    assert len(first) == 64
    local = tmp_path / '.local'
    local.mkdir()
    (local / 'runtime.log').write_text('noise')
    assert ps(f"Get-ProjectFingerprint '{root}'") == first
    file.write_text('changed', encoding='utf-8')
    second = ps(f"Get-ProjectFingerprint '{root}'")
    assert second != first
    (tmp_path / '.env').write_text('PRIVATE_TOKEN=changed', encoding='utf-8')
    assert ps(f"Get-ProjectFingerprint '{root}'") != second


def test_saved_process_creation_timestamp_survives_json_datetime_roundtrip():
    result = ps("$root='C:/fixture/project'; $created=[datetime]'2026-11-13T11:05:13.6372480Z'; "
                "$process=[pscustomobject]@{CommandLine='C:/fixture/project/.venv/python.exe -m apps.worker.main'; CreationDate=$created}; "
                "$manifest=[pscustomobject]@{started=$created.ToUniversalTime().ToString('o')} | ConvertTo-Json | ConvertFrom-Json; "
                "Test-ServiceCommand worker $process $root $manifest.started | ConvertTo-Json -Compress")
    assert json.loads(result) is True
