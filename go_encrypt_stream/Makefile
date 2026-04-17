# Makefile for building the AWS Encryption SDK Go test application on RedHat 9

APP_NAME = encrypt_stream
GO = go

.PHONY: all deps build test clean

all: deps build test

deps:
	@echo "Downloading dependencies..."
	GOPROXY=https://proxy.golang.org $(GO) get github.com/chainifynet/aws-encryption-sdk-go
	$(GO) mod tidy

build:
	@echo "Compiling the application..."
	$(GO) build -o $(APP_NAME) main.go

test: build
	@echo "Running a simple test..."
	echo "test pipeline" | ./$(APP_NAME) | wc -c

clean:
	@echo "Cleaning up..."
	rm -f $(APP_NAME)
