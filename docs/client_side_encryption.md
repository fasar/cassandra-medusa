# Client-Side Encryption

## Overview

Medusa can encrypt the SSTables it backs up **before they leave the node**, with a key that only the
node holds. The objects in the bucket are then ciphertext for everyone else: the storage provider,
anyone with read access to the bucket, and anyone who obtains a copy of it. This is independent of,
and can be combined with, the server-side encryption S3 itself performs with a KMS key (`kms_id`,
SSE-KMS).

The cryptography is delegated to the
[Amazon S3 Encryption Client](https://docs.aws.amazon.com/amazon-s3-encryption-client/latest/developerguide/what-is-s3-encryption-client.html)
for Python, the official AWS library. Each object is encrypted with its own data key under
AES-256-GCM with key commitment, and the data key travels with the object, wrapped by the key you
configure. That wrapping key is what makes this Medusa's own: the S3 Encryption Client only ships a KMS keyring, so
Medusa provides the keyring that wraps with a local AES-256 key instead.

**Client-side encryption is available with the S3 storage providers only**: `s3`, the `s3_*` regions,
`s3_compatible` (MinIO, Ceph RGW and the like), `s3_rgw` and `ibm_storage`. Configuring a key with
`local`, `google_storage` or `azure_blobs` is refused when Medusa starts.

**Important**: encrypted and unencrypted backups are **not compatible** in differential backup
chains; see [Turning encryption on for an existing cluster](#turning-encryption-on-for-an-existing-cluster).

## Prerequisites

Install Medusa with the optional `encryption` extra, which pulls in
`amazon-s3-encryption-client-python`:

```bash
pip install "cassandra-medusa[S3,encryption]"
```

Without it, a configured key fails at startup with a message saying how to install it.

## How it works

**During backup**

- Each SSTable component is encrypted on the fly while it is uploaded. Files below the multipart
  threshold (8 MiB) go up in a single request; larger ones go up as a multipart upload, one part of
  `multipart_chunksize` at a time, encrypted as they are read. No temporary file is written and
  memory use does not depend on the size of the file.
- Backup metadata (`manifest.json`, `schema.cql`, `tokenmap.json`, ...) is uploaded in plaintext,
  so a backup stays inspectable and its index usable without the key.
- The manifest records the size and hash of the **encrypted** object (that is what `verify`
  compares against the bucket) and, for differential backups, the size and MD5 of the file
  **before** encryption, which is what the next differential backup compares local files against.

**During restore**

- Encrypted objects are downloaded with `get_object` and decrypted block by block as they are
  written to disk. The authentication tag is verified on the last block; a file whose verification
  fails, or whose download fails halfway, is deleted rather than left where a restore could pick it
  up.
- Metadata files are downloaded as they are.

**Differential backups**

- Because the bucket only knows the encrypted size and hash of each object, Medusa reads the
  manifests of the previous differential backups to find the plaintext size and MD5 of the files
  already uploaded, and compares the local files against those.
- A file that was uploaded before encryption was turned on is never reused: it has no plaintext
  metadata to compare against, and a chain must not mix encrypted and unencrypted objects.

## What is stored

**What the S3 Encryption Client writes.** Each encrypted object is the AES-256-GCM ciphertext of
the file, 16 bytes longer than the file (the authentication tag). The S3 Encryption Client encrypts every object
under its own data key, with key commitment, and stores the encryption material in the object's
user metadata: `x-amz-meta-x-amz-3` is the wrapped data key, `x-amz-meta-x-amz-t` the encryption
context, `x-amz-meta-x-amz-d` the key commitment, and a few more. Medusa does not touch any of
this; it is the S3 Encryption Client's format, read back by the S3 Encryption Client on restore.

**What Medusa adds.** The S3 Encryption Client only knows how to wrap data keys with AWS KMS, so Medusa provides
the keyring that wraps them with the configured local key: AES-256-GCM, authenticated over the
whole encryption context, so that the metadata cannot be edited without invalidating the wrap.
Medusa also decides what the encryption context contains: the name of its keyring
(`medusa-backup/raw-aes-key`) and a short fingerprint of the key, checked on restore so that the
wrong key is reported as such rather than as a corrupt object. The fingerprint reveals nothing
about the key.

Objects written this way are readable only through Medusa, with the same key. They are **not**
interoperable with the AWS Encryption SDK, nor with the S3 Encryption Client's own AES keyring in
other languages, which wraps keys differently.

## What is *not* encrypted

Only SSTable files are encrypted. Backup metadata is deliberately left in plaintext, so that a
backup stays inspectable and its index usable without the key. Anyone with read access to the
bucket can therefore read, without the key:

| What | Reveals |
|---|---|
| Object keys | Keyspace names, table names and UUIDs, secondary index names, node fqdn, backup names and timestamps |
| `schema.cql` | The **complete schema**: every table, every column, their types, and CQL comments |
| `tokenmap.json` | Node addresses, tokens, datacenters and racks |
| `server_version.json` | The exact Cassandra version |
| `manifest.json` | File layout, and the encrypted size and hash of every object |
| `manifest.json`, differential backups only | `source_size` and `source_MD5`: the **exact size and MD5 of each file before encryption** |
| Object metadata | The key fingerprint, and that the object was encrypted by Medusa |

Two consequences worth weighing before enabling CSE to satisfy a compliance requirement:

- **Column names are often the most descriptive thing about a dataset.** The contents of a
  `card_number` column are encrypted; the fact that the column exists is not.
- On differential backups, `source_MD5` lets someone with bucket access confirm a *guessed*
  plaintext without the key. It is inert for a large high-entropy `Data.db`, and much less so for a
  small structured component. It is written only where it is needed - differential backups - and
  never for full backups.

If the metadata itself is sensitive, client-side encryption is not sufficient on its own: pair it
with restricted bucket access and server-side encryption.

## Configuration

### Encryption key generation

Generate a 32-byte (256-bit) key and base64-encode it:

```bash
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

This outputs a 44-character base64 string. Keep the output of your own command: never reuse a key
that has been published anywhere, including the ones that appear in this repository's test
configurations.

### Supplying the key

The key protects every backup you will ever take, so Medusa accepts it three ways. They are listed
here from the most to the least appropriate for production:

| How | Setting | Notes |
|---|---|---|
| A file readable only by the Medusa user | `key_secret_file`, or the `MEDUSA_KEY_SECRET_FILE` environment variable | Takes precedence over the other two. A trailing newline is ignored, so `echo "$KEY" > file` works. |
| An environment variable | `MEDUSA_KEY_SECRET_BASE64` | Suits containers and Kubernetes secrets mounted as env vars. |
| Directly in `medusa.ini` | `key_secret_base64` | Simplest, but puts the key in a file that configuration management usually templates and that is often readable more widely than the key deserves. |

Setting the key is what turns encryption on; there is no separate switch.

```ini
[storage]
storage_provider = s3_us_west_oregon
# ... other storage configuration ...

# Preferred: point at a file that only the Medusa user can read
key_secret_file = /etc/medusa/medusa-encryption-key

# Or inline, if you accept the key living in this file
;key_secret_base64 = <YOUR-BASE64-ENCODED-32-BYTE-KEY>

# Optional. Size of the parts of an encrypted multipart upload, and the unit of memory it uses:
# roughly four times this per file in flight. 50MB is the Medusa default.
;multipart_chunksize = 16MB
```

Settings that interact with encryption:

| Setting | With client-side encryption |
|---|---|
| `kms_id` (SSE-KMS) | Allowed. S3 encrypts the ciphertext a second time, server-side, with the KMS key; the two are independent. |
| `sse_c_key` (SSE-C) | **Refused.** SSE-C needs its key on every request of a multipart upload, which the S3 Encryption Client does not guarantee, and encrypting the ciphertext a second time with a customer key adds nothing. |
| `transfer_max_bandwidth` | Honoured for encrypted uploads and downloads, as for plaintext ones. |
| `multipart_chunksize` | Sets the part size of encrypted multipart uploads, and with it the memory used per file in flight (about four parts). |
| `concurrent_transfers` | The number of files encrypted and uploaded in parallel. The parts of one file are uploaded sequentially. |

## Security best practices

Whichever file holds the key - `medusa.ini` or the file `key_secret_file` points at - must be
readable only by the account running Medusa:

```bash
chmod 0600 /etc/medusa/medusa-encryption-key
chown cassandra:cassandra /etc/medusa/medusa-encryption-key
```

Ensure that:
- Only the user running Medusa has read access to the key.
- Group and other users have no access to it.
- The key is owned by the appropriate user/service account.

## Losing the key means losing the backups

There is no recovery path. The key is not stored with the backups, not derivable from them, and not
held by Medusa anywhere else. If it is lost, every encrypted backup taken with it becomes
permanently unreadable.

Before turning encryption on for a production cluster:

1. Store the key somewhere independent of the cluster it protects - a secrets manager, not the node
   being backed up, and not the same storage bucket as the backups.
2. Make sure at least two people, or one automated process, can retrieve it.
3. **Perform a full restore from an encrypted backup** on a scratch cluster, using only what your
   recovery runbook says to use. A key that cannot be found during an incident is a key you do not
   have.

Rotating the key is not supported: Medusa decrypts with the key currently configured, so backups
taken with a previous key stop being readable once you change it. Restoring one with the wrong key
fails with a message naming the fingerprint of the key it was taken with. Keep the old key for as
long as you keep the backups it protects.

## Turning encryption on for an existing cluster

Encrypted and unencrypted files cannot share a differential backup chain: Medusa cannot compare a
local file against an encrypted object without the plaintext metadata that only encrypted backups
record, so the first backup after enabling encryption re-uploads every SSTable. Plan for it to be
the size of a full one.

**Start the encrypted chain under a new `prefix` (or a new bucket).** Differential backups store
every SSTable by name under a `data/` prefix that all the backups of the node share; written over
the plaintext objects, the ciphertext would leave every earlier backup pointing at objects it cannot
read. Medusa refuses to do that: an encrypted upload that would overwrite an object uploaded without
encryption fails with a message saying to use a new prefix, and nothing is written. Keep the old
prefix, without a key, for as long as you keep the old backups; storage usage roughly doubles until
they age out of `max_backup_count` / `backup_grace_period_in_days`.

The backups taken before the key stay listed, verify, and restore, **but only with a configuration
that has no key**. With a key configured, Medusa refuses to restore an object that was uploaded
without one, with a message saying so, rather than falling back to a plaintext download: a key
configured means every SSTable it restores is authenticated, and a silent fallback would let anyone
with write access to the bucket swap a ciphertext for plaintext of their choosing. To restore an old
backup, run the restore with the same `medusa.ini` minus `key_secret_file` / `key_secret_base64`,
pointing at the old prefix. `tests/manual/UPGRADE.md` is the protocol that checks all of this
against backups taken by a Medusa without the feature.

## Usage

### Creating encrypted backups

Once configured, backups are automatically encrypted:

```bash
medusa backup --backup-name my-encrypted-backup
```

The manifest will include:
- `MD5` and `size`: hash and size of the **encrypted** object,
- for differential backups, `source_MD5` and `source_size`: hash and size of the **original** file.

### Restoring encrypted backups

Restoration works transparently:

```bash
medusa restore --backup-name my-encrypted-backup
```

### Verifying encrypted backups

```bash
medusa verify --backup-name my-encrypted-backup
```

Verification compares the encrypted objects in the bucket with the manifest. It does not decrypt
them; a full restore on a scratch cluster is the only check that the key you hold opens them.

## What is encrypted

**Encrypted**:
- SSTable data files (`*.db`, `*.txt`, `*.crc32`, ...),
- index files (secondary indexes in `.index_name/` directories),
- all user data files.

Everything else - the backup metadata - is stored in plaintext, and what that discloses is worth
knowing before you rely on CSE: see [What is *not* encrypted](#what-is-not-encrypted) above.

## Limits and performance

- **Object size.** AES-GCM encrypts at most about 64 GiB under one key, and one key covers a whole
  object. Medusa refuses to upload a larger SSTable component rather than produce an object that
  cannot be authenticated.
- **CPU.** AES-256-GCM on a CPU with AES instructions runs at well over a gigabyte per second per
  core; the network is usually the bottleneck. The parts of one file are encrypted and uploaded
  sequentially, so a single very large file no longer benefits from parallel part uploads; run
  several files in parallel with `concurrent_transfers` instead.
- **Memory.** About four times `multipart_chunksize` per file being uploaded, and one block per file
  being downloaded, whatever the size of the files. Lower `multipart_chunksize` on small nodes.
- **Disk.** No temporary file is written on either side.
- **Storage.** 16 bytes per object, plus a few hundred bytes of metadata.

### Measured

All the figures below come from the same machine, a developer laptop running the whole stack, so
the network is a loopback and wall-clock times say what the code costs, not what a link costs:

| | |
|---|---|
| CPU | Intel Core i7-1360P (13th gen), 12 cores, 16 threads, AES instructions |
| RAM | 31 GB |
| Storage | MinIO RELEASE.2025-09-07 on `127.0.0.1:9000`, on an overlay filesystem backed by SSD |
| Cassandra | 4.1.9, one node under CCM, JDK 11 |
| Medusa | this branch, Python 3.11, `concurrent_transfers = 4`, `multipart_chunksize = 50MB` unless stated, no bandwidth limit unless stated |
| Measure | `/usr/bin/time -v` around each `medusa` command: wall clock, CPU, and peak resident memory of the process |

**Cost of encryption** (`tests/manual/BENCHMARK.md`, §5), 743 MB dataset, median of 3 runs:
0.84 CPU-seconds per GiB of encrypted backup, about 250 MB of extra peak memory, no measurable
cost on restore, and under Medusa's default 50MB/s bandwidth cap a backup 12% longer than without
encryption.

**Large SSTables and memory** (`tests/manual/LARGE-SSTABLES.md`), SSTables produced by leveled
compaction at a chosen size, every restored component compared byte for byte with the original:

| SSTables | Configuration | Backup | Peak memory, backup | Restore | Peak memory, restore |
|---|---|---|---|---|---|
| 3 × ~300 MB (873 MB) | plaintext | 6.2 s | 122 MB | 15.4 s | 118 MB |
| | encrypted | 10.9 s | **414 MB** | 16.1 s | 130 MB |
| 722 MB + 1024 MB | plaintext | 8.5 s | 123 MB | 16.3 s | 118 MB |
| | encrypted | 17.0 s | **503 MB** | 17.6 s | 128 MB |
| 722 MB + 1024 MB | encrypted, `multipart_chunksize = 16MB` | 15.6 s | **261 MB** | 17.5 s | 129 MB |

The memory of an encrypted backup does not follow the size of the SSTables: the largest file grew
from 300 MB to 1 GB and the peak went from 414 MB to 503 MB. It follows the number of large files
uploaded at once and the part size, about `4 × multipart_chunksize` per file in flight, so at most
`concurrent_transfers × 4 × multipart_chunksize` (800 MB with the defaults). `multipart_chunksize`
is the lever: 16 MB parts halve the peak without slowing the backup down. Restore stays around
130 MB whatever the size, as decryption streams by blocks of 1 MiB.

Both protocols are in `tests/manual/` and can be rerun on your own cluster.
