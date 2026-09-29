"""Validate pictures and replace refused inline images with BPS attachments."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import socket
import threading
import uuid
from collections import OrderedDict
from urllib import error, request

from bps_proxy.upstream import UpstreamError, attachment_url, upload_headers
from bps_proxy.network import open_url

log = logging.getLogger('bps_proxy')
CACHE_SIZE = 256
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_IMAGE_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 64 * 1024 * 1024
UPLOAD_RESPONSE_BYTES = 64 * 1024
PICTURE_RETRY_STATUSES = {400, 422}
_EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}


class ImageInputError(UpstreamError):
    def __init__(self, message: str) -> None:
        super().__init__(400, message)


class UploadError(UpstreamError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(status, message)


class UploadUnavailable(UploadError):
    pass


class Sent:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.inline: set[str] = set()
        self.file_ids: set[str] = set()
        self.uploaded: set[str] = set()
        self.reused: set[str] = set()

    @property
    def pictures(self) -> bool:
        return bool(self.inline or self.file_ids)


def _dimensions(data: bytes, mime: str) -> tuple[int, int]:
    if mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 33 and data[12:16] == b'IHDR':
        return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if mime == 'image/gif' and data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 13:
        return int.from_bytes(data[6:8], 'little'), int.from_bytes(data[8:10], 'little')
    if mime == 'image/webp' and len(data) >= 30 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        if data[12:16] == b'VP8X':
            return 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
        if data[12:16] == b'VP8L' and data[20] == 0x2f:
            bits = int.from_bytes(data[21:25], 'little')
            return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
        if data[12:16] == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            return int.from_bytes(data[26:28], 'little') & 0x3fff, int.from_bytes(data[28:30], 'little') & 0x3fff
    if mime == 'image/jpeg' and data.startswith(b'\xff\xd8'):
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 0xff:
                break
            while offset < len(data) and data[offset] == 0xff:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xd8, 0xd9, 0xda):
                break
            if marker == 0x01 or 0xd0 <= marker <= 0xd7:
                continue
            size = int.from_bytes(data[offset:offset + 2], 'big')
            if size < 2 or offset + size > len(data):
                break
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf) and size >= 8:
                return int.from_bytes(data[offset + 5:offset + 7], 'big'), int.from_bytes(data[offset + 3:offset + 5], 'big')
            offset += size
    raise ImageInputError('图片格式无效，或与声明的格式不一致')


def _decode_data_url(url: str) -> tuple[str, bytes]:
    header, sep, payload = url.partition(',')
    fields = header.lower().split(';')
    mime = fields[0][5:]
    if not sep or len(fields) != 2 or fields[1] != 'base64' or mime not in _EXTENSIONS:
        raise ImageInputError('图片须为 base64 编码的 PNG、JPEG、GIF 或 WebP')
    if len(payload) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ImageInputError('单张图片不能超过 20 MiB')
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageInputError('图片的 base64 编码无效') from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageInputError('图片为空或超过 20 MiB')
    width, height = _dimensions(data, mime)
    if not width or not height or width * height > MAX_PIXELS:
        raise ImageInputError('图片尺寸无效或像素数过大')
    return mime, data


def _data_url(part: dict) -> str | None:
    url = part.get('image_url')
    if isinstance(url, dict):
        url = url.get('url')
    return url if isinstance(url, str) and url.startswith('data:') else None


def _multipart(media_type: str, data: bytes, filename: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    prefix = (f'--{boundary}\r\n'
              f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
              f'Content-Type: {media_type}\r\n\r\n').encode()
    return prefix + data + f'\r\n--{boundary}--\r\n'.encode(), boundary


def upload(session, media_type: str, data: bytes, digest: str) -> str:
    payload, boundary = _multipart(media_type, data, f'picture-{digest[:12]}.{_EXTENSIONS[media_type]}')
    headers = upload_headers(session)
    headers['content-type'] = f'multipart/form-data; boundary={boundary}'
    req = request.Request(attachment_url(), data=payload, headers=headers, method='POST')
    try:
        with open_url(req, timeout=120) as response:
            raw = response.read(UPLOAD_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        status = exc.code if exc.code in (401, 403, 429) else 502
        exc.close()
        raise UploadError(f'图片上传失败（HTTP {exc.code}）', status) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise UploadUnavailable('图片上传超时', 504) from exc
    except (error.URLError, OSError) as exc:
        raise UploadUnavailable('无法连接图片上传服务') from exc
    if len(raw) > UPLOAD_RESPONSE_BYTES:
        raise UploadError('图片上传服务返回的数据过大')
    try:
        file_id = json.loads(raw.decode('utf-8')).get('openai_file_id')
    except (UnicodeError, ValueError, AttributeError):
        file_id = None
    if not isinstance(file_id, str) or not file_id.strip():
        raise UploadError('图片上传服务没有返回附件编号')
    log.info('uploaded picture bytes=%d', len(data))
    return file_id.strip()


class Pictures:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._uploads = [threading.Lock() for _ in range(16)]
        self._refused: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._ids: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._local = threading.local()

    @property
    def last(self) -> Sent | None:
        return getattr(self._local, 'last', None)

    def refuse_inline(self, account: str, kinds: set[str]) -> None:
        with self._lock:
            for kind in kinds:
                self._refused[(account, kind)] = None
                self._refused.move_to_end((account, kind))
            while len(self._refused) > CACHE_SIZE:
                self._refused.popitem(last=False)

    def forget(self, account: str, file_ids: set[str]) -> None:
        with self._lock:
            for key, value in list(self._ids.items()):
                if key[0] == account and value in file_ids:
                    del self._ids[key]

    def _file(self, session, mime: str, data: bytes, sent: Sent, fresh: set[str], before_upload=None) -> str:
        account = getattr(session, 'account_id', '')
        key = (account, hashlib.sha256(data).hexdigest())
        with self._uploads[int(key[1][:2], 16) % len(self._uploads)]:
            with self._lock:
                file_id = self._ids.get(key)
                if file_id:
                    self._ids.move_to_end(key)
            if file_id:
                if file_id not in fresh:
                    sent.reused.add(file_id)
            else:
                if before_upload is not None:
                    before_upload()
                file_id = upload(session, mime, data, key[1])
                with self._lock:
                    self._ids[key] = file_id
                    while len(self._ids) > CACHE_SIZE:
                        self._ids.popitem(last=False)
                fresh.add(file_id)
                sent.uploaded.add(file_id)
        sent.file_ids.add(file_id)
        return file_id

    def rewrite(self, body: dict, session, *, fresh: set[str] | None = None, before_upload=None) -> Sent:
        sent = Sent(body)
        self._local.last = sent
        items = body.get('input')
        if not isinstance(items, list):
            return sent
        # Validate the entire request before uploading anything.
        decoded: dict[str, tuple[str, bytes]] = {}
        total = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            parts = item.get('output') if item.get('type') in ('function_call_output', 'custom_tool_call_output') else item.get('content')
            for part in parts if isinstance(parts, list) else []:
                if not isinstance(part, dict) or part.get('type') != 'input_image':
                    continue
                url = _data_url(part)
                if url is not None:
                    if url not in decoded:
                        decoded[url] = _decode_data_url(url)
                    total += len(decoded[url][1])
                    if total > MAX_REQUEST_IMAGE_BYTES:
                        raise ImageInputError('请求中的图片合计超过 32 MiB')
        if not decoded:
            return sent
        account = getattr(session, 'account_id', '')
        with self._lock:
            refused = {kind for owner, kind in self._refused if owner == account}
        fresh = set(fresh or ())
        rewritten = []
        for item in items:
            if not isinstance(item, dict):
                rewritten.append(item)
                continue
            kind = str(item.get('type') or 'message')
            field = 'output' if kind in ('function_call_output', 'custom_tool_call_output') else 'content'
            parts = item.get(field)
            if not isinstance(parts, list):
                rewritten.append(item)
                continue
            content = []
            for part in parts:
                url = _data_url(part) if isinstance(part, dict) and part.get('type') == 'input_image' else None
                if url is None:
                    content.append(part)
                elif kind not in refused:
                    sent.inline.add(kind)
                    content.append(part)
                else:
                    mime, data = decoded[url]
                    file_id = self._file(session, mime, data, sent, fresh, before_upload)
                    picture = {key: value for key, value in part.items() if key != 'image_url'}
                    picture.setdefault('detail', 'auto')
                    content.append({**picture, 'file_id': file_id})
            rewritten.append({**item, field: content})
        sent.body = {**body, 'input': rewritten}
        return sent

    def apply(self, body: dict, session, *, fresh: set[str] | None = None) -> dict:
        return self.rewrite(body, session, fresh=fresh).body

    def retry_plan(self, status: int, sent: Sent | None = None) -> str | None:
        sent = sent or self.last
        if status not in PICTURE_RETRY_STATUSES or sent is None:
            return None
        if sent.inline:
            return 'upload'
        if sent.reused:
            return 'reupload'
        return None
