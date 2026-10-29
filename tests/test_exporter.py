import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from PIL import Image
import exporter
import test_journal


class ExportTests(unittest.IsolatedAsyncioTestCase):
    def test_zip_parts_fit_and_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            parts = exporter.Parts(root, 1000)
            refs = [parts.add(f'images/{i}.png', b'original' * 50) for i in range(5)]
            parts.close()
            self.assertGreater(len(parts.paths), 1)
            for ref in refs:
                path = Path(root) / ref['part']
                self.assertLessEqual(path.stat().st_size, 1000)
                with zipfile.ZipFile(path) as archive:
                    self.assertEqual(archive.read(ref['path']), b'original' * 50)
            with self.assertRaises(ValueError):
                parts.add('oversized.png', b'x' * 2000)

    async def test_image_only_message_and_failure_manifest(self):
        msg = test_journal.message(attachments=[test_journal.attachment(), test_journal.attachment(2)])
        msg.author.id = 99
        msg.reference = NS(message_id=42)
        msg.jump_url = 'https://discord.com/channels/123/456/100'
        async def history(**kwargs):
            yield msg
        channel = NS(id=456, name='charts', history=history)
        with tempfile.TemporaryDirectory() as root, patch.object(exporter, 'original_image', AsyncMock(side_effect=[(b'original', 'png'), ValueError('too large')])):
            counts, count = await exporter.build(root, {456: channel}, lambda _: True, None, None, 7000, lambda _: None)
            self.assertEqual((counts['messages'], counts['images'], counts['failed_images']), (1, 1, 1))
            entries = {}
            for path in Path(root).glob('*.zip'):
                with zipfile.ZipFile(path) as archive:
                    entries.update({name: archive.read(name) for name in archive.namelist()})
            record = json.loads(entries['messages/456/100.json'])
            self.assertEqual(record['text'], '')
            self.assertEqual(record['reply_to'], '42')
            self.assertEqual(entries[record['images'][0]['path']], b'original')
            self.assertIn('too large', record['images'][1]['error'])
            self.assertEqual(json.loads(entries['manifest.json'])['images'], 1)

    async def test_denied_channel_is_not_read(self):
        channel = NS(id=456, history=AsyncMock())
        with tempfile.TemporaryDirectory() as root:
            counts, _ = await exporter.build(root, {456: channel}, lambda _: False, None, None, 7000, lambda _: None)
        channel.history.assert_not_called()
        self.assertEqual(counts['messages'], 0)
        self.assertTrue(counts['errors'])

    async def test_export_commands_reject_nonowner(self):
        b = test_journal.b
        for command in (b.export_command, b.export_status, b.export_download):
            interaction = NS(guild=NS(id=123, owner_id=99), user=NS(id=1), response=NS(send_message=AsyncMock()))
            await command.callback(interaction)
            self.assertIn('owner', interaction.response.send_message.call_args.args[0])

    async def test_original_image_preserves_full_resolution(self):
        image = Image.new('RGB', (3000, 100)); out = io.BytesIO(); image.save(out, format='PNG')
        data = out.getvalue()
        async def chunks(_):
            yield data
        response = NS(status=200, content_length=len(data), content=NS(iter_chunked=chunks))
        class Context:
            async def __aenter__(self): return response
            async def __aexit__(self, *args): pass
        session = NS(get=lambda *args, **kwargs: Context())
        result, suffix = await exporter.original_image(session, {'url': test_journal.attachment().url}, 100000)
        self.assertEqual(result, data)
        self.assertEqual(suffix, 'png')
        with self.assertRaises(ValueError):
            await exporter.original_image(session, {'url': 'https://example.com/private'}, 100000)

    async def test_download_rechecks_permissions(self):
        b = test_journal.b
        interaction = NS(guild=NS(id=123, owner_id=99), user=NS(id=99),
                         response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        with tempfile.TemporaryDirectory() as root, patch.object(b, 'EXPORT_STATE', Path(root) / 'state.json'), \
             patch.object(b.bot, 'fetch_channel', AsyncMock(return_value=object())), patch.object(b, 'permitted', return_value=False):
            b.export_state('Finished', parts=1, channels=[456])
            await b.export_download.callback(interaction, 1)
            self.assertIn('access changed', interaction.followup.send.call_args.args[0])


class TerminologyTests(unittest.TestCase):
    def test_counts_are_message_counts_and_exclude_model_notes(self):
        import journal
        rows = [(1, 2, 'owner', 'date', 'SMT SMT with LRL\n[Image observation]\nNVDA'),
                (2, 2, 'owner', 'date', 'smt LRL\n[Image observation]\nNVDA')]
        report = journal.terminology_report(rows, 123)
        self.assertIn('| smt | 2 |', report)
        self.assertIn('| lrl | 2 |', report)
        self.assertNotIn('| nvda |', report)
        self.assertIn('https://discord.com/channels/123/2/1', report)
