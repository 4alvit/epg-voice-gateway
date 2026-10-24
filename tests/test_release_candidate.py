"""A CI export records exact artifacts, never hardware or deployment acceptance."""
import copy
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('release_candidate', Path(__file__).resolve().parents[1] / 'scripts/release_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
VERSION = '0.1.0b2'
SOURCE = 'a' * 40


def fixture_files(root):
    prefix = f'epg_voice_gateway-{VERSION}'
    info = f'Name: epg-voice-gateway\nVersion: {VERSION}\n'.encode()
    with zipfile.ZipFile(root / f'{prefix}-py3-none-any.whl', 'w') as archive:
        archive.writestr(f'{prefix}.dist-info/METADATA', info)
        archive.writestr(f'{prefix}.dist-info/licenses/LICENSE', 'MIT')
    for filename, contents in [(f'{prefix}.tar.gz', {f'{prefix}/PKG-INFO': info, f'{prefix}/LICENSE': b'MIT'}),
                               ('source.tar.gz', {'pyproject.toml': f'[project]\nversion = "{VERSION}"\n'.encode()})]:
        with tarfile.open(root / filename, 'w:gz') as archive:
            for name, data in contents.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    config = b'{"architecture":"amd64","os":"linux"}'
    image_id = hashlib.sha256(config).hexdigest()
    with tarfile.open(root / 'runtime-linux-amd64.tar.gz', 'w:gz') as archive:
        payloads = {'manifest.json': json.dumps([{'Config': image_id + '.json'}]).encode(),
                    image_id + '.json': config}
        for name, data in payloads.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    for name in ['container-trivy.json', 'container-sbom.cdx.json']:
        (root / name).write_bytes(b'fixture')
    return {'Os': 'linux', 'Architecture': 'amd64', 'Id': 'sha256:' + image_id,
            'Config': {'Labels': {'org.opencontainers.image.revision': SOURCE}}}


def test_records_exact_hashes_without_claiming_acceptance(tmp_path):
    image = fixture_files(tmp_path)
    result = candidate.seal(tmp_path, VERSION, SOURCE, '123', image)
    assert not result['deployment_accepted'] and not result['physical_playback_accepted']
    assert len(result['files']) == 6
    for file in result['files']:
        assert hashlib.sha256((tmp_path / file['name']).read_bytes()).hexdigest() == file['sha256']
    assert json.loads((tmp_path / 'candidate-manifest.json').read_text()) == result
    assert len((tmp_path / 'SHA256SUMS').read_text().splitlines()) == 7


@pytest.mark.parametrize('change', ['stable', 'wrong_source', 'wrong_platform', 'extra', 'missing', 'symlink', 'wrong_archive'])
def test_rejects_unqualified_or_ambiguous_export(tmp_path, change):
    image = fixture_files(tmp_path)
    version = VERSION
    if change == 'stable':
        version = '0.1.0'
    elif change == 'wrong_source':
        image['Config']['Labels']['org.opencontainers.image.revision'] = 'c' * 40
    elif change == 'wrong_platform':
        image['Architecture'] = 'arm64'
    elif change == 'wrong_archive':
        image['Id'] = 'sha256:' + 'd' * 64
    elif change == 'extra':
        (tmp_path / '.env').write_text('private')
    elif change == 'missing':
        (tmp_path / 'source.tar.gz').unlink()
    elif change == 'symlink':
        (tmp_path / 'source.tar.gz').unlink()
        (tmp_path / 'source.tar.gz').symlink_to(tmp_path / 'container-trivy.json')
    with pytest.raises(ValueError):
        candidate.seal(tmp_path, version, SOURCE, '123', copy.deepcopy(image))
