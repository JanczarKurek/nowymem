import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import logging
from typing import Optional
from collections import deque
import subprocess as sub
import signal
import json
import argparse
from random import choice

from aiohttp.web_request import Request
from aiohttp.web_response import Response
from aiohttp.web_fileresponse import FileResponse

import jinja2
import aiohttp_jinja2
import aiohttp.web as web

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('').setLevel("DEBUG")

class MemeStatus(Enum):
    NEW = 'NEW'
    NORMAL = 'NORMAL'
    PENDING = 'PENDING'
    RETRACTED = 'RETRACTED'


@dataclass(frozen=True)
class Meme:
    path: Path
    status: MemeStatus


class MemeQueue:
    
    BAD_STATUSES = [MemeStatus.PENDING, MemeStatus.RETRACTED]
    
    def __init__(self):
        self._memes: dict[Path, Meme] = {}
        self._memes_queue: deque[Path] = deque()
        self._displayed_memes = []
    
    def add_meme(self, meme_path: Path, is_init=False):
        if is_init:
            try:
                info = json.load(open("meme_info"))
            except FileNotFoundError:
                info = {}
        else:
            info = {}
        if meme_path not in self._memes.keys():
            meme_status = info.get(meme_path, MemeStatus.NEW) if is_init else MemeStatus.NEW
            if meme_status not in self.BAD_STATUSES and is_init:
                meme_status = MemeStatus.NORMAL
            meme = Meme(meme_path, meme_status)
            self._memes[meme_path] = meme
            self._memes_queue.append(meme_path)

    def dump_bad_memes(self):
        json.dump({
           str(meme.path) : meme.status.name for meme in self.memes
        }, open("meme_info", 'w'))

    def _change_status(self, meme_path: Path, status: MemeStatus):
        self._memes[meme_path] = Meme(self._memes[meme_path].path, status)
    
    def block_meme(self, meme_path: Path):
        self._change_status(meme_path, MemeStatus.PENDING)
    
    def next_meme(self) -> Optional[Meme]:
        while True:
            if not self._memes_queue:
                return None
            meme_path = self._memes_queue.pop()
            if self._memes[meme_path].status in self.BAD_STATUSES:
                continue
            if not meme_path.is_file():
                del self._memes[meme_path]
                continue
            break
        meme = self._memes[meme_path]
        self._change_status(meme_path, MemeStatus.NORMAL)
        self._displayed_memes.append(meme)
        self._memes_queue.appendleft(meme.path)
        return meme
    
    def get_last_memes(self, cnt: int):
        return self._displayed_memes[-cnt:]
    
    @property
    def memes(self):
        return list(self._memes.values())


class MemeDisplay:
    
    def __init__(self):
        self._current_commercial = None
    
    async def display_meme(self, meme: Meme):
        print(f"{meme}")
        args = ['feh', f'{meme.path}', '--bg-max']
        sub.run(args)
        if meme.status == MemeStatus.NEW:
            args = ["cvlc", "nowymem.wav", "--play-and-exit"]
            proc = await asyncio.create_subprocess_exec(*args)
            await proc.communicate()

    async def display_commercial(self, commercial: Path):
        print(f"{commercial}")
        if not commercial:
            return
        args = ["cvlc", "--video-wallpaper", "--play-and-exit", f"{commercial}"]
        proc = await asyncio.create_subprocess_exec(*args)
        self._current_commercial = proc
        await proc.communicate()
        self._current_commercial = None
    
    async def kill_commercial(self):
        if self._current_commercial:
            self._current_commercial.kill()
            self._current_commercial = None


class MemeWatcher:
    
    def __init__(self, display_time=5., directory: str = '.', commercial_rate=30, commercial_directory=None):
        self._display_time: float = display_time
        self.directory = Path(directory)
        self.meme_queue = MemeQueue()
        self._ensure_commercial = False
        self._meme_displayer = MemeDisplay()
        self._commercial_rate = commercial_rate
        self._commercial_directory = Path(commercial_directory) if commercial_directory else None
    
    def get_random_commercial(self):
        commercials = list(self._commercial_directory.iterdir())
        if not commercials:
            return None
        return choice(commercials)
    
    async def kill_commercial(self):
        await self._meme_displayer.kill_commercial()
    
    def ask_for_commercial(self):
        self._ensure_commercial = True

    async def watch_memes(self):
        meme_display = self._meme_displayer
        meme_cnt = 1
        for meme_path in self.directory.iterdir():
            self.meme_queue.add_meme(meme_path, is_init=True)
        while True:
            for meme_path in self.directory.iterdir():
                self.meme_queue.add_meme(meme_path)
            if self._commercial_directory and not (meme_cnt % self._commercial_rate):
                await meme_display.display_commercial(self.get_random_commercial())
            if self._ensure_commercial:
                self._ensure_commercial = False
                meme_cnt = 0
                await meme_display.display_commercial(self.get_random_commercial())
            else:
                meme = self.meme_queue.next_meme()
                await meme_display.display_meme(meme)
            meme_cnt += 1
            await asyncio.sleep(self._display_time)


class MemeServer:
    
    def __init__(self, meme_watcher: MemeWatcher):
        self._meme_watcher = meme_watcher
        self._app = web.Application()
        self._jinja = aiohttp_jinja2.setup(
            self._app, loader=jinja2.FileSystemLoader(str(Path("templates").absolute()))
        )

    async def list_recent_memes(self, request: Request) -> Response:
        memes = [str(meme.path.name) for meme in self._meme_watcher.meme_queue.get_last_memes(10)]
        return aiohttp_jinja2.render_template(
            "list_of_memes.html",
            request,
            context={
                "memes": memes
            }
        )
    
    async def report_meme(self, request: Request) -> Response:
        meme_path = self._meme_watcher.directory / Path(request.match_info['meme_name'])
        self._meme_watcher.meme_queue.block_meme(meme_path)
        return Response(text="OK!")

    async def serve_meme(self, request: Request):
        meme_path = Path(self._meme_watcher.directory / request.match_info['meme'])
        print("meme_path")
        return FileResponse(meme_path)
    
    async def kill_commercial(self, request: Request):
        await self._meme_watcher.kill_commercial()
        return Response(text="Ok!")
    
    async def plz_show_commercial(self, request: Request):
        self._meme_watcher.ask_for_commercial()
        return Response(text="Ok!")
        
    async def last_meme(self, request: Request):
        meme_path = self._meme_watcher.meme_queue.get_last_memes(1)
        if not meme_path:
            return Response(text="No meme for u")
        else:
            return Response(text=str(meme_path[0].path.name))

    async def _cleanup(self, app):
        self._meme_watcher.meme_queue.dump_bad_memes()

    async def serve(self, hostname='0.0.0.0', port=8080):
        self._app.add_routes([
            web.get('/', self.list_recent_memes),
            web.post('/report/{meme_name}', self.report_meme),
            web.get(f"/memes/{{meme}}", self.serve_meme),
            web.get('/last_meme', self.last_meme),
            web.post('/kill_commercial', self.kill_commercial),
            web.post('/ask_commercial', self.plz_show_commercial),
        ])
        self._app.on_shutdown.append(self._cleanup)
        await web._run_app(self._app, host=hostname, port=port)


async def main(args):
    meme_watcher = MemeWatcher(args.duration, directory=args.directory,
        commercial_directory=args.commercial_dir, commercial_rate=args.commercial_rate,
    )
    await asyncio.gather(
        meme_watcher.watch_memes(),
        MemeServer(meme_watcher).serve(args.hostname, args.port),
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser('nowymem')
    parser.add_argument('--hostname', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--duration', type=float, default=5)
    parser.add_argument('--commercial-dir')
    parser.add_argument('--commercial-rate', type=int)
    parser.add_argument('directory')
    args = parser.parse_args()
    asyncio.run(main(args))
