# Client-Side Encryption

## Overview

Medusa supports client-side encryption (CSE) to encrypt backup files before uploading them to cloud storage.
This provides an additional layer of security, ensuring that data is encrypted in transit and at rest, independent of server-side encryption capabilities.

**Important**: Encrypted and unencrypted backups are **not compatible** in differential backup chains.


## Prerequisites

To use client-side encryption, you must install Medusa with the optional `encryption` dependency, which installs the `aws-encryption-sdk` library.
Note that Medusa requires `aws-encryption-sdk` version 3.x (versions >=4.0.0 are not supported due to incompatible API changes):

```bash
pip install "cassandra-medusa[encryption]"
```

## How It Works

When client-side encryption is enabled:

1. **During Backup**:
   - SSTable files are encrypted locally using the AWS Encryption SDK.
   - The stream is processed on-the-fly to manage memory usage.
   - Encrypted files are uploaded to cloud storage.
   - Metadata files (`manifest.json`, `schema.cql`, etc.) remain unencrypted for compatibility.

2. **During Restore**:
   - Encrypted files are downloaded from cloud storage.
   - Files are decrypted locally using the AWS Encryption SDK stream decryptor before being restored to the Cassandra data directory.
   - Metadata files are copied directly without decryption.

3. **Differential Backups**:
   - The manifest stores both encrypted and original file metadata (`source_MD5`, `source_size`).
   - This allows comparison with local files without decrypting remote files.
   - Reduces unnecessary uploads and improves backup efficiency.

## File Format

Medusa delegates the encryption frame and metadata format entirely to the `aws-encryption-sdk`.
The SDK automatically adds necessary headers, message IDs, and authentication tags to ensure strong security and integrity of the encrypted stream.
The underlying cryptographic material manager wraps a user-provided raw AES 256-bit key.

## Configuration

### Encryption Key Generation

Generate a 32-byte (256-bit) key and base64-encode it. For example:

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

```ini
[storage]
# ... other storage configuration ...

# Preferred: point at a file that only the Medusa user can read
key_secret_file = /etc/medusa/medusa-encryption-key

# Or inline, if you accept the key living in this file
;key_secret_base64 = <YOUR-BASE64-ENCODED-32-BYTE-KEY>

# Temporary directory for encryption/decryption operations (optional)
# Defaults to system temp directory if not specified
# Directory must have sufficient space for concurrent file operations
# Note: This setting is ignored for S3 storage provider as it uses streaming encryption/decryption.
encryption_tmp_dir = /tmp

# Frame length (in bytes) used by AWS Encryption SDK. Default is 8MB (8388608).
# Using larger frames dramatically reduces CPU usage and backup times for large files
# by minimizing cryptographic operations and frame header overhead.
encryption_frame_length = 8388608
```

## Security Best Practices

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
taken with a previous key stop being readable once you change it. Keep the old key for as long as
you keep the backups it protects.

## Turning encryption on for an existing cluster

Encrypted and unencrypted files cannot share a differential backup chain: Medusa cannot compare a
local file against an encrypted object without the plaintext metadata that only encrypted backups
record. When you enable encryption on a node that already has differential backups, the next backup
therefore re-uploads every SSTable, and previously uploaded files stay in place until they age out
of `max_backup_count` / `backup_grace_period_in_days`.

Plan for that first backup to be the size of a full one, and for storage usage to roughly double
until the old chain is purged.

## Usage

### Creating Encrypted Backups

Once configured, backups are automatically encrypted:

```bash
medusa backup --backup-name my-encrypted-backup
```

Files uploaded to storage will be encrypted.
The manifest will include:
- `MD5` and `size`: Hash and size of the **encrypted** file,
- `source_MD5` and `source_size`: Hash and size of the **original** file (before encryption).

### Restoring Encrypted Backups

Restoration works transparently:

```bash
medusa restore --backup-name my-encrypted-backup
```

### Verifying Encrypted Backups

```bash
medusa verify --backup-name my-encrypted-backup
```

Verification checks both encrypted file integrity and manifest consistency.

## What is Encrypted

**Encrypted**:
- SSTable data files (`*.db`, `*.txt`, etc.),
- Index files (secondary indexes in `.index_name/` directories),
- All user data files.

**Not Encrypted** (stored as plaintext):
- `manifest*.json`
- `schema.cql`
- `tokenmap.json`
- `server_version.json`
- `backup_name.txt`

These metadata files must be accessible without decryption for backup discovery and validation.

## Performance Considerations

### Resource Usage

- **CPU**: Encryption/decryption adds CPU overhead. Impact depends on backup size and concurrent transfers. The `aws-encryption-sdk` introduces per-frame overhead; use a large `encryption_frame_length` (e.g., 8MB) to significantly lower CPU load.
- **Disk**: Temporary encrypted files are stored in `encryption_tmp_dir` during upload/download.
  - Ensure sufficient disk space (at least `concurrent_transfers * largest_file_size`).
  - **S3**: S3 storage supports streaming for encryption and decryption. Temporary files are **not** created when using S3.

### Optimization

- Adjust `concurrent_transfers` in `medusa.ini` to balance throughput and resource usage.
- Use dedicated `encryption_tmp_dir` on fast storage (SSD) for better performance.
