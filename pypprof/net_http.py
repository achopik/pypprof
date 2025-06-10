from __future__ import print_function

import gc
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import importlib_resources


import mprofile
from pypprof.builder import Builder
from pypprof import thread_profiler


def start_pprof_server(host='localhost', port=8080):
    '''Start a pprof server at the given host:port in a background thread.

    After calling this function, the pprof endpoints should be available
    at /debug/pprof/profile, etc.

    Returns the underlying HTTPServer. To stop the server, call shutdown().
    '''
    server = HTTPServer((host, port), PProfRequestHandler)
    bg_thread = threading.Thread(target=server.serve_forever)
    bg_thread.daemon = True
    bg_thread.start()
    return server


class PProfRequestHandler(BaseHTTPRequestHandler):
    '''Handle pprof endpoint requests a la Go's net/http/pprof.

    The following endpoints are implemented:
      - /debug/pprof: List the available profiles.
      - /debug/pprof/heap: Get snapshot of current heap profile.
      - /debug/pprof/thread (or /debug/pprof/goroutine): Currently running threads.
    '''
    def do_GET(self):
        url = urlparse(self.path)
        route = url.path.rstrip("/")
        qs = parse_qs(url.query)

        if route == "/debug/pprof":
            self.index()
        elif route == "/debug/pprof/profile":
            self.profile(qs)
        elif route == "/debug/pprof/wall":
            self.wall(qs)
        elif route == "/debug/pprof/heap":
            self.heap(qs)
        elif route in ("/debug/pprof/thread", "/debug/pprof/goroutine"):
            self.thread(qs)
        elif route == "/debug/pprof/cmdline":
            self.cmdline()
        else:
            self.send_error(404)

    def index(self):
        template = importlib_resources.files(__name__).joinpath("index.html").read_text("utf-8")
        body = template.format(num_threads=threading.active_count())

        self.send_response(200)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def heap(self, query):
        if not mprofile.is_tracing():
            return self.send_error(412, "Heap profiling is not enabled")
        if query.get("gc"):
            gc.collect()
        snap = mprofile.take_snapshot()
        pprof = build_heap_pprof(snap)
        self._send_profile(pprof)

    def thread(self, query):
        if query.get("debug"):
            self.send_response(200)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            for frame in sys._current_frames().values():
                for line in traceback.format_stack(frame):
                    self.wfile.write(line.encode("utf-8"))
                self.wfile.write("\n".encode("utf-8"))
        else:
            pprof = thread_profiler.take_snapshot()
            self._send_profile(pprof)

    def _send_profile(self, pprof):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", 'attachment; filename="profile"')
        self.end_headers()
        self.wfile.write(pprof)


def build_heap_pprof(snap):
  profile_builder = Builder()
  samples = {}  # trace => (count, measurement)
  for stat in snap.statistics('traceback'):
    trace = tuple((frame.name, frame.filename, frame.firstlineno, frame.lineno)
                  for frame in stat.traceback)
    try:
        samples[trace][0] += stat.count
        samples[trace][1] += stat.size
    except KeyError:
        samples[trace] = (stat.count, stat.size)
  profile_builder.populate_profile(samples, 'HEAP', 'bytes', snap.sample_rate, 1)
  return profile_builder.emit()
