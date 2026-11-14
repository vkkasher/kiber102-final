from contextlib import contextmanager
import hashlib
from io import BytesIO
import zipfile

import httpx
import pytest


@pytest.mark.parametrize('failure', ['oversized', 'corrupt'])
def test_installer_recovers_only_its_own_invalid_partial(tmp_path, monkeypatch, failure):
    from scripts import install_local_llm as installer
    payload = BytesIO()
    with zipfile.ZipFile(payload, 'w') as archive:
        archive.writestr('fixture.txt', 'TEST_DATA installer')
    data = payload.getvalue()
    name = 'llama-vulkan.zip'
    partial = tmp_path / (name + '.part')
    partial.write_bytes(b'invalid-old-partial')
    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append(kwargs.get('headers'))
        if len(calls) == 1:
            status, body = (416, b'') if failure == 'oversized' else (200, b'corrupt-complete-file')
        else:
            status, body = 200, data
        yield httpx.Response(status, content=body, request=httpx.Request(method, url))

    monkeypatch.setattr(installer, 'RUNTIME', tmp_path)
    monkeypatch.setattr(installer, 'ASSETS', [(name, 'https://example.org/TEST_DATA.zip', hashlib.sha256(data).hexdigest())])
    monkeypatch.setattr(installer.httpx, 'stream', stream)
    installer.install()
    assert len(calls) == 2 and calls[1] == {}
    assert not partial.exists()
    assert (tmp_path / 'bin' / 'fixture.txt').read_text() == 'TEST_DATA installer'
