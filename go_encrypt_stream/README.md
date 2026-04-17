# Go Encryption Stream Test

This application was created to benchmark the performance of the AWS Encryption SDK for Go compared to the Python implementation.
It encrypts data from standard input (`stdin`) and outputs the ciphertext to standard output (`stdout`).

## Prerequisites (RedHat 9)
1. Install Go:
   ```bash
   sudo dnf install -y golang
   ```

## Usage
1. Download dependencies:
   ```bash
   make deps
   ```
2. Build the application:
   ```bash
   make build
   ```
3. Run the application:
   ```bash
   tar cf - /data | ./encrypt_stream | pv > /dev/null
   ```
