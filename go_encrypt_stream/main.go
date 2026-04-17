package main

import (
	"context"
	"crypto/rand"
	"io"
	"log"
	"os"

	"github.com/chainifynet/aws-encryption-sdk-go/pkg/client"
	"github.com/chainifynet/aws-encryption-sdk-go/pkg/clientconfig"
	"github.com/chainifynet/aws-encryption-sdk-go/pkg/materials"
	"github.com/chainifynet/aws-encryption-sdk-go/pkg/providers/rawprovider"
	"github.com/chainifynet/aws-encryption-sdk-go/pkg/suite"
)

func main() {
	// 1. Initialize the Client
	cfg, err := clientconfig.NewConfigWithOpts(clientconfig.WithCommitmentPolicy(suite.CommitmentPolicyRequireEncryptRequireDecrypt))
	if err != nil {
		log.Fatalf("failed to create client config: %v", err)
	}
	c := client.NewClientWithConfig(cfg)

	// 2. Generate a 256-bit (32 bytes) random dummy key
	key := make([]byte, 32)
	_, err = rand.Read(key)
	if err != nil {
		log.Fatalf("failed to read random bytes: %v", err)
	}

	// 3. Create a Raw AES Key Provider
	kp, err := rawprovider.NewWithOpts("medusa-backup", rawprovider.WithStaticKey("raw-aes-key", key))
	if err != nil {
		log.Fatalf("failed to create provider: %v", err)
	}

	// 4. Create materials manager with the provider
	cmm, err := materials.NewDefault(kp)
	if err != nil {
		log.Fatalf("failed to create cmm: %v", err)
	}

	// 5. Read from Stdin and Encrypt in Chunks to avoid OOM
	// We use 8MB chunks to simulate the frame size and maintain steady CPU work
	chunkSize := 8 * 1024 * 1024
	buffer := make([]byte, chunkSize)

	for {
		n, err := io.ReadFull(os.Stdin, buffer)
		if n > 0 {
			// Encrypt the chunk
			// We only encrypt the part of the buffer that was read
			ciphertext, _, errEncrypt := c.Encrypt(
				context.Background(),
				buffer[:n],
				nil, // no specific encryption context
				cmm,
				client.WithAlgorithm(suite.AES_256_GCM_HKDF_SHA512_COMMIT_KEY),
				client.WithFrameLength(8388608), // Use frame length integer directly
			)
			if errEncrypt != nil {
				log.Fatalf("failed to encrypt chunk: %v", errEncrypt)
			}

			// Write to Stdout
			_, errWrite := os.Stdout.Write(ciphertext)
			if errWrite != nil {
				log.Fatalf("failed to write stdout: %v", errWrite)
			}
		}

		if err == io.EOF || err == io.ErrUnexpectedEOF {
			break
		}
		if err != nil {
			log.Fatalf("error reading stdin: %v", err)
		}
	}
}
