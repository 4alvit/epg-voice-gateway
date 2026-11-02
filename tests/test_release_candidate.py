"""A CI export records exact artifacts, never hardware or deployment acceptance."""
import copy
import hashlib
import importlib.util
import io
import json
import tarfile
import tomllib
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
    config = json.dumps({'architecture': 'amd64', 'os': 'linux',
                         'config': {'Labels': {'org.opencontainers.image.revision': SOURCE}}}).encode()
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


def containerd_archive(root, image, *, index=True, change=None):
    """Model docker save's OCI blobs with an inspected manifest/index image ID."""
    config = json.dumps({'architecture': 'amd64', 'os': 'linux',
                         'config': {'Labels': {'org.opencontainers.image.revision': SOURCE}}}).encode()
    if change == 'wrong_saved_source':
        config = config.replace(SOURCE.encode(), b'b' * 40)
    config_id = hashlib.sha256(config).hexdigest()
    config_name = 'blobs/sha256/' + config_id
    payloads = {config_name: config}

    def blob(data, kind):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': kind, **data}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        payloads['blobs/sha256/' + digest] = raw
        return {'mediaType': kind, 'digest': 'sha256:' + digest, 'size': len(raw)}

    manifest = blob({'config': {'digest': 'sha256:' + config_id,
                                'size': len(config) + (1 if change == 'wrong_config_size' else 0)},
                     'layers': []}, 'application/vnd.oci.image.manifest.v1+json')
    manifest['platform'] = {'os': 'linux', 'architecture': 'amd64'}
    if change == 'wrong_platform':
        manifest['platform']['architecture'] = 'arm64'
    children = [manifest]
    if change == 'duplicate_platform':
        children.append(copy.deepcopy(manifest))
    descriptor = blob({'manifests': children}, 'application/vnd.oci.image.index.v1+json') if index else manifest
    image['Id'] = descriptor['digest']
    image['Descriptor'] = copy.deepcopy(descriptor)
    if change == 'wrong_inspected_id':
        image['Id'] = 'sha256:' + 'e' * 64
    elif change == 'wrong_descriptor_size':
        image['Descriptor']['size'] += 1
    elif change == 'missing_blob':
        del payloads['blobs/sha256/' + descriptor['digest'][7:]]
    elif change == 'tampered_blob':
        name = 'blobs/sha256/' + descriptor['digest'][7:]
        payloads[name] = payloads[name].replace(b'"schemaVersion": 2', b'"schemaVersion": 3')
    elif change == 'unbound_config':
        # Same metadata is insufficient: bind the exact bytes, not the labels.
        config += b' '
        config_id = hashlib.sha256(config).hexdigest()
        config_name = 'blobs/sha256/' + config_id
        payloads[config_name] = config
    payloads['manifest.json'] = json.dumps([{'Config': config_name}]).encode()
    with tarfile.open(root / 'runtime-linux-amd64.tar.gz', 'w:gz') as archive:
        for name, data in payloads.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return 'sha256:' + config_id


@pytest.mark.parametrize('index', [False, True])
def test_containerd_id_is_bound_to_exact_config_through_descriptor_graph(tmp_path, index):
    image = fixture_files(tmp_path)
    config_id = containerd_archive(tmp_path, image, index=index)
    assert image['Id'] != config_id
    result = candidate.seal(tmp_path, VERSION, SOURCE, '123', image)
    assert result['image_config_digest'] == config_id


@pytest.mark.parametrize('change', ['wrong_saved_source', 'wrong_config_size', 'wrong_platform',
                                  'duplicate_platform', 'wrong_inspected_id', 'wrong_descriptor_size',
                                  'missing_blob', 'tampered_blob', 'unbound_config'])
def test_containerd_graph_rejects_mismatched_or_ambiguous_provenance(tmp_path, change):
    image = fixture_files(tmp_path)
    containerd_archive(tmp_path, image, change=change)
    with pytest.raises(ValueError):
        candidate.seal(tmp_path, VERSION, SOURCE, '123', image)


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


def test_public_package_version_matches_distribution_and_lock():
    root = Path(__file__).resolve().parents[1]
    from epg_voice_gateway import __version__

    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    locked = [item["version"] for item in packages if item["name"] == project["name"]]
    assert __version__ == project["version"]
    assert locked == [__version__]
