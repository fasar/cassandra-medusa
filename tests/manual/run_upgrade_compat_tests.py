# -*- coding: utf-8 -*-
# Copyright 2026 DataStax, Inc.
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
Upgrade compatibility protocol - see UPGRADE.md for what it checks and why.

Backups are taken by a Medusa built from master, which knows nothing about client-side encryption.
They are then listed, verified and restored by the Medusa of this branch, first without a key,
then the chain is continued by this branch, and finally encryption is turned on. Two checkouts,
two virtualenvs, one CCM cluster, one MinIO bucket.

    MASTER_REPO   checkout of master (default: ../cassandra-medusa-master, a git worktree)
    BRANCH_REPO   checkout of this branch (default: the repository this file lives in)
"""

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import time

BRANCH_REPO = pathlib.Path(os.environ.get('BRANCH_REPO', pathlib.Path(__file__).resolve().parents[2]))
MASTER_REPO = pathlib.Path(os.environ.get('MASTER_REPO', BRANCH_REPO.parent / 'cassandra-medusa-master'))
WORK = pathlib.Path(os.environ.get('UPGRADE_TEST_DIR', '/tmp/medusa-upgrade'))
CASSANDRA_VERSION = os.environ.get('CASSANDRA_VERSION', '4.1.9')
CCM_REPO = pathlib.Path.home() / '.ccm' / 'repository' / CASSANDRA_VERSION
CLUSTER = 'upgrade_src'
BUCKET = 'medusa-upgrade'
PREFIX = 'upgrade-prefix'
CSE_PREFIX = 'upgrade-prefix-cse'
KEY = base64.b64encode(os.urandom(32)).decode()
GCM_TAG_LENGTH = 16

# Every state has distinct row counts, so a restore that lands on the wrong backup is caught.
# ks_beta stops growing at B4 so that a selective restore stays visible.
STATES = {
    'B1': {'ks_alpha': 100, 'ks_beta': 50},     # full, master
    'B2': {'ks_alpha': 200, 'ks_beta': 80},     # differential, master
    'B3': {'ks_alpha': 300, 'ks_beta': 110},    # differential, master
    'B4': {'ks_alpha': 400, 'ks_beta': 140},    # full, master
    'B5': {'ks_alpha': 500, 'ks_beta': 140},    # differential, master
    'B6': {'ks_alpha': 600, 'ks_beta': 140},    # differential, this branch, no key
    'B7': {'ks_alpha': 700, 'ks_beta': 140},    # differential, this branch, key on
    'B8': {'ks_alpha': 800, 'ks_beta': 140},    # differential, this branch, key on
}
MASTER_BACKUPS = (
    ('B1', 'full'), ('B2', 'differential'), ('B3', 'differential'), ('B4', 'full'), ('B5', 'differential')
)


class Failure(Exception):
    pass


def log(message, level='   '):
    print(f'{time.strftime("%H:%M:%S")} {level} {message}', flush=True)


def step(name):
    log(name, level='>>>')


def run(cmd, check=True, cwd=BRANCH_REPO, quiet=False):
    if not quiet:
        log('$ ' + ' '.join(str(c) for c in cmd))
    result = subprocess.run(
        [str(c) for c in cmd], cwd=str(cwd), capture_output=True, text=True,
        env={**os.environ, 'PYTHONWARNINGS': 'ignore'},
    )
    if check and result.returncode != 0:
        raise Failure(
            'command failed ({}): {}\n--- stdout ---\n{}\n--- stderr ---\n{}'.format(
                result.returncode, ' '.join(str(c) for c in cmd), result.stdout[-4000:], result.stderr[-4000:]))
    return result


class Medusa:
    """One Medusa installation: a checkout and the console scripts of its poetry venv."""

    def __init__(self, name, repo):
        self.name = name
        self.repo = repo
        out = run(['poetry', 'env', 'info', '--path'], cwd=repo, quiet=True)
        self.bin = pathlib.Path(out.stdout.strip()) / 'bin'
        if not (self.bin / 'medusa').exists():
            raise Failure(f'{name}: no medusa console script in {self.bin}; run poetry install in {repo}')
        describe = run(['git', 'log', '-1', '--format=%h %s'], cwd=repo, quiet=True).stdout.strip()
        log(f'{name}: {repo} @ {describe}')

    def __call__(self, config_file, *args, check=True):
        return run([self.bin / 'medusa', '--config-file', config_file, *args], cwd=self.repo, check=check)


# --------------------------------------------------------------------------- CCM


def ccm_bin():
    return pathlib.Path(run(['poetry', 'env', 'info', '--path'], quiet=True).stdout.strip()) / 'bin' / 'ccm'


def recreate_cluster():
    step(f'Recreating CCM cluster {CLUSTER}')
    run([ccm_bin(), 'remove', CLUSTER], check=False, quiet=True)
    run([ccm_bin(), 'create', CLUSTER, '-v', f'binary:{CASSANDRA_VERSION}', '-n', '1'])
    conf = pathlib.Path.home() / '.ccm' / CLUSTER / 'node1' / 'conf'
    env_sh = conf / 'cassandra-env.sh'
    text = env_sh.read_text().replace('#MAX_HEAP_SIZE="4G"', 'MAX_HEAP_SIZE="512m"')
    env_sh.write_text(text.replace('#HEAP_NEWSIZE="800M"', 'HEAP_NEWSIZE="200M"'))
    jvm11 = conf / 'jvm11-server.options'
    if jvm11.exists():
        opts = jvm11.read_text().replace('-XX:+UseConcMarkSweepGC', '#-XX:+UseConcMarkSweepGC')
        jvm11.write_text(opts.replace('#-XX:+UseG1GC', '-XX:+UseG1GC'))
    run([ccm_bin(), 'switch', CLUSTER], quiet=True)
    run([ccm_bin(), 'start', '--wait-for-binary-proto'])


def stop_cluster():
    run([ccm_bin(), 'switch', CLUSTER], check=False, quiet=True)
    run([ccm_bin(), 'stop'], check=False, quiet=True)


def flush():
    run([ccm_bin(), 'node1', 'nodetool', 'flush'])


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
    sess.cluster.shutdown()


def table_of(ks):
    return 't_alpha' if ks == 'ks_alpha' else 't_beta'


def create_schema(sess):
    for ks in ('ks_alpha', 'ks_beta'):
        sess.execute(f"CREATE KEYSPACE IF NOT EXISTS {ks} WITH replication = "
                     "{'class':'SimpleStrategy','replication_factor':1}")
        sess.execute(f'CREATE TABLE IF NOT EXISTS {ks}.{table_of(ks)} (id int PRIMARY KEY, value text)')
    time.sleep(2)


def count_rows(sess, ks):
    return sess.execute(f'SELECT COUNT(*) FROM {ks}.{table_of(ks)}').one()[0]


def seed_up_to(sess, counts):
    for ks, target in counts.items():
        for i in range(count_rows(sess, ks), target):
            sess.execute(f"INSERT INTO {ks}.{table_of(ks)} (id, value) VALUES ({i}, 'row-{i}')")
    flush()


def assert_counts(sess, expected, label):
    actual = {ks: count_rows(sess, ks) for ks in expected}
    if actual != expected:
        raise Failure(f'{label}: expected {expected}, got {actual}')
    log(f'{label}: {actual} as expected', level=' ok')


def assert_values_intact(sess, label):
    """Row counts prove which backup was restored; a few values prove the bytes came back right."""
    for ks in ('ks_alpha', 'ks_beta'):
        for i in (0, 7, 42):
            row = sess.execute(f'SELECT value FROM {ks}.{table_of(ks)} WHERE id = {i}').one()
            if row is None or row[0] != f'row-{i}':
                raise Failure(f'{label}: {ks} row {i} is {row}')
    log(f'{label}: sampled values intact', level=' ok')


# --------------------------------------------------------------------------- config and storage


def write_config(name, encrypted, prefix=PREFIX):
    conf = pathlib.Path.home() / '.ccm' / CLUSTER / 'node1' / 'conf' / 'cassandra.yaml'
    lines = [
        '[cassandra]',
        'is_ccm = 1',
        'stop_cmd = ccm stop',
        'start_cmd = ccm start',
        'cql_username = cassandra',
        'cql_password = cassandra',
        f'config_file = {conf}',
        f'sstableloader_bin = {CCM_REPO / "bin" / "sstableloader"}',
        'nodetool_flags = -Dcom.sun.jndi.rmiURLParsing=legacy',
        'nodetool_port = 7100',
        'resolve_ip_addresses = False',
        'use_sudo = false',
        '',
        '[storage]',
        'storage_provider = s3_compatible',
        f'bucket_name = {BUCKET}',
        'key_file = ~/.aws/minio_credentials',
        'api_profile = default',
        'host = 127.0.0.1',
        'port = 9000',
        'secure = False',
        'region = default',
        'base_path = /tmp',
        'use_sudo_for_restore = false',
        'host_file_separator = ,',
        'fqdn = 127.0.0.1',
        f'prefix = {prefix}',
        'backup_grace_period_in_days = 0',
        'concurrent_transfers = 4',
        'multi_part_upload_threshold = 1024',
    ]
    if encrypted:
        lines.append(f'key_secret_base64 = {KEY}')
    lines += ['', '[monitoring]', 'monitoring_provider = local', '']
    path = WORK / f'medusa-{name}.ini'
    path.write_text('\n'.join(lines))
    return path


def s3():
    import boto3
    import configparser
    creds = configparser.ConfigParser()
    creds.read(pathlib.Path.home() / '.aws' / 'minio_credentials')
    return boto3.client('s3', endpoint_url='http://127.0.0.1:9000',
                        aws_access_key_id=creds['default']['aws_access_key_id'],
                        aws_secret_access_key=creds['default']['aws_secret_access_key'])


def prepare_bucket():
    import botocore.exceptions
    client = s3()
    try:
        client.head_bucket(Bucket=BUCKET)
    except botocore.exceptions.ClientError:
        client.create_bucket(Bucket=BUCKET)
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
        keys = [{'Key': o['Key']} for o in page.get('Contents', [])]
        if keys:
            client.delete_objects(Bucket=BUCKET, Delete={'Objects': keys})


def data_objects(prefix=PREFIX):
    """{key: (size, has S3EC metadata)} for every SSTable object under the prefix."""
    client = s3()
    objects = {}
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=prefix + '/'):
        for o in page.get('Contents', []):
            if '/data/' in o['Key']:
                head = client.head_object(Bucket=BUCKET, Key=o['Key'])
                objects[o['Key']] = (o['Size'], 'x-amz-3' in head['Metadata'])
    return objects


def manifest_of(backup_name, prefix=PREFIX):
    client = s3()
    body = client.get_object(Bucket=BUCKET, Key=f'{prefix}/127.0.0.1/{backup_name}/meta/manifest.json')['Body'].read()
    return [o for section in json.loads(body) for o in section['objects']]


# --------------------------------------------------------------------------- the protocol


def phase_master(master, sess, plain_cfg):
    step('Phase M - backups taken by master, which has no client-side encryption')
    for name, mode in MASTER_BACKUPS:
        seed_up_to(sess, STATES[name])
        assert_counts(sess, STATES[name], f'state before {name}')
        master(plain_cfg, 'backup', '--backup-name', name, '--mode', mode, '--enable-md5-checks')
    listing = master(plain_cfg, 'list-backups').stdout
    for name, _ in MASTER_BACKUPS:
        if name not in listing:
            raise Failure(f'M: {name} missing from master list-backups')
    log('M: five backups taken by master (2 full, 3 differential)', level=' ok')
    objects = data_objects()
    encrypted = [k for k, (_, enc) in objects.items() if enc]
    if encrypted:
        raise Failure(f'M: master wrote objects with encryption metadata: {encrypted[:3]}')
    log(f'M: {len(objects)} SSTable objects, none carries encryption metadata', level=' ok')


def phase_read(branch, sess, plain_cfg):
    step('Phase R - the branch, without a key, reads and restores what master wrote')
    listing = branch(plain_cfg, 'list-backups').stdout
    for name, _ in MASTER_BACKUPS:
        if name not in listing:
            raise Failure(f'R1: {name} missing from branch list-backups\n{listing}')
    log('R1: the five master backups are listed by the branch', level=' ok')

    for name, _ in MASTER_BACKUPS:
        branch(plain_cfg, 'verify', '--backup-name', name, '--enable-md5-checks')
    log('R2: the five master backups verify on the branch', level=' ok')

    for name in ('B1', 'B3', 'B5', 'B4', 'B2'):
        close(sess)
        branch(plain_cfg, 'restore-node', '--backup-name', name, '--temp-dir', str(WORK / 'tmp'))
        sess = session()
        assert_counts(sess, STATES[name], f'R3 after restoring {name} with the branch')
        assert_values_intact(sess, f'R3 {name}')
    log('R3: full and differential master backups restore on the branch, in any order', level=' ok')
    return sess


def phase_continue(branch, sess, plain_cfg):
    step('Phase C - the branch continues the chain without a key')
    close(sess)
    branch(plain_cfg, 'restore-node', '--backup-name', 'B5', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    before = data_objects()
    seed_up_to(sess, STATES['B6'])
    branch(plain_cfg, 'backup', '--backup-name', 'B6', '--mode', 'differential', '--enable-md5-checks')
    after = data_objects()
    new_objects = set(after) - set(before)
    manifest = manifest_of('B6')
    reused = [o['path'] for o in manifest if o['path'].split('/')[-1] not in {k.split('/')[-1] for k in new_objects}]
    if not reused:
        raise Failure('C1: the differential backup reused nothing from the master chain')
    if any('source_MD5' in o for o in manifest):
        raise Failure('C1: source_MD5 written into a manifest without a key')
    log(f'C1: B6 differential on the branch: {len(new_objects)} objects uploaded, {len(reused)} reused '
        f'from master backups, no source_MD5 in the manifest', level=' ok')
    branch(plain_cfg, 'verify', '--backup-name', 'B6', '--enable-md5-checks')
    log('C2: B6 verifies', level=' ok')

    close(sess)
    branch(plain_cfg, 'restore-node', '--backup-name', 'B6', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    assert_counts(sess, STATES['B6'], 'C3 after restoring B6')
    assert_values_intact(sess, 'C3 B6')

    close(sess)
    branch(plain_cfg, 'restore-node', '--backup-name', 'B3', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    assert_counts(sess, STATES['B3'], 'C4 after restoring B3 again, an older master backup')
    return sess


def phase_encrypt(branch, sess, plain_cfg, cse_same_prefix_cfg, cse_cfg):
    step('Phase E - encryption turned on, on top of the master chain')
    close(sess)
    branch(plain_cfg, 'restore-node', '--backup-name', 'B6', '--temp-dir', str(WORK / 'tmp'))
    sess = session()

    listing = branch(cse_same_prefix_cfg, 'list-backups').stdout
    for name in ('B1', 'B3', 'B5', 'B6'):
        if name not in listing:
            raise Failure(f'E1: {name} missing from list-backups with a key\n{listing}')
    for name in ('B1', 'B5', 'B6'):
        branch(cse_same_prefix_cfg, 'verify', '--backup-name', name, '--enable-md5-checks')
    log('E1: with a key configured, the plaintext backups are still listed and verify', level=' ok')

    # E2 - the key on the SAME prefix must be refused: the differential layout stores SSTables by
    # name under a shared data/ prefix, so the first encrypted backup would overwrite the plaintext
    # objects every earlier backup references
    seed_up_to(sess, STATES['B7'])
    before = data_objects()
    result = branch(cse_same_prefix_cfg, 'backup', '--backup-name', 'B7', '--mode', 'differential',
                    '--enable-md5-checks', check=False)
    output = result.stdout + result.stderr
    if result.returncode == 0:
        raise Failure('E2: an encrypted backup on the plaintext prefix succeeded; it must refuse to overwrite')
    if 'new prefix' not in output:
        raise Failure(f'E2: refused, but without the expected message\n{output[-3000:]}')
    after = data_objects()
    changed = [k for k in before if k in after and after[k] != before[k]]
    if changed:
        raise Failure(f'E2: plaintext objects were overwritten before the refusal: {changed[:3]}')
    for name in ('B5', 'B6'):
        branch(plain_cfg, 'verify', '--backup-name', name, '--enable-md5-checks')
    log(f'E2: an encrypted backup on the plaintext prefix is refused, none of the {len(before)} plaintext objects '
        'was touched, B5 and B6 still verify', level=' ok')

    # E3 - the documented procedure: a new prefix for the encrypted chain
    branch(cse_cfg, 'backup', '--backup-name', 'B7', '--mode', 'differential', '--enable-md5-checks')
    objects = data_objects(CSE_PREFIX)
    manifest = manifest_of('B7', CSE_PREFIX)
    if len(objects) != len(manifest):
        raise Failure(f'E3: B7 manifest has {len(manifest)} objects but {len(objects)} are under {CSE_PREFIX}')
    not_encrypted = [k for k, (_, enc) in objects.items() if not enc]
    if not_encrypted:
        raise Failure(f'E3: objects uploaded without encryption metadata: {not_encrypted[:3]}')
    if any('source_MD5' not in o for o in manifest):
        raise Failure('E3: an object of the encrypted differential has no source_MD5')
    wrong_size = [o['path'] for o in manifest if o['size'] != o['source_size'] + GCM_TAG_LENGTH]
    if wrong_size:
        raise Failure(f'E3: objects whose size is not source_size + {GCM_TAG_LENGTH}: {wrong_size[:3]}')
    log(f'E3: B7 under the new prefix: all {len(manifest)} objects uploaded and encrypted, every one '
        f'source_size + {GCM_TAG_LENGTH} long, source_MD5 everywhere', level=' ok')

    seed_up_to(sess, STATES['B8'])
    before = data_objects(CSE_PREFIX)
    branch(cse_cfg, 'backup', '--backup-name', 'B8', '--mode', 'differential', '--enable-md5-checks')
    after = data_objects(CSE_PREFIX)
    uploaded = len(set(after) - set(before))
    manifest = manifest_of('B8', CSE_PREFIX)
    if uploaded >= len(manifest):
        raise Failure(f'E4: B8 reused nothing from B7 ({uploaded} uploaded for {len(manifest)} objects)')
    log(f'E4: B8, second encrypted differential: {uploaded} uploaded, {len(manifest) - uploaded} reused from B7 '
        'through source_MD5', level=' ok')
    for name in ('B7', 'B8'):
        branch(cse_cfg, 'verify', '--backup-name', name, '--enable-md5-checks')
    log('E5: B7 and B8 verify', level=' ok')

    close(sess)
    branch(cse_cfg, 'restore-node', '--backup-name', 'B8', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    assert_counts(sess, STATES['B8'], 'E6 after restoring B8 with the key')
    assert_values_intact(sess, 'E6 B8')

    close(sess)
    branch(cse_cfg, 'restore-node', '--backup-name', 'B7', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    assert_counts(sess, STATES['B7'], 'E6 after restoring B7 with the key')

    # E7 - an old plaintext backup, with the key still configured on the old prefix: refused
    close(sess)
    result = branch(cse_same_prefix_cfg, 'restore-node', '--backup-name', 'B5', '--temp-dir', str(WORK / 'tmp'),
                    check=False)
    output = result.stdout + result.stderr
    if result.returncode == 0:
        raise Failure('E7: restoring a plaintext backup with a key configured succeeded; it must refuse')
    if 'uploaded before encryption was enabled' not in output:
        raise Failure(f'E7: refused, but without the expected message\n{output[-3000:]}')
    log('E7: restoring master\'s B5 with the key is refused, and the message says to drop the key', level=' ok')

    # Cassandra was stopped by the refused restore; the plain config brings B5 back
    branch(plain_cfg, 'restore-node', '--backup-name', 'B5', '--temp-dir', str(WORK / 'tmp'))
    sess = session()
    assert_counts(sess, STATES['B5'], 'E8 after restoring B5 without the key')
    assert_values_intact(sess, 'E8 B5')
    log('E8: the same backup restores once the key is removed from the configuration', level=' ok')
    return sess


def assert_no_cassandra_running():
    import socket
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(('127.0.0.1', 7000)) == 0:
            raise Failure('something is already listening on 127.0.0.1:7000 - stop it (ccm stop) first')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    try:
        assert_no_cassandra_running()
        master = Medusa('master', MASTER_REPO)
        branch = Medusa('branch', BRANCH_REPO)
    except Failure as failure:
        log(str(failure), level='!!!')
        return 1

    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / 'tmp').mkdir(exist_ok=True)
    prepare_bucket()
    plain_cfg = write_config('plain', encrypted=False)
    cse_same_prefix_cfg = write_config('cse-same-prefix', encrypted=True)
    cse_cfg = write_config('cse', encrypted=True, prefix=CSE_PREFIX)

    started = time.time()
    sess = None
    verdict = 'PASS'
    try:
        recreate_cluster()
        sess = session()
        create_schema(sess)
        phase_master(master, sess, plain_cfg)
        sess = phase_read(branch, sess, plain_cfg)
        sess = phase_continue(branch, sess, plain_cfg)
        sess = phase_encrypt(branch, sess, plain_cfg, cse_same_prefix_cfg, cse_cfg)
    except Failure as failure:
        verdict = 'FAIL'
        log(f'FAIL\n{failure}', level='!!!')
    finally:
        if sess is not None:
            try:
                close(sess)
            except Exception:                                           # noqa: BLE001 - best effort
                pass
        stop_cluster()

    log('=' * 78)
    log(f'UPGRADE COMPATIBILITY: {verdict} in {time.time() - started:.0f}s', level='===')
    log('=' * 78)
    return 0 if verdict == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
