# Upgrade compatibility protocol — backups from master, restored and continued by `boto3_cse`

Answers one question: **can a cluster already backed up by a Medusa without client-side encryption
move to this branch without losing anything?** Three things have to hold: its existing backups
restore, the differential chain continues, and turning the key on afterwards does what the
documentation says.

Runner: `./run_upgrade_compat_tests.sh` (see *Execution*). Companion to `README.md` (correctness of
encryption on a fresh chain) and `BENCHMARK.md` (its cost).

## 1. What is under test

Two Medusa installations on the same node, the same MinIO bucket and the same prefix:

| | Checkout | Client-side encryption |
|---|---|---|
| **master** | `../cassandra-medusa-master` (git worktree, own venv) | does not exist: `ManifestObject` has 3 fields, no `key_secret_*` setting |
| **branch** | this repository | off in phases R and C, on in phase E |

The fixture is the one of `README.md`: two keyspaces, `ks_beta` stops growing at B4 so that a restore
landing on the wrong backup is caught by a count that only one state has.

| Backup | Taken by | Mode | `ks_alpha` | `ks_beta` |
|---|---|---|---|---|
| B1 | master | full | 100 | 50 |
| B2 | master | differential | 200 | 80 |
| B3 | master | differential | 300 | 110 |
| B4 | master | full | 400 | 140 |
| B5 | master | differential | 500 | 140 |
| B6 | branch, no key | differential | 600 | 140 |
| B7 | branch, key on | differential | 700 | 140 |
| B8 | branch, key on | differential | 800 | 140 |

`nodetool flush` runs before each backup; `--enable-md5-checks` is on for every backup and verify, so
that the reuse decisions compare hashes and not only sizes.

## 2. Steps

### Phase M — master writes the chain

| ID | Step | Pass criterion |
|---|---|---|
| M | B1..B5 with master's `medusa` | exit 0 each; `list-backups` shows the five; **no** object under `data/` carries `x-amz-meta-x-amz-3` |

### Phase R — the branch reads master's backups (no key)

| ID | Step | Pass criterion |
|---|---|---|
| R1 | `list-backups` with the branch | the five master backups are listed |
| R2 | `verify --enable-md5-checks` on each of the five | exit 0 |
| R3 | `restore-node` B1, B3, B5, B4, B2 — in that order, each in place | after each restore, the row counts of that backup, and sampled row values intact |

R3's order is deliberate: a full, then a differential that depends on B1, then B5 which depends on
B4 not yet restored, then B4, then back to B2. Restore order must not matter, because the manifest
of each backup is self-sufficient.

### Phase C — the branch continues the chain (no key)

| ID | Step | Pass criterion |
|---|---|---|
| C1 | restore B5, add rows to state B6, `backup --mode differential` B6 with the branch | the objects uploaded are only the new SSTables; the manifest **reuses** objects master wrote; **no** `source_MD5` in the manifest (the manifest format without a key is master's) |
| C2 | `verify` B6 | exit 0 |
| C3 | restore B6 | counts of B6, values intact |
| C4 | restore B3 again | counts of B3: an older master backup still restores after the branch extended the chain |

### Phase E — the key is turned on, on top of the plaintext chain

| ID | Step | Pass criterion |
|---|---|---|
| E1 | with the key configured, same prefix: `list-backups`, `verify` B1, B5, B6 | plaintext backups are still listed and verify (verify never decrypts) |
| E2 | add rows to state B7, `backup --mode differential` B7 **with the key, on the same prefix** | **refused**, exit ≠ 0, message says to use a `new prefix`; none of the plaintext objects was overwritten; B5 and B6 still verify |
| E3 | the same backup B7 with the key **under a new prefix** | every object uploaded and encrypted, exactly `source_size + 16` bytes, `source_MD5` on every object |
| E4 | add rows to state B8, `backup --mode differential` B8 with the key | B8 reuses objects from B7 (fewer uploads than objects in its manifest) |
| E5 | `verify` B7 and B8 | exit 0 |
| E6 | restore B8 then B7 with the key | counts of B8 then B7, values intact |
| E7 | restore master's B5 **with the key configured**, on the old prefix | **refused**, exit ≠ 0, message `uploaded before encryption was enabled … Restore this backup without key_secret_base64` |
| E8 | restore B5 with the plain configuration | counts of B5, values intact |

E2 is the finding that shaped this protocol. Differential backups store every SSTable by name under
a `data/` prefix that all the backups of the node share, and the first encrypted backup re-uploads
every file. Written over the plaintext objects, the ciphertext would leave the manifests of B1..B6
pointing at objects they cannot read: every earlier backup unrestorable, silently. Medusa therefore
refuses to write an encrypted object over a plaintext one, and the way to turn encryption on is a
**new prefix** (or bucket) for the encrypted chain, the old one kept, without a key, for the old
backups until they age out.

E7 is the second thing to know: Medusa does not fall back to a plaintext download by itself. With a
key configured, every SSTable it restores is authenticated, and silently accepting an
unauthenticated object would let anyone with write access to the bucket swap a ciphertext for
plaintext of their choosing. A backup taken before the key is restored with a configuration that
has no key.

## 3. Pass / fail

The run passes when every step meets its criterion. Any count that differs from the table is a
failure. The run stops at the first failure, prints the offending command and its output, and stops
the CCM cluster.

## 4. Execution

Prerequisites: CCM with Cassandra 4.1.9, `JAVA_HOME` set to a JDK 11, a MinIO at `127.0.0.1:9000`
with credentials in `~/.aws/minio_credentials`, this repository installed with the `encryption`
extra, and a checkout of master with its own venv:

```bash
git worktree add ../cassandra-medusa-master master
(cd ../cassandra-medusa-master && poetry install && poetry run pip install git+https://github.com/riptano/ccm.git)

export JAVA_HOME=/usr/lib/jvm/java-11-openjdk
cd tests/manual && ./run_upgrade_compat_tests.sh
```

`MASTER_REPO` and `UPGRADE_TEST_DIR` override the defaults. The bucket `medusa-upgrade` is emptied
at the start of each run. About 12 minutes.

## 5. Results — 2026-09-08

master at `0b713a2` (Merge branch '0.27'), branch at the commit that adds this protocol, Cassandra
4.1.9 under CCM, MinIO RELEASE.2025-09-07 on 127.0.0.1:9000. **PASS in 281 s.**

| ID | Observed |
|---|---|
| M | 5 backups (2 full, 3 differential); 576 SSTable objects, none with encryption metadata |
| R1, R2 | the five are listed and verify on the branch |
| R3 | B1, B3, B5, B4, B2 restored in that order: counts 100/50, 300/110, 500/140, 400/140, 200/80, sampled values intact each time |
| C1 | B6: 120 objects uploaded, **152 reused from master's backups**, no `source_MD5` in the manifest |
| C2, C3, C4 | B6 verifies and restores (600/140); B3 restores again after the chain was extended (300/110) |
| E1 | key configured: B1, B5, B6 still listed and verify |
| E2 | B7 with the key on the plaintext prefix: **refused**; none of the 696 plaintext objects touched; B5 and B6 still verify |
| E3 | B7 under `upgrade-prefix-cse`: 288 objects, all encrypted, all `source_size + 16`, `source_MD5` everywhere |
| E4 | B8: 8 uploaded, **288 reused from B7** through `source_MD5` |
| E5, E6 | B7 and B8 verify; B8 restores (800/140), B7 restores (700/140) |
| E7 | B5 with the key: **refused**, message names the way out |
| E8 | B5 without the key: 500/140, values intact |

The first run of this protocol, before the refusal of E2 existed, failed at that step: B7 had
288 objects in its manifest but only 144 new keys in the bucket — the other 144 had overwritten
master's plaintext objects in place. That run is why `PlaintextObjectExistsError` exists.
