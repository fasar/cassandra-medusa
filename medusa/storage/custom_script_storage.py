import asyncio
import json
import logging
import subprocess
from pathlib import Path

from medusa.storage.s3_base_storage import S3BaseStorage
from medusa.storage.abstract_storage import ManifestObject, AbstractStorage, AbstractBlob


class CustomScriptStorage(S3BaseStorage):
    def __init__(self, config):
        super().__init__(config)
        self.upload_script = config.upload_script
        self.download_script = config.download_script

        if not self.upload_script or not self.download_script:
            raise ValueError(
                "Both 'upload_script' and 'download_script' must be specified in the "
                "storage configuration when using the 'custom_script' provider."
            )

    async def _upload_blob(self, src: str, dest: str) -> ManifestObject:
        """
        Executes the upload script to process and transfer the file.
        The script must output a JSON object to stdout containing:
        {'remote_md5': '...', 'remote_size': int}
        """
        src_path = Path(src)
        if not src_path.exists():
            raise IOError(f"Source file {src} does not exist")

        # Generate original file attributes
        original_size = src_path.stat().st_size
        original_md5 = AbstractStorage.generate_md5_hash(src_path)

        cmd = [self.upload_script, str(src), dest]
        logging.debug(f"Executing upload script: {' '.join(cmd)}")

        loop = asyncio.get_event_loop()
        try:
            # Run in executor to not block the asyncio loop
            process = await loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, check=True)
            )
        except subprocess.CalledProcessError as e:
            logging.error(f"Upload script failed with error: {e.stderr}")
            raise RuntimeError(f"Custom script upload failed for {src}") from e

        try:
            # Parse the stdout from the script
            result = json.loads(process.stdout)
            remote_md5 = result.get("remote_md5")
            remote_size = result.get("remote_size")
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON from upload script output: {process.stdout}")
            raise RuntimeError(f"Upload script did not output valid JSON for {src}") from e

        # Return a ManifestObject enriched with the remote properties
        return ManifestObject(
            path=Path(dest).name,
            size=original_size,
            MD5=original_md5,
            remote_size=remote_size,
            remote_md5=remote_md5
        )

    def _download_blob(self, src: str, dest: str):
        """
        Executes the download script to retrieve and process the file.
        """
        cmd = [self.download_script, src, str(dest)]
        logging.debug(f"Executing download script: {' '.join(cmd)}")

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Download script failed with error: {e.stderr}")
            raise RuntimeError(f"Custom script download failed for {src}") from e

    async def _download_encrypted_blob(self, src: str, dest: str):
        """
        Reroute encrypted blob download to the custom download script.
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._download_blob, src, dest)

    @staticmethod
    def blob_matches_manifest(blob: AbstractBlob, object_in_manifest: dict, enable_md5_checks=False):
        """
        Compares a remote blob against the properties saved in the manifest.
        If custom remote attributes were saved, use them; otherwise, fall back to the original file attributes.
        """
        expected_size = object_in_manifest.get('remote_size', object_in_manifest['size'])
        expected_md5 = object_in_manifest.get('remote_md5', object_in_manifest['MD5'])

        object_in_manifest_copy = object_in_manifest.copy()
        object_in_manifest_copy['size'] = expected_size
        object_in_manifest_copy['MD5'] = expected_md5
        return S3BaseStorage.blob_matches_manifest(blob, object_in_manifest_copy, enable_md5_checks)
