#!/usr/bin/env python3
import sys
import base64
import os
from medusa.storage.encryption import EncryptedStream

def main():
    # Read key from argument or generate one
    key = sys.argv[1] if len(sys.argv) > 1 else base64.b64encode(os.urandom(32)).decode('utf-8')

    input_stream = sys.stdin.buffer
    output_stream = sys.stdout.buffer

    try:
        encrypted_stream = EncryptedStream(input_stream, key)

        # Read and write in chunks to avoid loading everything into memory
        chunk_size = 1024 * 1024 # 1MB chunk size

        while True:
            chunk = encrypted_stream.read(chunk_size)
            if not chunk:
                break
            output_stream.write(chunk)

    except BrokenPipeError:
        # Happens when pv or another tool closes the pipe early
        sys.stderr.close()
    finally:
        if 'encrypted_stream' in locals():
            encrypted_stream.close()

if __name__ == "__main__":
    main()
