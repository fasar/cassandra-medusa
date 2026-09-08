# -*- coding: utf-8 -*-
# Copyright 2026 DataStax, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
An in-memory S3 that answers a real boto3 client at the HTTP layer.

It registers as a before-send handler, the last thing botocore runs before opening a socket, so
everything above it is genuine: serialization, the S3 Encryption Client's before-call and
after-call hooks, checksums, response parsing. That is what makes it possible to exercise
encryption end to end - put, multipart, get, metadata round trip - without a MinIO.

Only what Medusa uses is implemented: put/get/head/delete object, list_objects_v2, and the
multipart upload calls, with path-style URLs.
"""

import datetime
import hashlib
import io
import uuid

from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree

import boto3

from botocore.awsrequest import AWSResponse
from botocore.config import Config
from urllib3.response import HTTPResponse

S3_XMLNS = 'http://s3.amazonaws.com/doc/2006-03-01/'
METADATA_PREFIX = 'x-amz-meta-'
# Request headers other than metadata that tests assert on, kept per object as sent.
RECORDED_HEADERS = (
    'x-amz-storage-class', 'x-amz-server-side-encryption', 'x-amz-server-side-encryption-aws-kms-key-id'
)


def make_client(endpoint_url='http://127.0.0.1:1'):
    """A boto3 client that can only ever talk to a FakeS3: unroutable endpoint, no retries."""
    return boto3.client(
        's3',
        endpoint_url=endpoint_url,
        aws_access_key_id='fake-access-key',
        aws_secret_access_key='fake-secret-key',
        region_name='us-east-1',
        config=Config(s3={'addressing_style': 'path'}, retries={'max_attempts': 1}),
    )


class FakeS3:

    def __init__(self):
        self.objects = {}
        self.uploads = {}
        # (method, key, type of the request body) for every request, so tests can assert on
        # which operations ran and on what was handed to the transport
        self.requests = []

    def attach(self, client):
        client.meta.events.register('before-send.s3', self)
        return self

    # --- helpers for tests -------------------------------------------------------------------

    def get(self, bucket, key):
        return self.objects[(bucket, key)]

    def keys(self, bucket):
        return sorted(k for (b, k) in self.objects if b == bucket)

    def store(self, bucket, key, body, metadata=None):
        self.objects[(bucket, key)] = {
            'body': bytes(body),
            'metadata': dict(metadata or {}),
            'headers': {},
            'etag': '"{}"'.format(hashlib.md5(body).hexdigest()),
            'last_modified': datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        }

    # --- the handler -------------------------------------------------------------------------

    def __call__(self, request, **kwargs):
        url = urlparse(request.url)
        path = unquote(url.path).lstrip('/')
        bucket, _, key = path.partition('/')
        query = parse_qs(url.query, keep_blank_values=True)
        body = request.body
        body_type = type(body)
        if body is not None and hasattr(body, 'read'):
            # botocore streams bodies in blocks; so does this, so that a stream whose read()
            # requires a size (s3transfer's bandwidth limited stream) works here too
            chunks = []
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            body = b''.join(chunks)
        body = body or b''
        self.requests.append((request.method, key, body_type))
        headers = {k.lower(): (v.decode('utf-8') if isinstance(v, bytes) else v) for k, v in request.headers.items()}

        if request.method == 'POST' and 'uploads' in query:
            return self._create_multipart_upload(request, bucket, key, headers)
        if request.method == 'PUT' and 'uploadId' in query:
            return self._upload_part(request, query, body)
        if request.method == 'POST' and 'uploadId' in query:
            return self._complete_multipart_upload(request, bucket, key, query, body)
        if request.method == 'DELETE' and 'uploadId' in query:
            self.uploads.pop(query['uploadId'][0], None)
            return self._respond(request, 204)
        if request.method == 'PUT':
            return self._put_object(request, bucket, key, headers, body)
        if request.method == 'GET' and not key:
            return self._list_objects(request, bucket, query)
        if request.method == 'GET':
            return self._get_object(request, bucket, key, headers)
        if request.method == 'HEAD':
            return self._head_object(request, bucket, key)
        if request.method == 'DELETE':
            self.objects.pop((bucket, key), None)
            return self._respond(request, 204)
        return self._respond(request, 501)

    def _put_object(self, request, bucket, key, headers, body):
        self.store(bucket, key, body, self._metadata(headers))
        self.objects[(bucket, key)]['headers'] = {h: headers[h] for h in RECORDED_HEADERS if h in headers}
        return self._respond(request, 200, {'ETag': self.objects[(bucket, key)]['etag']})

    def _create_multipart_upload(self, request, bucket, key, headers):
        upload_id = uuid.uuid4().hex
        self.uploads[upload_id] = {
            'bucket': bucket, 'key': key, 'parts': {},
            'metadata': self._metadata(headers),
            'headers': {h: headers[h] for h in RECORDED_HEADERS if h in headers},
        }
        xml = '<InitiateMultipartUploadResult xmlns="{}"><Bucket>{}</Bucket><Key>{}</Key><UploadId>{}</UploadId>' \
              '</InitiateMultipartUploadResult>'.format(S3_XMLNS, bucket, key, upload_id)
        return self._respond(request, 200, body=xml.encode('utf-8'))

    def _upload_part(self, request, query, body):
        upload = self.uploads[query['uploadId'][0]]
        part_number = int(query['partNumber'][0])
        upload['parts'][part_number] = body
        return self._respond(request, 200, {'ETag': '"{}"'.format(hashlib.md5(body).hexdigest())})

    def _complete_multipart_upload(self, request, bucket, key, query, body):
        upload = self.uploads.pop(query['uploadId'][0])
        root = ElementTree.fromstring(body)
        numbers = [int(e.text) for e in root.iter('{%s}PartNumber' % S3_XMLNS)] or sorted(upload['parts'])
        parts = [upload['parts'][n] for n in numbers]
        self.store(bucket, key, b''.join(parts), upload['metadata'])
        obj = self.objects[(bucket, key)]
        obj['headers'] = upload['headers']
        digests = b''.join(hashlib.md5(p).digest() for p in parts)
        obj['etag'] = '"{}-{}"'.format(hashlib.md5(digests).hexdigest(), len(parts))
        xml = '<CompleteMultipartUploadResult xmlns="{}"><Bucket>{}</Bucket><Key>{}</Key><ETag>{}</ETag>' \
              '</CompleteMultipartUploadResult>'.format(S3_XMLNS, bucket, key, obj['etag'].replace('"', '&quot;'))
        return self._respond(request, 200, body=xml.encode('utf-8'))

    def _get_object(self, request, bucket, key, headers):
        obj = self.objects.get((bucket, key))
        if obj is None:
            return self._not_found(request)
        data = obj['body']
        status = 200
        extra = {}
        if 'range' in headers:
            start, _, end = headers['range'].replace('bytes=', '').partition('-')
            start = int(start)
            end = int(end) if end else len(data) - 1
            extra['Content-Range'] = 'bytes {}-{}/{}'.format(start, end, len(data))
            data = data[start:end + 1]
            status = 206
        return self._respond(request, status, dict(self._object_headers(obj), **extra), body=data)

    def _head_object(self, request, bucket, key):
        obj = self.objects.get((bucket, key))
        if obj is None:
            return self._respond(request, 404)
        return self._respond(request, 200, self._object_headers(obj), content_length=len(obj['body']))

    def _list_objects(self, request, bucket, query):
        prefix = query.get('prefix', [''])[0]
        contents = ''
        for (b, k), obj in sorted(self.objects.items()):
            if b != bucket or not k.startswith(prefix):
                continue
            contents += '<Contents><Key>{}</Key><Size>{}</Size><ETag>{}</ETag><LastModified>{}</LastModified>' \
                        '<StorageClass>STANDARD</StorageClass></Contents>'.format(
                            k, len(obj['body']), obj['etag'].replace('"', '&quot;'),
                            obj['last_modified'].strftime('%Y-%m-%dT%H:%M:%S.000Z'))
        xml = '<ListBucketResult xmlns="{}"><Name>{}</Name><Prefix>{}</Prefix><IsTruncated>false</IsTruncated>{}' \
              '</ListBucketResult>'.format(S3_XMLNS, bucket, prefix, contents)
        return self._respond(request, 200, body=xml.encode('utf-8'))

    @staticmethod
    def _metadata(headers):
        return {k[len(METADATA_PREFIX):]: v for k, v in headers.items() if k.startswith(METADATA_PREFIX)}

    @staticmethod
    def _object_headers(obj):
        headers = {
            'ETag': obj['etag'],
            'Last-Modified': obj['last_modified'].strftime('%a, %d %b %Y %H:%M:%S GMT'),
            'Content-Type': 'binary/octet-stream',
        }
        headers.update(obj['headers'])
        for name, value in obj['metadata'].items():
            headers[METADATA_PREFIX + name] = value
        return headers

    def _not_found(self, request):
        xml = '<Error><Code>NoSuchKey</Code><Message>The specified key does not exist.</Message></Error>'
        return self._respond(request, 404, body=xml.encode('utf-8'))

    @staticmethod
    def _respond(request, status, headers=None, body=b'', content_length=None):
        headers = dict(headers or {})
        headers['Content-Length'] = str(len(body) if content_length is None else content_length)
        raw = HTTPResponse(body=io.BytesIO(body), status=status, headers=headers, preload_content=False,
                           enforce_content_length=False)
        return AWSResponse(request.url, status, headers, raw)
