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
Backup and restore of large SSTables, encrypted and plaintext - see LARGE-SSTABLES.md.

Writes about a gigabyte with cassandra-stress, switches the table to leveled compaction with
sstable_size_in_mb=300 and compacts, so that the data directory holds a few SSTables of ~300 MB.
Then, for the plaintext and the encrypted configuration: full backup under /usr/bin/time -v, verify,
inspection of the objects in the bucket, restore-node, and a byte-for-byte comparison of every
SSTable component with the original, plus a row count.

    --rows N                 rows written by cassandra-stress (default 4000000, ~1 GB)
    --sstable-mb N           target SSTable size (default 300)
    --multipart-chunksize S  multipart_chunksize for both configurations (default: Medusa's 50MB)
    --only plain|cse         run a single configuration
    LARGE_TEST_DIR, CASSANDRA_VERSION as in run_benchmark.py; MinIO at 127.0.0.1:9000
"""

import argparse
import base64
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
WORK = pathlib.Path(os.environ.get('LARGE_TEST_DIR', '/tmp/medusa-large'))
CASSANDRA_VERSION = os.environ.get('CASSANDRA_VERSION', '4.1.9')
CCM_HOME = pathlib.Path.home() / '.ccm'
CCM_REPO = CCM_HOME / 'repository' / CASSANDRA_VERSION
CLUSTER = 'large_src'
BUCKET = 'medusa-large'
KEY = base64.b64encode(os.urandom(32)).decode()
MB = 1024 * 1024
GCM_TAG_LENGTH = 16


class Failure(Exception):
    pass


def log(message, level='   '):
    print(f'{time.strftime("%H:%M:%S")} {level} {message}', flush=True)


def step(name):
    log(name, level='>>>')


def venv_bin(name):
    if not hasattr(venv_bin, 'root'):
        out = subprocess.run(['poetry', 'env', 'info', '--path'], cwd=str(REPO), capture_output=True, text=True,
                             check=True)
        venv_bin.root = pathlib.Path(out.stdout.strip()) / 'bin'
    return str(venv_bin.root / name)


def run(cmd, check=True, quiet=False, timeout=None):
    if not quiet:
        log('$ ' + ' '.join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(REPO), capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'PYTHONWARNINGS': 'ignore'})
    if check and result.returncode != 0:
        raise Failure('failed: {}\n{}\n{}'.format(' '.join(str(c) for c in cmd), result.stdout[-2000:],
                                                  result.stderr[-3000:]))
    return result


def measure(label, cmd):
    """Run a command under /usr/bin/time -v; return wall, cpu and peak RSS."""
    report = WORK / f'{label}.time'
    log(f'measuring {label}: ' + ' '.join(str(c) for c in cmd))
    result = subprocess.run(['/usr/bin/time', '-v', '-o', str(report), *[str(c) for c in cmd]], cwd=str(REPO),
                            capture_output=True, text=True, env={**os.environ, 'PYTHONWARNINGS': 'ignore'})
    (WORK / f'{label}.out').write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        raise Failure(f'{label} failed:\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}')
    text = report.read_text()
    wall_text = re.search(r'Elapsed \(wall clock\) time.*?:\s*([\d:.]+)', text).group(1)
    parts = [float(p) for p in wall_text.split(':')]
    wall = parts[-1] + (parts[-2] * 60 if len(parts) > 1 else 0) + (parts[-3] * 3600 if len(parts) > 2 else 0)
    user = float(re.search(r'User time \(seconds\):\s*([\d.]+)', text).group(1))
    system = float(re.search(r'System time \(seconds\):\s*([\d.]+)', text).group(1))
    rss = int(re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)', text).group(1)) / 1024
    sample = {'wall': wall, 'cpu': user + system, 'rss_mb': rss}
    log(f'{label}: wall={wall:.1f}s cpu={user + system:.1f}s rss={rss:.0f}MB', level=' ok')
    return sample


# --------------------------------------------------------------------------- cluster


def recreate_cluster():
    step(f'Recreating CCM cluster {CLUSTER}')
    run([venv_bin('ccm'), 'remove', CLUSTER], check=False, quiet=True)
    run([venv_bin('ccm'), 'create', CLUSTER, '-v', f'binary:{CASSANDRA_VERSION}', '-n', '1'])
    conf = CCM_HOME / CLUSTER / 'node1' / 'conf'
    env_sh = conf / 'cassandra-env.sh'
    text = env_sh.read_text().replace('#MAX_HEAP_SIZE="4G"', 'MAX_HEAP_SIZE="2G"')
    env_sh.write_text(text.replace('#HEAP_NEWSIZE="800M"', 'HEAP_NEWSIZE="600M"'))
    jvm11 = conf / 'jvm11-server.options'
    if jvm11.exists():
        opts = jvm11.read_text().replace('-XX:+UseConcMarkSweepGC', '#-XX:+UseConcMarkSweepGC')
        jvm11.write_text(opts.replace('#-XX:+UseG1GC', '-XX:+UseG1GC'))
    run([venv_bin('ccm'), 'switch', CLUSTER], quiet=True)
    run([venv_bin('ccm'), 'start', '--wait-for-binary-proto'])


def stop_cluster():
    run([venv_bin('ccm'), 'switch', CLUSTER], check=False, quiet=True)
    run([venv_bin('ccm'), 'stop'], check=False, quiet=True)


def nodetool(*args, check=True):
    return run([venv_bin('ccm'), 'node1', 'nodetool', *args], check=check, quiet=True)


def wait_for_compactions():
    for _ in range(240):
        if 'pending tasks: 0' in nodetool('compactionstats').stdout:
            return
        time.sleep(5)
    raise Failure('compactions still running after 20 minutes')


def stress(rows):
    run([CCM_REPO / 'tools' / 'bin' / 'cassandra-stress', 'write', f'n={rows}', '-rate', 'threads=50',
         '-node', '127.0.0.1', '-schema', 'replication(strategy=SimpleStrategy,replication_factor=1)'],
        timeout=3600)


def cql(statement):
    run([CCM_REPO / 'bin' / 'cqlsh', '127.0.0.1', '--request-timeout=600', '-e', statement], timeout=900)


def table_dir():
    return next((CCM_HOME / CLUSTER / 'node1' / 'data0' / 'keyspace1').glob('standard1-*'))


def sstable_files():
    """{name: path} of the live SSTable components of keyspace1.standard1 (no snapshots, no backups)."""
    return {p.name: p for p in table_dir().iterdir() if p.is_file() and p.suffix in ('.db', '.txt', '.crc32')}


def md5_of(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(4 * MB), b''):
            h.update(block)
    return h.hexdigest()


def cql_session():
    from cassandra.cluster import Cluster
    from cassandra import ProtocolVersion
    return Cluster(contact_points=['127.0.0.1'], protocol_version=ProtocolVersion.V4).connect()


def sample_rows(count=200):
    """{key: C0} for a sample of rows: a full COUNT(*) on millions of rows hits the server timeout."""
    session = cql_session()
    try:
        return {row.key: row.C0 for row in session.execute(f'SELECT key, "C0" FROM keyspace1.standard1 LIMIT {count}')}
    finally:
        session.cluster.shutdown()


def check_rows(expected):
    session = cql_session()
    try:
        statement = session.prepare('SELECT "C0" FROM keyspace1.standard1 WHERE key = ?')
        for key, value in expected.items():
            row = session.execute(statement, [key]).one()
            if row is None or row.C0 != value:
                raise Failure(f'row {key.hex()} is {row} after restore')
    finally:
        session.cluster.shutdown()


def partition_estimate():
    out = nodetool('tablestats', 'keyspace1.standard1').stdout
    match = re.search(r'Number of partitions \(estimate\):\s*(\d+)', out)
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------- storage


def write_config(name, encrypted, prefix, chunksize=None):
    lines = [
        '[cassandra]',
        'is_ccm = 1',
        'stop_cmd = ccm stop',
        'start_cmd = ccm start',
        'cql_username = cassandra',
        'cql_password = cassandra',
        f'config_file = {CCM_HOME / CLUSTER / "node1" / "conf" / "cassandra.yaml"}',
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
        'transfer_max_bandwidth = ',
    ]
    if chunksize:
        lines.append(f'multipart_chunksize = {chunksize}')
    if encrypted:
        lines.append(f'key_secret_base64 = {KEY}')
    lines += ['', '[checks]', 'enable_md5_checks = True', '', '[monitoring]', 'monitoring_provider = local', '']
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


def objects_of(prefix, backup_name):
    """{file name: (size, etag, encrypted)} for the standard1 objects of a backup."""
    client = s3()
    out = {}
    data_prefix = f'{prefix}/127.0.0.1/{backup_name}/data/keyspace1/'
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=data_prefix):
        for o in page.get('Contents', []):
            head = client.head_object(Bucket=BUCKET, Key=o['Key'])
            out[o['Key'].split('/')[-1]] = (o['Size'], o['ETag'].strip('"'), 'x-amz-3' in head['Metadata'])
    return out


# --------------------------------------------------------------------------- the test


def prepare_data(rows, sstable_mb):
    step(f'Writing {rows} rows with cassandra-stress')
    stress(rows)
    nodetool('flush')
    step(f'Switching keyspace1.standard1 to leveled compaction, sstable_size_in_mb = {sstable_mb}')
    cql("ALTER TABLE keyspace1.standard1 WITH compaction = "
        f"{{'class': 'LeveledCompactionStrategy', 'sstable_size_in_mb': {sstable_mb}}}")
    nodetool('compact', 'keyspace1', 'standard1')
    wait_for_compactions()
    nodetool('disableautocompaction', 'keyspace1', 'standard1')
    data_files = sorted((p for p in sstable_files().values() if p.name.endswith('-Data.db')),
                        key=lambda p: p.stat().st_size)
    sizes = [p.stat().st_size / MB for p in data_files]
    log(f'{len(data_files)} Data.db files: {", ".join(f"{s:.0f} MB" for s in sizes)}', level='===')
    big = [s for s in sizes if s >= sstable_mb * 0.8]
    if not big:
        raise Failure(f'no SSTable of about {sstable_mb} MB was produced: {sizes}')
    return sizes


def run_configuration(name, cfg, prefix, encrypted, originals, sample):
    step(f'Configuration {name} - client-side encryption {"on" if encrypted else "off"}')
    results = {}

    backup_name = f'large_{name}'
    results['backup'] = measure(f'{name}-backup', [venv_bin('medusa'), '--config-file', cfg, 'backup',
                                                   '--backup-name', backup_name, '--mode', 'full'])
    results['verify'] = measure(f'{name}-verify', [venv_bin('medusa'), '--config-file', cfg, 'verify',
                                                   '--backup-name', backup_name, '--enable-md5-checks'])

    # the objects in the bucket, against the local files
    objects = objects_of(prefix, backup_name)
    missing = [n for n in originals if n not in objects]
    if missing:
        raise Failure(f'{name}: objects missing from the bucket: {missing}')
    for file_name, (size, etag, is_encrypted) in objects.items():
        local_size = originals[file_name]['size']
        expected = local_size + GCM_TAG_LENGTH if encrypted else local_size
        if size != expected:
            raise Failure(f'{name}: {file_name} is {size} bytes in the bucket, expected {expected}')
        if is_encrypted != encrypted:
            raise Failure(f'{name}: {file_name} encryption metadata present={is_encrypted}, expected {encrypted}')
    big_objects = {n: v for n, v in objects.items() if n.endswith('-Data.db') and v[0] > 8 * MB}
    multipart = [n for n, (_, etag, _) in big_objects.items() if '-' in etag]
    log(f'{len(objects)} objects checked: every size is local{" + 16" if encrypted else ""}, encryption metadata '
        f'{"present" if encrypted else "absent"} on all; {len(multipart)}/{len(big_objects)} large Data.db uploaded '
        f'as multipart', level=' ok')
    results['objects'] = len(objects)
    results['data_db'] = {n: v[0] for n, v in big_objects.items()}

    # restore in place, then compare every SSTable component byte for byte
    results['restore'] = measure(f'{name}-restore', [venv_bin('medusa'), '--config-file', cfg, 'restore-node',
                                                     '--backup-name', backup_name, '--temp-dir', str(WORK / 'tmp')])
    restored = sstable_files()
    for file_name, original in originals.items():
        if file_name not in restored:
            raise Failure(f'{name}: {file_name} missing after restore')
        actual = md5_of(restored[file_name])
        if actual != original['md5']:
            raise Failure(f'{name}: {file_name} differs after restore ({actual} != {original["md5"]})')
    log(f'{len(originals)} SSTable components identical to the originals after restore (md5)', level=' ok')

    check_rows(sample)
    estimate = partition_estimate()
    log(f'{len(sample)} sampled rows read back by key with their values; ~{estimate} partitions', level=' ok')
    results['partitions_estimate'] = estimate
    return results


def report(sizes, all_results, rows, args):
    print('\n' + '=' * 78)
    chunk = f', multipart_chunksize {args.multipart_chunksize}' if args.multipart_chunksize else ''
    described = ', '.join(f'{s:.0f} MB' for s in sizes)
    print(f'{len(sizes)} SSTables: {described} ({rows} rows, {sum(sizes):.0f} MB){chunk}')
    print('=' * 78)
    print(f'{"":<10} {"backup wall":>12} {"backup cpu":>11} {"backup rss":>11} {"verify":>8} '
          f'{"restore wall":>13} {"restore rss":>12}')
    for name, r in all_results.items():
        print(f'{name:<10} {r["backup"]["wall"]:>11.1f}s {r["backup"]["cpu"]:>10.1f}s {r["backup"]["rss_mb"]:>9.0f}MB '
              f'{r["verify"]["wall"]:>7.1f}s {r["restore"]["wall"]:>12.1f}s {r["restore"]["rss_mb"]:>10.0f}MB')
    plain, cse = all_results.get('plain'), all_results.get('cse')
    if plain and cse:
        gib = sum(sizes) / 1024
        print(f'\nencryption cost: {(cse["backup"]["cpu"] - plain["backup"]["cpu"]) / gib:.2f} CPU-s/GiB, '
              f'wall x{cse["backup"]["wall"] / plain["backup"]["wall"]:.2f}, '
              f'+{cse["backup"]["rss_mb"] - plain["backup"]["rss_mb"]:.0f} MB peak RSS on backup, '
              f'restore wall x{cse["restore"]["wall"] / plain["restore"]["wall"]:.2f}')
    print('=' * 78)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rows', type=int, default=4000000)
    parser.add_argument('--sstable-mb', type=int, default=300)
    parser.add_argument('--only', choices=('plain', 'cse'), default=None)
    parser.add_argument('--multipart-chunksize', default=None)
    args = parser.parse_args()

    if shutil.which('/usr/bin/time') is None:
        sys.exit('/usr/bin/time is missing')
    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / 'tmp').mkdir(parents=True)
    prepare_bucket()

    verdict = 'PASS'
    all_results = {}
    try:
        recreate_cluster()
        sizes = prepare_data(args.rows, args.sstable_mb)
        originals = {n: {'size': p.stat().st_size, 'md5': md5_of(p)} for n, p in sstable_files().items()}
        sample = sample_rows()
        log(f'{len(originals)} SSTable components hashed and {len(sample)} rows sampled before backup', level=' ok')
        configs = [('plain', False), ('cse', True)]
        if args.only:
            configs = [c for c in configs if c[0] == args.only]
        for name, encrypted in configs:
            cfg = write_config(name, encrypted, f'large_{name}', args.multipart_chunksize)
            all_results[name] = run_configuration(name, cfg, f'large_{name}', encrypted, originals, sample)
        report(sizes, all_results, args.rows, args)
        (WORK / 'results.json').write_text(json.dumps({'sstables_mb': sizes, 'results': all_results}, indent=1))
    except Failure as failure:
        verdict = 'FAIL'
        log(f'FAIL\n{failure}', level='!!!')
    finally:
        stop_cluster()
    log(f'LARGE SSTABLES: {verdict}', level='===')
    return 0 if verdict == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
