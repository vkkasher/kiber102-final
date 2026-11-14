"""Restore the owner's private project-data release into a fresh clone."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def restore(archive_path, destination):
    destination = Path(destination).resolve()
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or '\\' in member.name or any(':' in part for part in path.parts) or member.name in names
                    or not (member.name in {'.env', '.env.original', 'PROJECT_DATA_MANIFEST.json'}
                            or path.parts[0] in {'.local', '.impeccable'})):
                raise ValueError('Invalid project-data archive member')
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination) or target.exists():
                raise ValueError('Restore refuses to overwrite an existing file: ' + member.name)
            names.add(member.name)
        manifest_stream = archive.extractfile('PROJECT_DATA_MANIFEST.json')
        manifest = json.load(manifest_stream)
        expected = {item['path']: item for item in manifest['files']}
        if names != set(expected) | {'PROJECT_DATA_MANIFEST.json'}:
            raise ValueError('Archive does not match its manifest')
        # Verify every byte before writing anything into the destination.
        for member in members:
            if member.name == 'PROJECT_DATA_MANIFEST.json':
                continue
            stream = archive.extractfile(member)
            if (member.size != expected[member.name]['bytes']
                    or hashlib.file_digest(stream, 'sha256').hexdigest() != expected[member.name]['sha256']):
                raise ValueError('Project-data checksum mismatch')
        for member in members:
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('xb') as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
    print('Project data restored. Existing files were not overwritten. Secrets were not printed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path, default=Path.cwd())
    args = parser.parse_args()
    restore(args.archive, args.destination)
