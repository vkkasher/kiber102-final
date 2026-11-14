"""Install a pinned, checksum-verified Windows local model into ignored .local."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.local' / 'llm-runtime'
ASSETS = [
    ('llama-vulkan.zip', 'https://github.com/ggml-org/llama.cpp/releases/download/b12360/llama-b12360-bin-win-vulkan-x64.zip',
     '42a6f9df608b99c1c83fcaf77284a516e3f167b32a4690f4e4e364c377547055'),
    ('Qwen3-4B-Q4_K_M.gguf', 'https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf',
     '7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5'),
]


def checksum(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def install():
    if sys.platform != 'win32':
        raise SystemExit('This optional installer targets Windows x64. Other systems can host an OpenAI-compatible local model.')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    for name, url, expected in ASSETS:
        target = RUNTIME / name
        if not target.exists() or checksum(target) != expected:
            temporary = target.with_suffix(target.suffix + '.part')
            # Explicit installation only. Resume a contiguous .part, never infer
            # completion from the apparent size of a sparse parallel download.
            for attempt in range(8):
                start = temporary.stat().st_size if temporary.exists() else 0
                try:
                    with httpx.stream('GET', url, headers={'Range': f'bytes={start}-'} if start else {},
                                      follow_redirects=True, timeout=60) as response:
                        if response.status_code == 416 and start:
                            temporary.unlink()
                            continue
                        response.raise_for_status()
                        resume = start and response.status_code == 206 and response.headers.get('content-range', '').startswith(f'bytes {start}-')
                        with temporary.open('ab' if resume else 'wb') as output:
                            for block in response.iter_bytes(1024 * 1024):
                                output.write(block)
                    if checksum(temporary) != expected:
                        temporary.unlink()
                        raise ValueError('Checksum mismatch: ' + name)
                    temporary.replace(target)
                    break
                except (httpx.HTTPError, ValueError):
                    if attempt == 7:
                        raise
            print('Verified:', name, flush=True)
    destination = RUNTIME / 'bin'
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(RUNTIME / 'llama-vulkan.zip') as archive:
        for member in archive.infolist():
            if not (destination / member.filename).resolve().is_relative_to(destination.resolve()):
                raise ValueError('Unsafe archive member')
        archive.extractall(destination)
    (RUNTIME / 'installed.json').write_text(json.dumps({name: digest for name, _, digest in ASSETS}, indent=2), encoding='utf8')
    print('Local Qwen3-4B installed. API billing is zero; hardware and electricity are not estimated.')


if __name__ == '__main__':
    install()
