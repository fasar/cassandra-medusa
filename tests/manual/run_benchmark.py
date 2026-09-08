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
Runs BENCHMARK.md against a single-node CCM cluster and prints its tables filled in.

BENCHMARK.md is the reference: it is meant to be executed by hand on a real multi-node cluster. This
script performs the same measurements on one node so the protocol itself can be validated and a
first set of figures produced. Same commands, same names, same calculations.

The storage is a MinIO at 127.0.0.1:9000 (credentials in ~/.aws/minio_credentials), because
client-side encryption exists for the S3 providers only. --provider local is kept for the plaintext
half and for comparison with implementations that encrypt on every provider.
"""

import argparse
import base64
import json
import os
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
WORK = pathlib.Path(os.environ.get('BENCH_DIR', '/tmp/medusa-bench'))
CASSANDRA_VERSION = os.environ.get('CASSANDRA_VERSION', '4.1.9')
CCM_HOME = pathlib.Path.home() / '.ccm'
CCM_REPO = CCM_HOME / 'repository' / CASSANDRA_VERSION
CLUSTER = 'bench'
KEY = base64.b64encode(os.urandom(32)).decode()
MINIO_ENDPOINT = 'http://127.0.0.1:9000'
MINIO_BUCKET = 'medusa-bench'
GIB = 1024 ** 3
MIB = 1024 ** 2


def log(message, level='   '):
    print(f'{time.strftime("%H:%M:%S")} {level} {message}', flush=True)


def venv_bin(name):
    if not hasattr(venv_bin, 'root'):
        out = subprocess.run(['poetry', 'env', 'info', '--path'], cwd=str(REPO),
                             capture_output=True, text=True, check=True)
        venv_bin.root = pathlib.Path(out.stdout.strip()) / 'bin'
    return str(venv_bin.root / name)


def run(cmd, check=True, quiet=False, timeout=None):
    if not quiet:
        log('$ ' + ' '.join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=str(REPO), capture_output=True,
                            text=True, timeout=timeout,
                            env={**os.environ, 'PYTHONWARNINGS': 'ignore'})
    if check and result.returncode != 0:
        raise RuntimeError('failed: {}\n{}\n{}'.format(
            ' '.join(str(c) for c in cmd), result.stdout[-2000:], result.stderr[-3000:]))
    return result


def measure(label, cmd):
    """Run a command under /usr/bin/time -v and return the four numbers BENCHMARK.md asks for."""
    report = WORK / f'bench-{label}.txt'
    log(f'measuring {label}: ' + ' '.join(str(c) for c in cmd))
    started = time.time()
    if os.environ.get('BENCH_DEBUG'):
        cmd = [cmd[0], '-vv', *cmd[1:]]   # medusa --config-file ... -> debug logging with timestamps
    result = subprocess.run(
        ['/usr/bin/time', '-v', '-o', str(report), *[str(c) for c in cmd]],
        cwd=str(REPO), capture_output=True, text=True,
        env={**os.environ, 'PYTHONWARNINGS': 'ignore'},
    )
    # keep medusa's own output next to the measurement: it is what explains a surprising figure
    (WORK / f'bench-{label}.out').write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f'{label} failed:\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}')

    text = report.read_text()

    def field(pattern, cast=float):
        match = re.search(pattern, text)
        if not match:
            raise RuntimeError(f'{label}: could not read "{pattern}" from {report}')
        return cast(match.group(1))

    wall_text = re.search(r'Elapsed \(wall clock\) time.*?:\s*([\d:.]+)', text).group(1)
    parts = [float(p) for p in wall_text.split(':')]
    wall = parts[-1]
    if len(parts) > 1:
        wall += parts[-2] * 60
    if len(parts) > 2:
        wall += parts[-3] * 3600

    sample = {
        'wall': wall,
        'user': field(r'User time \(seconds\):\s*([\d.]+)'),
        'sys': field(r'System time \(seconds\):\s*([\d.]+)'),
        'rss_mb': field(r'Maximum resident set size \(kbytes\):\s*(\d+)') / 1024,
        'observed': time.time() - started,
    }
    sample['cpu'] = sample['user'] + sample['sys']
    log(f"{label}: wall={sample['wall']:.1f}s cpu={sample['cpu']:.1f}s "
        f"(user={sample['user']:.1f} sys={sample['sys']:.1f}) rss={sample['rss_mb']:.0f}MB", level=' ok')
    return sample


# --------------------------------------------------------------------------- cluster


def recreate_cluster(heap='2G'):
    log('Recreating the CCM cluster', level='>>>')
    run([venv_bin('ccm'), 'remove', CLUSTER], check=False, quiet=True)
    run([venv_bin('ccm'), 'create', CLUSTER, '-v', f'binary:{CASSANDRA_VERSION}', '-n', '1'])
    conf = CCM_HOME / CLUSTER / 'node1' / 'conf'
    env_sh = conf / 'cassandra-env.sh'
    text = env_sh.read_text()
    text = text.replace('#MAX_HEAP_SIZE="4G"', f'MAX_HEAP_SIZE="{heap}"')
    text = text.replace('#HEAP_NEWSIZE="800M"', 'HEAP_NEWSIZE="600M"')
    env_sh.write_text(text)
    jvm11 = conf / 'jvm11-server.options'
    if jvm11.exists():
        opts = jvm11.read_text().replace('-XX:+UseConcMarkSweepGC', '#-XX:+UseConcMarkSweepGC')
        jvm11.write_text(opts.replace('#-XX:+UseG1GC', '-XX:+UseG1GC'))
    run([venv_bin('ccm'), 'start', '--wait-for-binary-proto'])


def nodetool(*args, check=True):
    return run([venv_bin('ccm'), 'node1', 'nodetool', *args], check=check, quiet=True)


def wait_for_compactions():
    """The CPU column measures Medusa only if Cassandra is not compacting underneath it."""
    for _ in range(120):
        out = nodetool('compactionstats').stdout
        if 'pending tasks: 0' in out:
            return
        time.sleep(5)
    log('compactions still running after 10 minutes, measuring anyway', level='!!!')


def data_dir():
    return CCM_HOME / CLUSTER / 'node1' / 'data0'


def dataset_bytes():
    total = 0
    for keyspace in ('keyspace1',):
        path = data_dir() / keyspace
        if path.exists():
            total += sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    return total


def stress(rows, threads=50):
    binary = CCM_REPO / 'tools' / 'bin' / 'cassandra-stress'
    run([binary, 'write', f'n={rows}', '-rate', f'threads={threads}',
         '-node', '127.0.0.1',
         '-schema', 'replication(strategy=SimpleStrategy,replication_factor=1)'],
        timeout=3600)


# --------------------------------------------------------------------------- config


def write_config(name, encrypted, prefix, provider, max_bandwidth=None, chunksize=None):
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
            'bucket_name = bucket',
            f'base_path = {WORK}',
        ]
    else:
        lines += [
            'storage_provider = s3_compatible',
            f'bucket_name = {MINIO_BUCKET}',
            'key_file = ~/.aws/minio_credentials',
            'api_profile = default',
            'host = 127.0.0.1',
            'port = 9000',
            'secure = False',
            'region = default',
            'base_path = /tmp',
        ]
    # Medusa's default is 50MB/s, which on a fast link measures the cap rather than the code.
    # Unlimited unless asked otherwise; an empty value is how the config says "no limit".
    lines.append(f'transfer_max_bandwidth = {max_bandwidth or ""}')
    if chunksize:
        lines.append(f'multipart_chunksize = {chunksize}')
    if encrypted:
        lines.append(f'key_secret_base64 = {KEY}')
    lines += ['', '[monitoring]', 'monitoring_provider = local', '']
    path = WORK / f'medusa-{name}.ini'
    path.write_text('\n'.join(lines))
    return path


class LocalBucket:

    def prepare(self):
        pass

    def wipe(self, prefix):
        shutil.rmtree(WORK / 'bucket' / prefix, ignore_errors=True)

    def stored(self, prefix):
        """(bytes, files) under a prefix."""
        root = WORK / 'bucket' / prefix
        if not root.exists():
            return 0, 0
        files = [f for f in root.rglob('*') if f.is_file()]
        return sum(f.stat().st_size for f in files), len(files)


class MinioBucket:

    def __init__(self):
        import boto3
        import configparser
        credentials = configparser.ConfigParser()
        credentials.read(pathlib.Path.home() / '.aws' / 'minio_credentials')
        self.client = boto3.client(
            's3', endpoint_url=MINIO_ENDPOINT,
            aws_access_key_id=credentials['default']['aws_access_key_id'],
            aws_secret_access_key=credentials['default']['aws_secret_access_key'],
        )

    def prepare(self):
        import botocore.exceptions
        try:
            self.client.head_bucket(Bucket=MINIO_BUCKET)
        except botocore.exceptions.ClientError:
            self.client.create_bucket(Bucket=MINIO_BUCKET)

    def _objects(self, prefix):
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=MINIO_BUCKET, Prefix=prefix + '/'):
            yield from page.get('Contents', [])

    def wipe(self, prefix):
        keys = [{'Key': o['Key']} for o in self._objects(prefix)]
        for i in range(0, len(keys), 1000):
            self.client.delete_objects(Bucket=MINIO_BUCKET, Delete={'Objects': keys[i:i + 1000]})

    def stored(self, prefix):
        objects = list(self._objects(prefix))
        return sum(o['Size'] for o in objects), len(objects)


# --------------------------------------------------------------------------- run


def median_of(samples, key):
    return statistics.median(s[key] for s in samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=3000000,
                        help='rows written by cassandra-stress (default 3000000, ~700 MB)')
    parser.add_argument('--repeats', type=int, default=3,
                        help='repetitions per backup measurement, median is kept (default 3)')
    parser.add_argument('--skip-restore', action='store_true',
                        help='skip M5/M6, which stop and restart Cassandra')
    parser.add_argument('--provider', choices=('minio', 'local'), default='minio',
                        help='storage backend (default minio; local cannot encrypt on this branch)')
    parser.add_argument('--max-bandwidth', default=None,
                        help='transfer_max_bandwidth for both configurations, e.g. 50MB/s, the Medusa default '
                             '(default here: unlimited, so that the code is measured rather than the cap)')
    parser.add_argument('--multipart-chunksize', default=None,
                        help='multipart_chunksize for both configurations, e.g. 16MB (default: Medusa default)')
    args = parser.parse_args()

    if shutil.which('/usr/bin/time') is None:
        sys.exit('/usr/bin/time is missing (dnf install time / apt install time)')

    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    bucket = MinioBucket() if args.provider == 'minio' else LocalBucket()
    bucket.prepare()
    for prefix in ('bench_plain', 'bench_cse'):
        bucket.wipe(prefix)

    recreate_cluster()
    log(f'Writing {args.rows} rows with cassandra-stress', level='>>>')
    stress(args.rows)
    nodetool('flush')
    nodetool('compact', 'keyspace1')
    wait_for_compactions()

    dataset = dataset_bytes()
    log(f'T1 dataset D = {dataset} bytes ({dataset / GIB:.2f} GiB)', level='===')
    if dataset < 200 * MIB:
        log('dataset below 200 MB: startup dominates, figures will not mean much', level='!!!')

    plain_cfg = write_config('plain', False, 'bench_plain', args.provider, args.max_bandwidth, args.multipart_chunksize)
    cse_cfg = write_config('cse', True, 'bench_cse', args.provider, args.max_bandwidth, args.multipart_chunksize)

    results = {}

    # ---- M1 / M2: full backups, repeated, median kept
    for label, cfg, name in (('M1', plain_cfg, 'plain'), ('M2', cse_cfg, 'cse')):
        samples = []
        for i in range(args.repeats):
            # wipe only this configuration's prefix: the other one still holds the backups that
            # M5/M6/M7 will restore and verify
            bucket.wipe(f'bench_{name}')
            wait_for_compactions()
            samples.append(measure(f'{label}-{i}', [
                venv_bin('medusa'), '--config-file', cfg,
                'backup', '--backup-name', f'bench_full_{name}_{i}', '--mode', 'full']))
        results[label] = {k: median_of(samples, k) for k in ('wall', 'user', 'sys', 'cpu', 'rss_mb')}
        results[label]['samples'] = samples
        results[f'S_{label}'], results[f'F_{label}'] = bucket.stored(f'bench_{name}')

    # ---- M3 / M4: differential, after changing a little data
    log('Writing 200000 more rows before the differential backups', level='>>>')
    stress(200000)
    nodetool('flush')
    wait_for_compactions()
    for label, cfg, name in (('M3', plain_cfg, 'plain'), ('M4', cse_cfg, 'cse')):
        results[label] = measure(label, [
            venv_bin('medusa'), '--config-file', cfg,
            'backup', '--backup-name', f'bench_diff_{name}', '--mode', 'differential'])

    # ---- M7: verify
    for label, cfg, name in (('M7a', plain_cfg, 'plain'), ('M7b', cse_cfg, 'cse')):
        results[label] = measure(label, [
            venv_bin('medusa'), '--config-file', cfg,
            'verify', '--backup-name', f'bench_full_{name}_{args.repeats - 1}'])

    # ---- M5 / M6: restores
    if not args.skip_restore:
        for label, cfg, name in (('M5', plain_cfg, 'plain'), ('M6', cse_cfg, 'cse')):
            results[label] = measure(label, [
                venv_bin('medusa'), '--config-file', cfg, 'restore-node',
                '--backup-name', f'bench_full_{name}_{args.repeats - 1}',
                '--temp-dir', str(WORK / 'tmp')])

    report(results, dataset, args)
    (WORK / 'results.json').write_text(json.dumps(
        {k: v for k, v in results.items() if not isinstance(v, dict) or 'samples' not in v},
        indent=2, default=str))
    return 0


def report(r, dataset, args):
    gib = dataset / GIB

    def cpu(label):
        return r[label]['cpu']

    def wall(label):
        return r[label]['wall']

    print('\n' + '=' * 76)
    print('T1  dataset D = {:,} bytes ({:.2f} GiB), median of {} runs, storage {}{}{}'.format(
        dataset, gib, args.repeats, args.provider,
        f', max bandwidth {args.max_bandwidth}' if args.max_bandwidth else ', bandwidth unlimited',
        f', chunk {args.multipart_chunksize}' if args.multipart_chunksize else ''))
    print('=' * 76)
    print(f'{"Ref":<5} {"Measurement":<34} {"Wall(s)":>9} {"CPU(s)":>9} {"RSS(MB)":>9}')
    names = {
        'M1': 'full backup, no encryption', 'M2': 'full backup, encrypted',
        'M3': 'differential, no encryption', 'M4': 'differential, encrypted',
        'M5': 'restore, no encryption', 'M6': 'restore, encrypted',
        'M7a': 'verify, no encryption', 'M7b': 'verify, encrypted',
    }
    for label, name in names.items():
        if label in r:
            print(f'{label:<5} {name:<34} {wall(label):9.1f} {cpu(label):9.1f} '
                  f'{r[label]["rss_mb"]:9.0f}')

    print('\nStored bytes')
    print(f'  S1 plaintext  {r["S_M1"]:>14,}  ({r["F_M1"]} files)')
    print(f'  S2 encrypted  {r["S_M2"]:>14,}  ({r["F_M2"]} files)')

    print('\n' + '=' * 76)
    print('R   calculations')
    print('=' * 76)
    c1 = (cpu('M2') - cpu('M1')) / gib
    print(f'  C1  CPU cost of encryption          {c1:8.2f}  CPU-s per GiB backed up')
    print(f'  C2  CPU ratio, backup               {cpu("M2") / cpu("M1"):8.2f}  x')
    print(f'  C3  Wall ratio, backup              {wall("M2") / wall("M1"):8.2f}  x')
    print(f'  C4  throughput without              {dataset / MIB / wall("M1"):8.1f}  MB/s')
    print(f'      throughput with                 {dataset / MIB / wall("M2"):8.1f}  MB/s')
    print(f'  C5  storage overhead                '
          f'{(r["S_M2"] - r["S_M1"]) / r["S_M1"] * 100:8.2f}  %')
    if 'M5' in r:
        print(f'  C6  CPU ratio, restore              {cpu("M6") / cpu("M5"):8.2f}  x')
        print(f'      wall ratio, restore             {wall("M6") / wall("M5"):8.2f}  x')
    print(f'  C7  CPU ratio, differential         {cpu("M4") / cpu("M3"):8.2f}  x')
    print(f'      wall ratio, differential        {wall("M4") / wall("M3"):8.2f}  x')
    print(f'  C8  extra peak memory               '
          f'{r["M2"]["rss_mb"] - r["M1"]["rss_mb"]:8.0f}  MB')
    print(f'      verify, CPU ratio               {cpu("M7b") / cpu("M7a"):8.2f}  x')
    print('=' * 76)


if __name__ == '__main__':
    sys.exit(main())
