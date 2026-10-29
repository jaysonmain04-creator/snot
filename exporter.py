"""Original-image, disk-backed Discord exports. No OpenAI calls."""
import asyncio
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import aiohttp
from PIL import Image
import journal


class Parts:
    def __init__(self, root, limit, budget=1024 * 1024 * 1024):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.limit, self.budget = limit, budget
        self.total = 0
        self.paths = []
        self.archive = None
        self.size = 22

    def add(self, name, data):
        # Stored ZIP overhead: local header + central directory + filenames.
        cost = len(data) + 76 + 2 * len(name.encode('utf-8'))
        if cost + 22 > self.limit:
            raise ValueError('File exceeds ZIP part limit; use a smaller image')
        if self.total + cost + 22 > self.budget:
            raise ValueError('Export reached 1 GiB storage limit; use a smaller date range')
        if self.archive is None or self.size + cost > self.limit:
            self.close()
            path = self.root / f'journal-part-{len(self.paths)+1:04d}.zip'
            self.paths.append(path)
            self.archive = zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED)
            self.size = 22
            self.total += 22
        self.archive.writestr(name, data)
        self.size += cost
        self.total += cost
        return {'part': self.paths[-1].name, 'path': name}

    def close(self):
        if self.archive:
            self.archive.close()
            self.archive = None


async def original_image(session, source, limit):
    if not journal.safe_url(source['url']):
        raise ValueError('External image has no supported Discord proxy')
    if source.get('size') and source['size'] > limit:
        raise ValueError('Image exceeds download/ZIP part limit')
    async with session.get(source['url'], allow_redirects=False,
                           timeout=aiohttp.ClientTimeout(total=40)) as response:
        if response.status != 200:
            raise ValueError(f'Image download returned HTTP {response.status}')
        if response.content_length and response.content_length > limit:
            raise ValueError('Image exceeds download/ZIP part limit')
        data = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('Image exceeds download/ZIP part limit')
    # Validate image type, but preserve original bytes, dimensions, and animation.
    with Image.open(io.BytesIO(data)) as image:
        suffix = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp', 'GIF': 'gif'}.get(image.format)
        if not suffix:
            raise ValueError('Unsupported image format')
        image.verify()
    return bytes(data), suffix


async def build(root, channels, permitted, start, end, limit, progress, discovery_status=""):
    parts = Parts(root, limit)
    counts = {'messages': 0, 'images': 0, 'failed_images': 0, 'channels': [], 'errors': [], 'discovery_status': discovery_status}
    try:
        async with aiohttp.ClientSession() as session:
            for channel in channels.values():
                if not permitted(channel):
                    counts['errors'].append(f'Channel {channel.id}: no access')
                    continue
                counts['channels'].append(channel.id)
                try:
                    # Fetch live messages so signed image URLs are fresh and deleted text is absent.
                    async for msg in channel.history(limit=None, after=start - timedelta(milliseconds=1) if start else None, before=end, oldest_first=True):
                        if start and msg.created_at < start:
                            continue
                        if end and msg.created_at >= end:
                            continue
                        record = {'id': str(msg.id), 'channel_id': str(channel.id),
                                  'channel': channel.name, 'author': str(msg.author),
                                  'author_id': str(msg.author.id), 'date': msg.created_at.isoformat(),
                                  'text': msg.content, 'url': msg.jump_url,
                                  'reply_to': str(msg.reference.message_id) if msg.reference and msg.reference.message_id else None,
                                  'attachments': [{'name': a.filename, 'url': a.url, 'size': a.size} for a in msg.attachments],
                                  'images': []}
                        for index, source in enumerate(journal.image_sources(msg).values()):
                            item = {'original_name': source['name'], 'source_url': source['url']}
                            try:
                                data, suffix = await original_image(session, source, min(journal.MAX_BYTES, limit - 2048))
                                item.update(parts.add(f'images/{channel.id}/{msg.id}-{index}.{suffix}', data))
                                counts['images'] += 1
                            except (ValueError, OSError, aiohttp.ClientError, asyncio.TimeoutError, Image.DecompressionBombError) as exc:
                                item['error'] = str(exc)[:200]
                                counts['failed_images'] += 1
                            record['images'].append(item)
                        parts.add(f'messages/{channel.id}/{msg.id}.json', json.dumps(record, ensure_ascii=False, indent=2).encode())
                        counts['messages'] += 1
                        progress(f"Running: #{channel.name}; {counts['messages']} messages, {counts['images']} images, {counts['failed_images']} skipped images")
                        await asyncio.sleep(0)
                except aiohttp.ClientError:
                    raise
                except Exception as exc:
                    # A storage/packaging error must stop; channel HTTP failures may continue.
                    import discord
                    if not isinstance(exc, discord.HTTPException):
                        raise
                    counts['errors'].append(f'Channel {channel.id}: {type(exc).__name__}')
        parts.add('manifest.json', json.dumps(counts, indent=2).encode())
        parts.add('README.txt', b'Upload all ZIP parts to ChatGPT. Each messages/<channel>/<id>.json contains text, author, date, source link, reply ID and image paths with their ZIP part names. Images retain downloaded original bytes. Reply targets outside the date range are not included. Non-image attachments are metadata only. See manifest.json and per-image errors for incomplete coverage. Dates are UTC. No OpenAI calls were made. Extract all parts into the same folder to combine them.')
    finally:
        parts.close()
    return counts, len(parts.paths)
