# Large SSTables — backup, restore and Medusa's memory

Answers two questions with real SSTables of 300 MB and 1 GB: **does an encrypted backup of
large SSTables restore byte for byte**, and **is Medusa's memory bounded, or does it grow with the
size of the file it encrypts?**

Runner: `run_large_sstable_test.py` (see *Execution*). Companion to `README.md`, `BENCHMARK.md`
and `UPGRADE.md`.

## 1. Method

One CCM node, one MinIO bucket, two configurations identical but for the key (`plain`, `cse`),
`concurrent_transfers = 4`, no bandwidth limit, `enable_md5_checks = True`.

1. `cassandra-stress write n=<rows>` into `keyspace1.standard1`, flush.
2. `ALTER TABLE ... WITH compaction = {'class': 'LeveledCompactionStrategy', 'sstable_size_in_mb': N}`
   then `nodetool compact`: leveled compaction splits its output into SSTables of about N MB, which
   is how the data directory ends up holding a few SSTables of the wanted size. Automatic
   compaction is then disabled so they stay as they are.
3. Every SSTable component (`Data.db`, `Index.db`, `Summary.db`, ...) is hashed, and 200 rows are
   sampled with their value.
4. For each configuration: `medusa backup --mode full` under `/usr/bin/time -v`, `medusa verify`,
   then every object of the backup is checked in the bucket (size equal to the local file, plus
   16 bytes when encrypted; encryption metadata present or absent as expected; large `Data.db`
   uploaded as multipart, by their ETag), then `medusa restore-node` under `/usr/bin/time -v`,
   then every restored component is compared to its original hash and the 200 sampled rows are
   read back by key.

Memory is the **maximum resident set size** of the `medusa` process as reported by
`/usr/bin/time -v`: the peak, threads included, over the whole command.

## 2. Pass criteria

- every object in the bucket has the expected size and encryption metadata;
- every restored SSTable component is byte-for-byte identical to the original;
- every sampled row reads back with its value;
- the peak memory of the encrypted backup does **not** scale with the size of the SSTables.

## 3. Results — 2026-09-09

Branch `boto3_cse` at the commit that adds this protocol, Cassandra 4.1.9, MinIO on 127.0.0.1:9000,
16 cores, 31 GB RAM. All three runs **PASS**: every object had the expected size and metadata,
every component came back identical, every sampled row read back.

### 300 MB SSTables — 4 000 000 rows, 3 `Data.db` of 273, 300 and 300 MB, 873 MB in 24 components

| | backup wall | backup CPU | **backup peak RSS** | verify | restore wall | restore peak RSS |
|---|---|---|---|---|---|---|
| plain | 6.2 s | 7.5 s | 122 MB | 0.8 s | 15.4 s | 118 MB |
| cse | 10.9 s | 10.3 s | **414 MB** | 0.8 s | 16.1 s | 130 MB |

### 1 GB SSTables — 8 000 000 rows, 2 `Data.db` of 722 and 1024 MB, 1 746 MB in 16 components

| | backup wall | backup CPU | **backup peak RSS** | verify | restore wall | restore peak RSS |
|---|---|---|---|---|---|---|
| plain | 8.5 s | 9.8 s | 123 MB | 0.8 s | 16.3 s | 118 MB |
| cse | 17.0 s | 13.9 s | **503 MB** | 0.8 s | 17.6 s | 128 MB |

### 1 GB SSTables again, encrypted, with `multipart_chunksize = 16MB`

| | backup wall | backup CPU | **backup peak RSS** | verify | restore wall | restore peak RSS |
|---|---|---|---|---|---|---|
| cse, 16 MB parts | 15.6 s | 12.9 s | **261 MB** | 0.8 s | 17.5 s | 129 MB |

Encryption cost: 3.4 CPU-s/GiB at 300 MB, 2.4 CPU-s/GiB at 1 GB (the fixed cost of the process
weighs less on a bigger dataset). Restore is within 8 % of plaintext in both cases.

## 4. Reading the memory figures

**The memory of an encrypted backup does not grow with the SSTable size.** The largest file went
from 300 MB to 1 024 MB, 3.4 times bigger, and the peak went from 414 MB to 503 MB. Had Medusa
held a file in memory, the second peak would have been above a gigabyte.

What it grows with is the number of large files **in flight** and the **part size**:

| Term | Value |
|---|---|
| baseline of the process (plain backup, same data) | ~122 MB |
| per large file being uploaded, multipart of `multipart_chunksize` (50 MB by default) | current part and the next one in plaintext, the encrypted part being sent, the previous encrypted part kept for a retry: **≈ 4 × 50 MB** |
| files in flight at once | up to `concurrent_transfers` (4), in practice the files large enough to go multipart that happen to be uploaded together |

300 MB run: 3 large `Data.db` uploaded together, 414 − 122 = 292 MB above baseline, about 100 MB
per file. 1 GB run: 2 `Data.db` plus their 60–90 MB `Index.db`, also multipart, 503 − 122 =
381 MB, about 130 MB per large file. Both are under the 4 × 50 MB per file the model allows: parts
are freed as they go, and the peak catches a few of them at once, not all four for every file.

The worst case is therefore `concurrent_transfers × 4 × multipart_chunksize`, 800 MB with the
defaults, whatever the size of the SSTables. **`multipart_chunksize` is the lever**, and the third
run shows it: the same 1 GB SSTables with 16 MB parts peak at 261 MB instead of 503 MB, 139 MB
above the plaintext baseline, and the backup is not slower (15.6 s against 17.0 s). The price is
three times more requests per file, which a local MinIO does not feel and a distant S3 might.

The restore side is flat: 130 MB at 300 MB, 128 MB at 1 GB, 10 MB above plaintext. Decryption
streams by blocks of 1 MiB and keeps nothing.

## 5. Execution

Prerequisites as for `BENCHMARK.md`: CCM with Cassandra 4.1.9, `JAVA_HOME` on a JDK 11, MinIO at
`127.0.0.1:9000` with `~/.aws/minio_credentials`, GNU `time`, the `encryption` extra installed.

```bash
export JAVA_HOME=/usr/lib/jvm/java-11-openjdk
poetry run python tests/manual/run_large_sstable_test.py --rows 4000000 --sstable-mb 300
poetry run python tests/manual/run_large_sstable_test.py --rows 8000000 --sstable-mb 1024
poetry run python tests/manual/run_large_sstable_test.py --rows 8000000 --sstable-mb 1024 --only cse --multipart-chunksize 16MB
```

`LARGE_TEST_DIR` (default `/tmp/medusa-large`) receives the configurations, the `/usr/bin/time`
reports, medusa's output per command and `results.json`. The bucket `medusa-large` is emptied at
the start. About 3 minutes per run at 300 MB, 4 at 1 GB, most of it `cassandra-stress`.
