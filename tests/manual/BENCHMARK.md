# Benchmark protocol — what client-side encryption costs

Measures the price of turning CSE on: wall clock, CPU, memory and stored bytes, for backup and
restore, against the same data on the same cluster.

Written to be run **by hand on a multi-node cluster**. Every command is given verbatim, every number
to write down is named, and every calculation is spelled out. Nothing here is inferred: if a figure
is not in a table below, it was not measured.

> Companion to `README.md`, which checks that CSE is *correct*. This one only asks what it *costs*.

---

## 0. Before you start

### 0.1 What you need

- A Cassandra cluster, 3 nodes or more, where you can stop and start Cassandra.
- Medusa installed on **every** node, with a storage backend reachable from all of them.
- `/usr/bin/time` (GNU time, **not** the shell builtin). Check with `/usr/bin/time --version`;
  install with `apt install time` or `dnf install time`.
- Enough room in the bucket for roughly **2.2×** your dataset (one plaintext copy, one encrypted).
- An S3 storage provider: client-side encryption exists for `s3`, `s3_compatible`, `s3_rgw` and
  `ibm_storage` only.

### 0.2 Record the environment first

These numbers make the results comparable later. Fill in **table T0**.

```bash
# on one node
nproc
free -g | awk '/^Mem:/ {print $2}'
lscpu | grep -E '^Model name|^CPU\(s\):|^Thread'
cat /sys/block/$(lsblk -no pkname $(df --output=source /var/lib/cassandra | tail -1) | head -1)/queue/rotational
nodetool version
medusa --version
python3 --version
```

**Table T0 — environment**

| Item | Value |
|---|---|
| Number of nodes | |
| vCPU per node | |
| RAM per node (GB) | |
| CPU model | |
| Data disk: 0 = SSD, 1 = spinning | |
| Cassandra version | |
| Medusa version | |
| Python version | |
| Storage backend (s3 / s3_compatible / ibm_storage) | |
| `concurrent_transfers` in medusa.ini | |
| `multipart_chunksize` in medusa.ini (default 50MB) | |
| `transfer_max_bandwidth` in medusa.ini, if any | |

> If `medusa --version` prints `'callable'`, you have hit defect M1 — use
> `"$(poetry env info --path)/bin/medusa"` everywhere below instead of `medusa`.

### 0.3 The two configurations

Two files, identical except for the encryption key and the prefix. **The distinct prefix is
mandatory**: sharing one would let the second run reuse the first one's files and destroy the
measurement.

```bash
sudo cp /etc/medusa/medusa.ini /etc/medusa/medusa-plain.ini
sudo cp /etc/medusa/medusa.ini /etc/medusa/medusa-cse.ini

# generate one key, the SAME on every node
python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

In `/etc/medusa/medusa-plain.ini`, under `[storage]`:

```ini
prefix = bench_plain
# no key_secret_base64 line at all
```

In `/etc/medusa/medusa-cse.ini`, under `[storage]`:

```ini
prefix = bench_cse
key_secret_base64 = <the key printed above>
```

Deploy both files to **every** node, with the same key.

---

## 1. Create the dataset

Run on **one** node. `cassandra-stress` ships with Cassandra.

```bash
cassandra-stress write n=5000000 -rate threads=50 \
    -node <node1_ip>,<node2_ip>,<node3_ip> \
    -schema "replication(strategy=SimpleStrategy,replication_factor=3)"
```

Then, on **every** node:

```bash
nodetool flush
nodetool compact keyspace1
```

Measure the on-disk size **per node** — write down each one in table T1:

```bash
du -sb /var/lib/cassandra/data/keyspace1 | cut -f1
```

**Table T1 — dataset**

| Node | Bytes on disk (`du -sb`) |
|---|---|
| node1 | |
| node2 | |
| node3 | |
| … | |
| **D = total, all nodes** | |

> 5 million rows gives roughly 1.2 GB per node at RF=3. Adjust `n=` so each node holds **at least
> 1 GB**: below that, JVM startup and network dominate and the measurement says nothing about
> encryption.

---

## 2. The measurements

Every backup and restore below is run through `/usr/bin/time -v`, whose output gives the four numbers
we need. Run each command **on every node**, and record the numbers **per node**.

The template, used throughout:

```bash
/usr/bin/time -v medusa --config-file <CONFIG> <COMMAND> 2> /tmp/bench-<LABEL>-$(hostname).txt
```

Read the four values back with:

```bash
grep -E 'Elapsed \(wall|User time|System time|Maximum resident' /tmp/bench-<LABEL>-$(hostname).txt
```

They map to the table columns as:

| `time -v` line | Column |
|---|---|
| `Elapsed (wall clock) time` | **Wall** (convert `m:ss` to seconds) |
| `User time (seconds)` | **User CPU** |
| `System time (seconds)` | **Sys CPU** |
| `Maximum resident set size (kbytes)` | **RSS** (divide by 1024 for MB) |

---

### 2.1 M1 — full backup, no encryption

On every node:

```bash
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-plain.ini \
    backup --backup-name bench_full_plain --mode full \
    2> /tmp/bench-M1-$(hostname).txt
```

Then, once, from any node:

```bash
medusa --config-file /etc/medusa/medusa-plain.ini status --backup-name bench_full_plain
```

**Table M1**

| Node | Wall (s) | User CPU (s) | Sys CPU (s) | RSS (MB) |
|---|---|---|---|---|
| node1 | | | | |
| node2 | | | | |
| node3 | | | | |
| **Sum** | | | | |

Also record the stored size — see §2.6 for how to read it per backend:

- **S1 = bytes stored under prefix `bench_plain`** = ______

---

### 2.2 M2 — full backup, with encryption

```bash
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-cse.ini \
    backup --backup-name bench_full_cse --mode full \
    2> /tmp/bench-M2-$(hostname).txt
```

**Table M2** — same columns as M1.

- **S2 = bytes stored under prefix `bench_cse`** = ______

---

### 2.3 M3 and M4 — differential backups

A differential backup on unchanged data uploads nothing and measures only the comparison logic —
which is exactly where the encrypted path differs (it reads manifests instead of listing storage).
Change a little data first so the run is not empty.

On one node:

```bash
cassandra-stress write n=200000 -rate threads=50 -node <node1_ip>
```

On every node: `nodetool flush`

Then, on every node:

```bash
# M3 - differential, no encryption
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-plain.ini \
    backup --backup-name bench_diff_plain --mode differential \
    2> /tmp/bench-M3-$(hostname).txt

# M4 - differential, with encryption
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-cse.ini \
    backup --backup-name bench_diff_cse --mode differential \
    2> /tmp/bench-M4-$(hostname).txt
```

**Tables M3 and M4** — same columns.

---

### 2.4 M5 and M6 — restores

⚠️ A restore **stops Cassandra, erases the data directory and restarts**. Do this on a cluster you
can destroy.

Restore the plaintext backup first, then the encrypted one, so both start from the same state.

On every node:

```bash
# M5 - restore, no encryption
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-plain.ini \
    restore-node --backup-name bench_full_plain \
    2> /tmp/bench-M5-$(hostname).txt
```

Wait for the cluster to come back (`nodetool status` shows every node `UN`), then:

```bash
# M6 - restore, with encryption
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-cse.ini \
    restore-node --backup-name bench_full_cse \
    2> /tmp/bench-M6-$(hostname).txt
```

**Tables M5 and M6** — same columns.

---

### 2.5 M7 — verify

`verify` reads every object back. With CSE it also has to read the encrypted objects, so it is worth
one measurement.

```bash
/usr/bin/time -v medusa --config-file /etc/medusa/medusa-plain.ini \
    verify --backup-name bench_full_plain 2> /tmp/bench-M7a-$(hostname).txt

/usr/bin/time -v medusa --config-file /etc/medusa/medusa-cse.ini \
    verify --backup-name bench_full_cse 2> /tmp/bench-M7b-$(hostname).txt
```

**Table M7** — one row per configuration.

---

### 2.6 Reading the stored size

Pick the line matching your backend:

```bash
# local storage
du -sb <base_path>/<bucket_name>/bench_plain | cut -f1
du -sb <base_path>/<bucket_name>/bench_cse   | cut -f1

# S3 / MinIO / any S3-compatible
aws s3 ls --summarize --recursive s3://<bucket>/bench_plain | tail -1
aws s3 ls --summarize --recursive s3://<bucket>/bench_cse   | tail -1
```

---

## 3. The calculations

Use the **sums across all nodes** from each table. Write the results into table R.

Let, for a measurement M:

- `Wall(M)` = sum of the Wall column
- `CPU(M)` = sum of User CPU + sum of Sys CPU
- `D` = dataset size in bytes, from table T1

### C1 — CPU cost of encryption, per gigabyte backed up

```
C1 = ( CPU(M2) - CPU(M1) ) / ( D / 1073741824 )
```

**Unit: CPU-seconds per GB.** This is the figure to quote. It is independent of node count and of
how many transfers run in parallel, so it transfers to other clusters.

### C2 — CPU overhead ratio, backup

```
C2 = CPU(M2) / CPU(M1)
```

`1.00` means free; `2.00` means encryption doubles the CPU spent backing up.

### C3 — Wall-clock overhead ratio, backup

```
C3 = Wall(M2) / Wall(M1)
```

Compare C3 with C2. **If C3 is much lower than C2, the backup is network-bound**, and the CPU cost of
encryption is hidden behind transfer time — the usual case on a cloud backend. If C3 ≈ C2, the backup
is CPU-bound and encryption will be felt directly.

### C4 — Throughput, with and without

```
C4_plain = ( D / 1048576 ) / Wall(M1)      # MB/s
C4_cse   = ( D / 1048576 ) / Wall(M2)      # MB/s
```

### C5 — Storage overhead

```
C5 = ( S2 - S1 ) / S1 * 100                # percent
```

Expected to be tiny: the S3 Encryption Client adds one 16-byte authentication tag per object, plus
a few hundred bytes of object metadata that `aws s3 ls` does not count. **Beware**: with many small
SSTable components the relative overhead climbs, so quote C5 alongside the file count.

### C6 — Restore overhead

```
C6_cpu  = CPU(M6) / CPU(M5)
C6_wall = Wall(M6) / Wall(M5)
```

### C7 — Differential overhead

```
C7_cpu  = CPU(M4) / CPU(M3)
C7_wall = Wall(M4) / Wall(M3)
```

This one is not about cryptography. In differential mode the encrypted path reads **every previous
differential backup's manifest** instead of listing storage once, so C7 is expected to grow with the
number of retained backups. If C7_wall is high on a cluster with a long backup history, that is why.

### C8 — Peak memory

```
C8 = max RSS(M2) - max RSS(M1)             # MB
```

Expected to be bounded by `concurrent_transfers × 4 × multipart_chunksize`: an encrypted multipart
upload holds the current and next part in plaintext and the current and previous part in
ciphertext. If C8 grows with the size of the largest SSTable instead, something is buffering.

### C9 — Extrapolation to your production volume

```
Extra CPU-seconds per backup = C1 × (production dataset in GB)
Extra wall-clock             = ( C3 - 1 ) × (current backup duration)
Extra storage                = C5 % of current backup footprint
```

**Table R — results**

| Ref | Quantity | Value | Unit |
|---|---|---|---|
| C1 | CPU cost of encryption | | CPU-s / GB |
| C2 | CPU ratio, backup | | × |
| C3 | Wall ratio, backup | | × |
| C4_plain | Throughput without | | MB/s |
| C4_cse | Throughput with | | MB/s |
| C5 | Storage overhead | | % |
| C6_cpu | CPU ratio, restore | | × |
| C6_wall | Wall ratio, restore | | × |
| C7_cpu | CPU ratio, differential | | × |
| C7_wall | Wall ratio, differential | | × |
| C8 | Extra peak memory | | MB |

---

## 4. Reading the results honestly

- **Run each measurement three times and keep the median.** A single run on a cluster that is also
  compacting tells you about compaction, not about encryption.
- **Watch out for compaction.** `nodetool compactionstats` must be empty before every measurement,
  otherwise the CPU column measures Cassandra, not Medusa.
- **The page cache favours the second run.** M2 runs after M1 on the same files. Either drop the
  cache between the two (`sync && echo 3 | sudo tee /proc/sys/vm/drop_caches`), or alternate the
  order across repetitions and say which you did.
- **C1 is the transferable number.** Wall clock depends on your network; CPU-seconds per gigabyte
  does not.
- **The measurement does not include key management.** Reading a key from a secrets manager on each
  invocation can cost more than the encryption itself on a small dataset.
