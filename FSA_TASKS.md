

## Setup env

```bash
export PYENV_VERSION=
pyenv local p314-medusa
cd .
```

## Notes à indiqué pour le pull request

aws-cryptographic-material-providers requires Python <4.0.0,>=3.11.0, so it will not be installable for Python >=3.10.0,<3.11.0

Passage en 3.11 car aws-cryptographic-material-providers requires Python >=3.11.0


pyenv shell p314-medusa
poetry add  aws-cryptographic-material-providers
poetry add  aws-encryption-sdk[raw]




python -m pytest tests/storage/encryption_test.py -v


./run_integration_tests.sh --minio --no-local --cassandra-version=4.1.9 --test="1,27,30"