"""Seal an explicitly exported beta; this is not deployment acceptance."""
from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

MAX_BYTES = 400 * 1024 * 1024
INDEX_TYPES = {'application/vnd.oci.image.index.v1+json',
               'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST_TYPES = {'application/vnd.oci.image.manifest.v1+json',
                  'application/vnd.docker.distribution.manifest.v2+json'}


def inspected_config(archive: tarfile.TarFile, names: list[str], image: dict,
                     config_name: str, config_bytes: bytes, source: str) -> str:
    """Bind either a classic image ID or a containerd target to the saved config."""
    actual = 'sha256:' + hashlib.sha256(config_bytes).hexdigest()
    config = json.loads(config_bytes)
    if (config.get('os') != 'linux' or config.get('architecture') != 'amd64'
            or config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != source):
        raise ValueError('saved image platform or source label mismatch')
    descriptor = image.get('Descriptor')
    if descriptor is None:
        if image['Id'] != actual:
            raise ValueError('runtime archive does not match the inspected image')
        return actual
    if not isinstance(descriptor, dict) or descriptor.get('digest') != image['Id']:
        raise ValueError('inspected image descriptor does not match its ID')
    visited = set()

    def read_descriptor(item: dict, depth: int) -> dict:
        if not isinstance(item, dict) or depth > 4 or len(visited) >= 32:
            raise ValueError('invalid or excessive image descriptor graph')
        digest = item.get('digest', '')
        size = item.get('size')
        if (not re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
                or type(size) is not int or not 0 < size <= 8 * 1024 * 1024
                or digest in visited):
            raise ValueError('invalid, repeated or cyclic image descriptor')
        visited.add(digest)
        name = 'blobs/sha256/' + digest.removeprefix('sha256:')
        if names.count(name) != 1:
            raise ValueError('image descriptor blob missing or ambiguous')
        member = archive.getmember(name)
        if not member.isfile() or member.size != size:
            raise ValueError('image descriptor size or type mismatch')
        with archive.extractfile(member) as stream:
            data = stream.read()
        if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('image descriptor checksum mismatch')
        result = json.loads(data)
        if not isinstance(result, dict) or result.get('schemaVersion') != 2:
            raise ValueError('invalid image descriptor schema')
        return result

    def matches(item: dict, depth: int = 0) -> int:
        blob = read_descriptor(item, depth)
        kind = item.get('mediaType')
        if blob.get('mediaType', kind) != kind:
            raise ValueError('image descriptor media type mismatch')
        if kind in MANIFEST_TYPES:
            declared = blob.get('config', {})
            return int(declared.get('digest') == actual
                       and declared.get('size') == len(config_bytes)
                       and config_name == 'blobs/sha256/' + actual.removeprefix('sha256:'))
        if kind not in INDEX_TYPES or not isinstance(blob.get('manifests'), list):
            raise ValueError('unsupported image descriptor type')
        children = blob['manifests']
        if len(children) > 32 or any(not isinstance(child, dict) for child in children):
            raise ValueError('invalid image index entries')
        count = 0
        for child in children:
            platform = child.get('platform')
            if platform is None or platform == {'os': 'linux', 'architecture': 'amd64'}:
                count += matches(child, depth + 1)
        return count

    if matches(descriptor) != 1:
        raise ValueError('runtime config is not uniquely bound to the inspected image')
    return actual


def saved_image_config(path: Path, image: dict, source: str) -> str:
    """Inspect archive provenance without loading or running the saved image."""
    if (image.get('Os') != 'linux' or image.get('Architecture') != 'amd64'
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', image.get('Id', ''))
            or image.get('Config', {}).get('Labels', {}).get('org.opencontainers.image.revision') != source):
        raise ValueError('image platform or source label mismatch')
    with tarfile.open(path, 'r:gz') as archive:
        names = archive.getnames()
        if names.count('manifest.json') != 1:
            raise ValueError('runtime archive manifest missing or ambiguous')
        member = archive.getmember('manifest.json')
        if not member.isfile() or member.size > 65536:
            raise ValueError('invalid runtime manifest')
        with archive.extractfile(member) as stream:
            manifest = json.load(stream)
        if type(manifest) is not list or len(manifest) != 1:
            raise ValueError('runtime archive must contain one image')
        config_name = manifest[0].get('Config')
        if type(config_name) is not str or names.count(config_name) != 1:
            raise ValueError('runtime config missing or ambiguous')
        config = archive.getmember(config_name)
        if not config.isfile() or config.size > 8 * 1024 * 1024:
            raise ValueError('invalid runtime config')
        with archive.extractfile(config) as stream:
            actual = inspected_config(archive, names, image, config_name, stream.read(), source)
    return actual


def metadata(data: bytes, version: str) -> None:
    fields = email.parser.BytesParser().parsebytes(data)
    if fields.get_all('Name') != ['epg-voice-gateway'] or fields.get_all('Version') != [version]:
        raise ValueError('distribution metadata does not match candidate')


def seal(directory: Path, version: str, source: str, run_id: str, image: dict, run_attempt: str = "1") -> dict:
    if not re.fullmatch(r'\d+\.\d+\.\d+b[1-9]\d*', version):
        raise ValueError('only explicit beta versions may be exported')
    if (not re.fullmatch(r'[0-9a-f]{40}', source)
            or not re.fullmatch(r'[1-9]\d*', run_id)
            or not re.fullmatch(r'[1-9]\d*', run_attempt)):
        raise ValueError('invalid source/run provenance')
    prefix = f'epg_voice_gateway-{version}'
    expected = {f'{prefix}-py3-none-any.whl', f'{prefix}.tar.gz', 'source.tar.gz',
                'runtime-linux-amd64.tar.gz', 'container-trivy.json', 'container-sbom.cdx.json'}
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('unexpected or missing candidate files')
    paths = sorted(directory.iterdir())
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size == 0 for p in paths):
        raise ValueError('candidate files must be nonempty regular files')
    if sum(p.stat().st_size for p in paths) > MAX_BYTES:
        raise ValueError('candidate exceeds 400 MiB export budget')
    with zipfile.ZipFile(directory / f'{prefix}-py3-none-any.whl') as archive:
        names = archive.namelist()
        member = f'{prefix}.dist-info/METADATA'
        if names.count(member) != 1 or f'{prefix}.dist-info/licenses/LICENSE' not in names:
            raise ValueError('wheel metadata/license missing or ambiguous')
        metadata(archive.read(member), version)
    with tarfile.open(directory / f'{prefix}.tar.gz', 'r:gz') as archive:
        names = archive.getnames()
        member = f'{prefix}/PKG-INFO'
        if names.count(member) != 1 or f'{prefix}/LICENSE' not in names:
            raise ValueError('source distribution metadata/license missing or ambiguous')
        with archive.extractfile(member) as stream:
            metadata(stream.read(), version)
    with tarfile.open(directory / 'source.tar.gz', 'r:gz') as archive:
        if archive.getnames().count('pyproject.toml') != 1:
            raise ValueError('source manifest missing or ambiguous')
        with archive.extractfile('pyproject.toml') as stream:
            if tomllib.loads(stream.read().decode())['project']['version'] != version:
                raise ValueError('source version mismatch')
    actual = saved_image_config(directory / 'runtime-linux-amd64.tar.gz', image, source)
    files = []
    for path in paths:
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        files.append({'name': path.name, 'size': path.stat().st_size, 'sha256': digest})
    manifest = {'schema_version': 1, 'stage': 'beta', 'version': version,
                'source_commit': source, 'repository': '4alvit/epg-voice-gateway',
                'workflow_run_id': run_id, 'workflow_run_attempt': int(run_attempt), 'image_config_digest': actual,
                'platform': 'linux/amd64', 'deployment_accepted': False,
                'physical_playback_accepted': False, 'files': files}
    target = directory / 'candidate-manifest.json'
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    sums = [f"{f['sha256']}  {f['name']}\n" for f in files]
    sums.append(f'{hashlib.sha256(target.read_bytes()).hexdigest()}  {target.name}\n')
    (directory / 'SHA256SUMS').write_text(''.join(sums))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--image-inspect', type=Path, required=True)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_REPOSITORY') != '4alvit/epg-voice-gateway'):
        raise ValueError('export requires explicit dispatch on the owned main branch')
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if source != os.environ.get('GITHUB_SHA'):
        raise ValueError('checkout does not match requested source')
    version = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
    images = json.loads(args.image_inspect.read_text())
    if type(images) is not list or len(images) != 1:
        raise ValueError('expected exactly one image')
    result = seal(args.directory, version, source, os.environ.get('GITHUB_RUN_ID', ''), images[0],
                  os.environ.get('GITHUB_RUN_ATTEMPT', ''))
    print(f"Sealed beta {version}: {len(result['files'])} files; private acceptance still required")


if __name__ == '__main__':
    main()
