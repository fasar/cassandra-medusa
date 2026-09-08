# -*- coding: utf-8 -*-
# Copyright 2024 DataStax, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Manual test protocol for client-side encryption - see README.md for what it checks and why.

Drives the medusa CLI against CCM clusters the way an operator would. Fails loudly: every command is
checked, every row count is compared to the expected value, and the run stops on the first mismatch.
"""

import argparse
import base64
import os
import pathlib
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
WORK = pathlib.Path(os.environ.get('MANUAL_TEST_DIR', '/tmp/medusa-manual'))
CASSANDRA_VERSION = os.environ.get('CASSANDRA_VERSION', '4.1.9')
CCM_REPO = pathlib.Path.home() / '.ccm' / 'repository' / CASSANDRA_VERSION
SRC_CLUSTER = 'manual_src'
DST_CLUSTER = 'manual_dst'
LOCAL_BUCKET = WORK / 'local_bucket'
KEY = base64.b64encode(b'medusa-manual-protocol-key-32byt').decode()

# ks_beta stops growing after B2, which is what makes a selective restore visible
STATES = {
    'B1': {'ks_alpha': 100, 'ks_beta': 50},
    'B2': {'ks_alpha': 200, 'ks_beta': 100},
    'B3': {'ks_alpha': 300, 'ks_beta': 100},
}

# Client-side encryption is implemented with the S3 Encryption Client, so the encrypted
# configuration needs an S3 API: MinIO. The local provider only appears in A10, which checks that
# a key configured with it is refused.
CONFIGURATIONS = {
    'minio-plain': {'provider': 'minio', 'encrypted': False},
    'minio-cse': {'provider': 'minio', 'encrypted': True},
}
# The ciphertext of an object is its plaintext plus this authentication tag
GCM_TAG_LENGTH = 16


class Failure(Exception):
    pass


def log(message, level='   '):
    print(f'{time.strftime("%H:%M:%S")} {level} {message}', flush=True)


def step(name):
    log(name, level='>>>')


def run(cmd, check=True, cwd=REPO, quiet=False):
    """Run a command, showing it, and fail loudly with its output when it does not succeed."""
    if not quiet:
        log('$ ' + ' '.join(str(c) for c in cmd))
    result = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True,
        env={**os.environ, 'PYTHONWARNINGS': 'ignore'},
    )
    if check and result.returncode != 0:
        raise Failure(
            'command failed ({}): {}\n--- stdout ---\n{}\n--- stderr ---\n{}'.format(
                result.returncode, ' '.join(str(c) for c in cmd),
                result.stdout[-4000:], result.stderr[-4000:]
            )
        )
    return result


def venv_bin(name):
    """
    Path to a console script inside the poetry venv.

    `poetry run medusa` does not work with this project: [tool.poetry.scripts] declares its entry
    points with the legacy `reference`/`type` keys, and Poetry 2.x resolves declared scripts itself
    expecting `callable`, so it dies with KeyError('callable') printed as just 'callable'. The
    installed console script itself is fine, so call it directly. Calling it directly is also a
    second faster per invocation, and this protocol makes a lot of invocations.
    """
    if not hasattr(venv_bin, 'root'):
        venv_bin.root = pathlib.Path(
            run(['poetry', 'env', 'info', '--path'], quiet=True).stdout.strip()
        ) / 'bin'
    return venv_bin.root / name


def medusa(config_file, *args, check=True):
    return run([venv_bin('medusa'), '--config-file', config_file, *args], check=check)


# --------------------------------------------------------------------------- CCM


def ccm(*args, check=True, cluster=None):
    if cluster:
        run([venv_bin('ccm'), 'switch', cluster], quiet=True)
    return run([venv_bin('ccm'), *args], check=check)


def recreate_cluster(name):
    step(f'Recreating CCM cluster {name}')
    run([venv_bin('ccm'), 'remove', name], check=False, quiet=True)
    run([venv_bin('ccm'), 'create', name, '-v', f'binary:{CASSANDRA_VERSION}', '-n', '1'])
    conf = pathlib.Path.home() / '.ccm' / name / 'node1' / 'conf'
    env_sh = conf / 'cassandra-env.sh'
    text = env_sh.read_text()
    text = text.replace('#MAX_HEAP_SIZE="4G"', 'MAX_HEAP_SIZE="512m"')
    text = text.replace('#HEAP_NEWSIZE="800M"', 'HEAP_NEWSIZE="200M"')
    env_sh.write_text(text)
    jvm11 = conf / 'jvm11-server.options'
    if jvm11.exists():
        opts = jvm11.read_text()
        opts = opts.replace('-XX:+UseConcMarkSweepGC', '#-XX:+UseConcMarkSweepGC')
        opts = opts.replace('#-XX:+UseG1GC', '-XX:+UseG1GC')
        jvm11.write_text(opts)
    ccm('start', '--wait-for-binary-proto', cluster=name)


def stop_cluster(name):
    run([venv_bin('ccm'), 'switch', name], check=False, quiet=True)
    run([venv_bin('ccm'), 'stop'], check=False, quiet=True)


# --------------------------------------------------------------------------- CQL


def session():
    from cassandra.cluster import Cluster
    from cassandra import ProtocolVersion
    last = None
    for _ in range(30):
        try:
            return Cluster(contact_points=['127.0.0.1'], protocol_version=ProtocolVersion.V4).connect()
        except Exception as e:                                          # noqa: BLE001 - retry loop
            last = e
            time.sleep(2)
    raise Failure(f'could not connect to Cassandra: {last}')


def close(sess):
    """Shut the cluster down, not just the session: leaving it up prints a scheduler traceback at exit."""
    sess.cluster.shutdown()


def create_schema(sess):
    for ks in ('ks_alpha', 'ks_beta'):
        sess.execute(
            f"CREATE KEYSPACE IF NOT EXISTS {ks} WITH replication = "
            "{'class':'SimpleStrategy','replication_factor':1}"
        )
        table = 't_alpha' if ks == 'ks_alpha' else 't_beta'
        sess.execute(f'CREATE TABLE IF NOT EXISTS {ks}.{table} (id int PRIMARY KEY, value text)')
    time.sleep(2)


def table_of(ks):
    return 't_alpha' if ks == 'ks_alpha' else 't_beta'


def seed_up_to(sess, counts):
    """Insert rows so that each keyspace holds exactly the requested number, then flush."""
    for ks, target in counts.items():
        table = table_of(ks)
        current = count_rows(sess, ks)
        for i in range(current, target):
            sess.execute(f"INSERT INTO {ks}.{table} (id, value) VALUES ({i}, 'row-{i}')")
    flush()


def flush():
    """
    ccm's own option parser eats a leading -D even after --, so the rmiURLParsing flag cannot be
    passed here. It is not needed for a plain flush; medusa gets it from nodetool_flags in the
    config for its own nodetool calls.
    """
    ccm('node1', 'nodetool', 'flush')


def count_rows(sess, ks):
    return sess.execute(f'SELECT COUNT(*) FROM {ks}.{table_of(ks)}').one()[0]


def assert_counts(sess, expected, label):
    actual = {ks: count_rows(sess, ks) for ks in expected}
    if actual != expected:
        raise Failure(f'{label}: expected {expected}, got {actual}')
    log(f'{label}: {actual} as expected', level=' ok')


# --------------------------------------------------------------------------- config


def write_config(name, provider, encrypted, cluster, prefix):
    conf_dir = pathlib.Path.home() / '.ccm' / cluster / 'node1' / 'conf' / 'cassandra.yaml'
    lines = [
        '[cassandra]',
        'is_ccm = 1',
        'stop_cmd = ccm stop',
        'start_cmd = ccm start',
        'cql_username = cassandra',
        'cql_password = cassandra',
        f'config_file = {conf_dir}',
        f'sstableloader_bin = {CCM_REPO / "bin" / "sstableloader"}',
        'nodetool_flags = -Dcom.sun.jndi.rmiURLParsing=legacy',
        'nodetool_port = 7100',
        'resolve_ip_addresses = False',
        'use_sudo = false',
        '',
        '[storage]',
        'use_sudo_for_restore = false',
        'host_file_separator = ,',
        'fqdn = 127.0.0.1',
        f'prefix = {prefix}',
        'backup_grace_period_in_days = 0',
        'concurrent_transfers = 4',
    ]
    if provider == 'local':
        lines += [
            'storage_provider = local',
            'bucket_name = local_bucket',
            f'base_path = {WORK}',
        ]
    else:
        lines += [
            'storage_provider = s3_compatible',
            'bucket_name = medusa-manual',
            'key_file = ~/.aws/minio_credentials',
            'api_profile = default',
            'host = 127.0.0.1',
            'port = 9000',
            'secure = False',
            'region = default',
            'base_path = /tmp',
            'multi_part_upload_threshold = 1024',
        ]
    if encrypted:
        lines += [f'key_secret_base64 = {KEY}']
    lines += ['', '[monitoring]', 'monitoring_provider = local', '']

    path = WORK / f'medusa-{name}.ini'
    path.write_text('\n'.join(lines))
    return path


def prepare_storage(provider):
    if provider == 'local':
        if LOCAL_BUCKET.exists():
            shutil.rmtree(LOCAL_BUCKET)
        LOCAL_BUCKET.mkdir(parents=True)
    else:
        import boto3
        import botocore.exceptions
        client = boto3.client(
            's3', endpoint_url='http://127.0.0.1:9000',
            aws_access_key_id=_minio_credential('aws_access_key_id'),
            aws_secret_access_key=_minio_credential('aws_secret_access_key'),
        )
        try:
            client.head_bucket(Bucket='medusa-manual')
        except botocore.exceptions.ClientError:
            client.create_bucket(Bucket='medusa-manual')
        paginator = client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket='medusa-manual'):
            keys = [{'Key': o['Key']} for o in page.get('Contents', [])]
            if keys:
                client.delete_objects(Bucket='medusa-manual', Delete={'Objects': keys})


def _minio_credential(key):
    path = pathlib.Path.home() / '.aws' / 'minio_credentials'
    for line in path.read_text().splitlines():
        if line.strip().startswith(key):
            return line.split('=', 1)[1].strip()
    raise Failure(f'{key} not found in {path}')


# --------------------------------------------------------------------------- storage assertions


def storage_checks(config_file, encrypted, backup_name):
    """A7-A9: what actually landed in storage, read straight from the backend."""
    sys.path.insert(0, str(REPO))
    import json
    import medusa.config
    from medusa.storage import Storage

    config = medusa.config.load_config({}, pathlib.Path(config_file))
    with Storage(config=config.storage) as storage:
        # prefix=None means "everything" for the local and GCS drivers, but S3 and Azure format it
        # as the literal string "None" and silently return nothing. Always pass a real prefix.
        blobs = storage.storage_driver.list_blobs(prefix=storage._prefix)
        names = [b.name for b in blobs]

        # A7 - an SSTable must be ciphertext exactly when encryption is on. The S3 Encryption
        # Client stores the wrapped data key in the object's user metadata (x-amz-meta-x-amz-3),
        # and the ciphertext is the plaintext plus a 16-byte authentication tag; the manifest of a
        # differential backup records both sizes.
        data_blob = next((n for n in names if n.endswith('-Data.db')), None)
        if data_blob is None:
            raise Failure('no SSTable found in storage')
        driver = storage.storage_driver
        head = driver.s3_client.head_object(Bucket=driver.bucket_name, Key=data_blob)
        looks_encrypted = 'x-amz-3' in head['Metadata']
        if looks_encrypted != encrypted:
            raise Failure(
                'A7: {} carries metadata {}, which reads as {} while encryption is {}'.format(
                    data_blob, sorted(head['Metadata']),
                    'encrypted' if looks_encrypted else 'plaintext',
                    'on' if encrypted else 'off')
            )
        log(f'A7: SSTable metadata {sorted(head["Metadata"]) or "none"} - '
            f'{"S3 Encryption Client object" if looks_encrypted else "raw SSTable"}', level=' ok')

        # A8 - metadata is plaintext either way
        schema_blob = next((n for n in names if n.endswith('schema.cql')), None)
        schema = storage.storage_driver.get_blob_content_as_string(schema_blob)
        if 'CREATE' not in schema.upper():
            raise Failure('A8: schema.cql is not readable as plaintext')
        log('A8: schema.cql readable without the key', level=' ok')

        # A9 - source_MD5 only for differential backups with encryption on
        manifest_blob = next(n for n in names if n.endswith(f'{backup_name}/meta/manifest.json'))
        manifest = json.loads(storage.storage_driver.get_blob_content_as_string(manifest_blob))
        objects = [o for section in manifest for o in section['objects']]
        has_source = any('source_MD5' in o for o in objects)
        if has_source != encrypted:
            raise Failure(
                f'A9: source_MD5 present={has_source} in {backup_name} while encryption is {encrypted}')
        log(f'A9: source_MD5 present in manifest: {has_source}', level=' ok')

        # A7 (continued) - with encryption on, every object is exactly one tag longer than its file
        if encrypted:
            wrong = [o['path'] for o in objects
                     if 'source_size' in o and o['size'] != o['source_size'] + GCM_TAG_LENGTH]
            if wrong:
                raise Failure(f'A7: objects whose size is not source_size + {GCM_TAG_LENGTH}: {wrong[:5]}')
            log(f'A7: {sum("source_size" in o for o in objects)} objects are source_size + {GCM_TAG_LENGTH} '
                f'bytes long', level=' ok')


# --------------------------------------------------------------------------- scenario


def run_configuration(name):
    spec = CONFIGURATIONS[name]
    provider, encrypted = spec['provider'], spec['encrypted']
    log('=' * 78)
    step(f'Configuration {name} - storage={provider}, client-side encryption={"on" if encrypted else "off"}')
    log('=' * 78)

    prepare_storage(provider)
    recreate_cluster(SRC_CLUSTER)
    src_config = write_config(name, provider, encrypted, SRC_CLUSTER, f'{name}-prefix')

    sess = session()
    create_schema(sess)

    # ---- A1 to A3: the three backups
    for backup_name, mode in (('B1', 'full'), ('B2', 'differential'), ('B3', 'differential')):
        seed_up_to(sess, STATES[backup_name])
        assert_counts(sess, STATES[backup_name], f'state before {backup_name}')
        step(f'A: {mode} backup {backup_name}')
        medusa(src_config, 'backup', '--backup-name', backup_name, '--mode', mode,
               '--enable-md5-checks')

    # ---- A4 to A6
    step('A4: list-backups')
    listing = medusa(src_config, 'list-backups').stdout
    for backup_name in STATES:
        if backup_name not in listing:
            raise Failure(f'A4: {backup_name} missing from list-backups output')
    log('A4: the three backups are listed', level=' ok')

    step('A5: verify each backup')
    for backup_name in STATES:
        medusa(src_config, 'verify', '--backup-name', backup_name, '--enable-md5-checks')
    log('A5: three backups verified', level=' ok')

    step('A6: status of B3')
    status = medusa(src_config, 'status', '--backup-name', 'B3').stdout
    if 'Finished:' not in status and 'complete' not in status.lower():
        raise Failure(f'A6: unexpected status output\n{status}')
    log('A6: B3 reported complete', level=' ok')

    # ---- A7 to A9
    step('A7-A9: what actually landed in storage')
    storage_checks(src_config, encrypted, 'B3')

    # ---- R1: whole-node restore of the full backup
    step('R1: in-place restore of B1 (whole node)')
    close(sess)
    medusa(src_config, 'restore-node', '--backup-name', 'B1', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    assert_counts(sess, STATES['B1'], 'R1 after restoring B1')

    # ---- R2: single keyspace out of two
    step('R2: in-place restore of B3, ks_alpha only')
    close(sess)
    medusa(src_config, 'restore-node', '--backup-name', 'B3', '--temp-dir', str(WORK / 'tmp'),
           '--keyspace', 'ks_alpha')
    sess = session()
    assert_counts(sess, {'ks_alpha': STATES['B3']['ks_alpha'], 'ks_beta': STATES['B1']['ks_beta']},
                  'R2 after restoring ks_alpha only')

    # ---- R3: another cluster, through sstableloader
    step('R3: restore B2 onto a second cluster with sstableloader')
    close(sess)
    stop_cluster(SRC_CLUSTER)
    recreate_cluster(DST_CLUSTER)
    dst_config = write_config(f'{name}-dst', provider, encrypted, DST_CLUSTER, f'{name}-prefix')
    sess = session()
    create_schema(sess)          # sstableloader loads into an existing schema, it does not create one
    assert_counts(sess, {'ks_alpha': 0, 'ks_beta': 0}, 'R3 target cluster starts empty')
    medusa(dst_config, 'restore-node', '--backup-name', 'B2', '--temp-dir', str(WORK / 'tmp'),
           '--use-sstableloader')
    flush()
    assert_counts(sess, STATES['B2'], 'R3 after sstableloader restore of B2')
    close(sess)
    stop_cluster(DST_CLUSTER)

    log(f'Configuration {name}: PASS', level='===')


def refusal_check():
    """A10: a key configured with a provider other than S3 is refused, and says so."""
    step('A10: a client-side encryption key with the local provider is refused')
    prepare_storage('local')
    config = write_config('local-cse', 'local', True, SRC_CLUSTER, 'local-cse-prefix')
    result = medusa(config, 'list-backups', check=False)
    if result.returncode == 0:
        raise Failure('A10: medusa accepted a key with storage_provider = local')
    if 'only supported with the S3 storage providers' not in result.stderr + result.stdout:
        raise Failure(f'A10: unexpected error output\n{result.stderr[-2000:]}')
    log('A10: refused with an explicit message', level=' ok')


def assert_no_cassandra_running():
    """
    A Cassandra already listening on the storage port means another suite is using CCM. Say so,
    rather than letting ccm fail three frames deep with "Address already in use".
    """
    import socket
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', 7000)) == 0:
            raise Failure(
                'something is already listening on 127.0.0.1:7000 - another Cassandra or another '
                'test suite is running. Stop it (ccm stop) before running this protocol.'
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configurations', nargs='*', default=None,
                        help='configurations to run (default: all)')
    args = parser.parse_args()

    selected = args.configurations or list(CONFIGURATIONS)
    unknown = [c for c in selected if c not in CONFIGURATIONS]
    if unknown:
        parser.error(f'unknown configuration(s): {", ".join(unknown)}; '
                     f'known: {", ".join(CONFIGURATIONS)}')

    try:
        assert_no_cassandra_running()
    except Failure as failure:
        log(str(failure), level='!!!')
        return 1

    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / 'tmp').mkdir(exist_ok=True)

    results = {}
    started = time.time()
    try:
        refusal_check()
        results['A10 local+key refused'] = ('PASS', time.time() - started, '')
    except Failure as failure:
        results['A10 local+key refused'] = ('FAIL', time.time() - started, str(failure))
        log(f'A10: FAIL\n{failure}', level='!!!')

    for name in selected:
        started = time.time()
        try:
            run_configuration(name)
            results[name] = ('PASS', time.time() - started, '')
        except Failure as failure:
            results[name] = ('FAIL', time.time() - started, str(failure))
            log(f'Configuration {name}: FAIL\n{failure}', level='!!!')
        finally:
            # whatever happened, do not leave a Cassandra holding the ports the next run needs
            stop_cluster(SRC_CLUSTER)
            stop_cluster(DST_CLUSTER)

    log('')
    log('=' * 78)
    log('SUMMARY')
    for name, (verdict, elapsed, detail) in results.items():
        log(f'  {name:<24} {verdict:<5} {elapsed:6.0f}s')
        if detail:
            for line in detail.splitlines()[:6]:
                log(f'      {line}')
    log('=' * 78)

    return 0 if all(v == 'PASS' for v, _, _ in results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
