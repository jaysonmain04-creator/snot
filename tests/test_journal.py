import asyncio
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from PIL import Image
import journal

# Offline placeholders only; tests never connect to Discord/OpenAI.
os.environ.update(DISCORD_GUILD_ID='123', OPENAI_API_KEY='offline-test', DATA_DIR=tempfile.mkdtemp())
for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
    os.environ.pop(name, None)
import bot as b


def message(mid=100, text='', attachments=None):
    return NS(id=mid, content=text, author=NS(bot=False), channel=NS(id=456),
              created_at=datetime(2026,10,28,tzinfo=timezone.utc), attachments=attachments or [], embeds=[])


def attachment(aid=1):
    return NS(id=aid, url=f'https://cdn.discordapp.com/attachments/456/{aid}/chart.png?ex=1',
              filename='chart.png', content_type='image/png', size=100)


class JournalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        b.db.execute('DELETE FROM messages'); b.db.execute('DELETE FROM review_state'); b.db.commit()
        self.channel = NS(id=456, name='journal', guild=NS(id=123, me=object()),
                          permissions_for=lambda _: NS(view_channel=True,read_message_history=True))
        b.channels.clear(); b.channels[456]=self.channel
        b.bot.get_channel=lambda _: self.channel

    def test_image_only_and_search_and_edits(self):
        msg=message(attachments=[attachment()]); b.save(msg)
        self.assertEqual(b.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],1)
        b.db.execute("UPDATE images SET caption='NVDA breakout plan'")
        journal.reindex(b.db,100)
        self.assertEqual(len(b.select_rows('NVDA')),1)
        msg.attachments[0].url=msg.attachments[0].url.replace('ex=1','ex=2'); b.save(msg)
        self.assertEqual(b.db.execute('SELECT caption FROM images').fetchone()[0],'NVDA breakout plan')
        msg.content='changed plan'; b.save(msg)
        self.assertIsNone(b.db.execute('SELECT caption FROM images').fetchone()[0])
        msg.attachments=[]; b.save(msg)
        self.assertEqual(b.db.execute('SELECT COUNT(*) FROM images').fetchone()[0],0)
        self.assertEqual(b.select_rows('NVDA'),[])

    async def test_delete_cleans_images(self):
        b.save(message(attachments=[attachment()]))
        await b.on_raw_message_delete(NS(guild_id=123,message_id=100))
        self.assertEqual(b.db.execute('SELECT COUNT(*) FROM images').fetchone()[0],0)

    async def test_owner_and_permission_filter(self):
        interaction=NS(guild=NS(id=123,owner_id=99),user=NS(id=1),response=NS(send_message=AsyncMock()))
        self.assertFalse(await b.owner(interaction))
        b.save(message(text='NVDA'))
        self.channel.permissions_for=lambda _: NS(view_channel=False,read_message_history=True)
        self.assertEqual(b.select_rows('NVDA'),[])

    def test_image_normalization_and_url_boundary(self):
        image=Image.new('RGB',(3200,1000)); out=io.BytesIO();image.save(out,format='PNG')
        self.assertTrue(journal.normalize_image(out.getvalue()).startswith('data:image/png;base64,'))
        for url in ('http://127.0.0.1/a','https://example.com/a','https://cdn.discordapp.com.evil.test/a','https://cdn.discordapp.com:8443/a'):
            self.assertFalse(journal.safe_url(url))
        self.assertTrue(journal.safe_url(attachment().url))
        with self.assertRaises(ValueError): journal.normalize_image(b'not image bytes')

    def test_chunks_keep_all_text(self):
        records=['A'*8000,'B'*30000,'C'*100]
        parts=list(journal.chunks(records))
        self.assertEqual(''.join(parts),''.join(records))
        self.assertTrue(all(len(p)<=24000 for p in parts))

    async def test_real_vision_payload(self):
        client=NS(responses=NS(create=AsyncMock(return_value=NS(output_text='visible chart'))))
        await journal.describe_image(client,'gpt-4.1-mini','data:image/png;base64,AAAA','my entry plan')
        payload=client.responses.create.call_args.kwargs
        self.assertFalse(payload['store'])
        self.assertEqual(payload['input'][0]['content'][1]['type'],'input_image')
        self.assertEqual(payload['input'][0]['content'][1]['detail'],'high')

    async def test_full_review_and_cache_reuse(self):
        msg=message(attachments=[attachment()]); b.save(msg); b.db.commit()
        self.channel.fetch_message=AsyncMock(return_value=msg)
        async def scope(): return {456:self.channel}
        with patch.object(b,'sync_history',AsyncMock()), patch.object(b,'current_channels',scope), \
             patch.object(journal,'download_image',AsyncMock(return_value='data:image/png;base64,AAAA')) as download, \
             patch.object(journal,'describe_image',AsyncMock(return_value='NVDA chart, price unreadable')) as vision, \
             patch.object(b,'summarize_evidence',AsyncMock(return_value='Review with source')):
            await b.run_review('Review everything',None,None)
            state,report=b.db.execute('SELECT status,report FROM review_state').fetchone()
            self.assertTrue(state.startswith('Finished'))
            self.assertIn('1/1 images analyzed',report)
            await b.run_review('Review again',None,None)
            self.assertEqual(vision.await_count,1)
            self.assertEqual(download.await_count,1)

    async def test_failed_image_not_claimed_read(self):
        msg=message(attachments=[attachment()]); b.save(msg); b.db.commit()
        self.channel.fetch_message=AsyncMock(return_value=msg)
        with patch.object(b,'sync_history',AsyncMock()), patch.object(b,'current_channels',AsyncMock(return_value={456:self.channel})), \
             patch.object(journal,'download_image',AsyncMock(side_effect=ValueError('unreadable image'))), \
             patch.object(b,'summarize_evidence',AsyncMock(return_value='No visible evidence')):
            await b.run_review('Review',None,None)
            state,report=b.db.execute('SELECT status,report FROM review_state').fetchone()
            self.assertTrue(state.startswith('Finished'))
            self.assertIn('0/1 images analyzed',report)
            self.assertIn('unreadable image',report)

    def test_restart_and_migration(self):
        b.review_state('Running: images','partial report')
        journal.initialize(b.db)
        state,report=b.db.execute('SELECT status,report FROM review_state').fetchone()
        self.assertIn('Interrupted',state)
        self.assertEqual(report,'partial report')
        db=sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE messages(id INTEGER PRIMARY KEY,channel INTEGER,author TEXT,date TEXT,content TEXT); CREATE TABLE cursors(channel INTEGER PRIMARY KEY,last_id INTEGER); INSERT INTO messages VALUES(1,2,"a","date","old text"); INSERT INTO cursors VALUES(2,1);')
        journal.initialize(db)
        self.assertEqual(db.execute('SELECT raw_content FROM messages').fetchone()[0],'old text')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM cursors').fetchone()[0],0)
        db.close()

if __name__=='__main__': unittest.main()
