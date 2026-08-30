# Manual test protocol — client-side encryption

Drives the `medusa` CLI directly against CCM clusters, the way an operator would, rather than through
the behave suite. It exists because the automated suite covers one storage backend per run and never
compares the encrypted and unencrypted paths side by side on the same data.

Runner: `./run_manual_cse_tests.sh` (see *Execution* below).

## 1. What is under test

| Axis | Values |
|---|---|
| Storage | `local` (physical filesystem) · `minio` (S3-compatible) |
| Client-side encryption | off · on |
| Backup mode | full · differential |
| Restore | in place · single keyspace · to another cluster via sstableloader |

The first two axes give **four configurations**. Each one runs the same scenario, so any difference
in outcome is attributable to the storage backend or to encryption, and to nothing else.

| # | Configuration | Storage provider | `key_secret_base64` |
|---|---|---|---|
| C1 | `local-plain` | local | absent |
| C2 | `local-cse` | local | set |
| C3 | `minio-plain` | s3_compatible | absent |
| C4 | `minio-cse` | s3_compatible | set |

## 2. Fixture

Two keyspaces, so that a single-keyspace restore can be shown to leave the other one alone.

| Keyspace | Table | Purpose |
|---|---|---|
| `ks_alpha` | `t_alpha` | Grows at every step |
| `ks_beta` | `t_beta` | Stops growing after B2, which makes a selective restore visible |

Row counts are distinct at every backup point, so each assertion identifies exactly one state.

| Step | `ks_alpha` | `ks_beta` | Action |
|---|---|---|---|
| t0 | 100 | 50 | **B1** — full backup |
| t1 | 200 | 100 | **B2** — differential backup |
| t2 | 300 | 100 | **B3** — differential backup |

`nodetool flush` runs before each backup so the data is in SSTables rather than only in memtables.

## 3. Steps, per configuration

### Backup

| ID | Step | Pass criterion |
|---|---|---|
| **A1** | `medusa backup --mode=full --backup-name=B1` | exit 0 |
| **A2** | `medusa backup --mode=differential --backup-name=B2` | exit 0 |
| **A3** | `medusa backup --mode=differential --backup-name=B3` | exit 0 |
| **A4** | `medusa list-backups` | the three backups are listed with the expected mode |
| **A5** | `medusa verify --backup-name=<each>` | reports no error for all three |
| **A6** | `medusa status --backup-name=B3` | reports the backup as complete |

### Storage-level checks

| ID | Step | Pass criterion |
|---|---|---|
| **A7** | Read an SSTable object straight from storage | **CSE on**: starts with `02` (AWS ESDK v2 header) · **CSE off**: does not |
| **A8** | Read `schema.cql` straight from storage | readable as plaintext in **both** cases — metadata is never encrypted |
| **A9** | Inspect `manifest.json` | `source_MD5` present **only** for differential backups with CSE on |

A7 is what actually proves the data is encrypted. Everything else could pass with encryption silently
disabled.

### Restore

Restores run in order; each starts from the state the previous one left, and expected counts account
for that.

| ID | Step | Expected `ks_alpha` | Expected `ks_beta` |
|---|---|---|---|
| **R1** | Restore **B1** in place, whole node | 100 | 50 |
| **R2** | Restore **B3** in place, `--keyspace ks_alpha` only | 300 | **50** (untouched by R2) |
| **R3** | Restore **B2** onto a second cluster with `--use-sstableloader` | 200 | 100 |

R2's second column is the point of the test: `ks_beta` must still hold what R1 restored, proving the
restore was actually selective. R3 targets a **separate CCM cluster**, which is the remote-restore
path and the only one that exercises `sstableloader` against encrypted data.

## 4. Pass / fail

A configuration passes when every step A1-A9 and R1-R3 meets its criterion. Any row count that
differs from the table is a failure, including one that is "close" — a differential backup that
silently drops a file produces a plausible but wrong number, which is exactly what this protocol is
looking for.

Results are recorded per configuration, so a failure isolates to a storage backend, to encryption, or
to both.

## 5. Execution

Prerequisites: CCM with Cassandra 4.1.9, `JAVA_HOME` set to a JDK 11, and for C3/C4 a MinIO reachable
at `127.0.0.1:9000` with credentials in `~/.aws/minio_credentials`.

```bash
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk
ulimit -n 8192
cd tests/manual && ./run_manual_cse_tests.sh            # all four configurations
./run_manual_cse_tests.sh local-cse                     # or just one
```

The script is written to fail loudly: every medusa invocation is checked, every row count is compared
to the table above, and the run stops on the first mismatch with the offending command and its output.
