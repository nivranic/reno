"""Test isolation: allow only the original Windows Proactor self-pipe IPC.

The same fixed hook is installed in the pytest process and dedicated native
test children. It grants no product capability and records no stack/locals.
"""
import os
import socket
import sys


def install():
    if os.name == "nt":
        from asyncio import proactor_events
        fallback_code = socket._fallback_socketpair.__code__
        pipe_code = proactor_events.BaseProactorEventLoop._make_self_pipe.__code__
    else:
        fallback_code = pipe_code = None

    def audit(event, args):
        if event not in {"socket.connect", "socket.getaddrinfo", "socket.bind"}:
            return
        if os.name == "nt" and event in {"socket.bind", "socket.connect"}:
            frame = sys._getframe(1)
            address = args[1]
            if (frame.f_code is fallback_code and frame.f_back is not None
                    and frame.f_back.f_code is pipe_code
                    and type(address) is tuple and len(address) == 2
                    and type(address[0]) is str and address[0] == "127.0.0.1"
                    and type(address[1]) is int
                    and (address[1] == 0 if event == "socket.bind" else 0 < address[1] <= 65535)
                    and args[0].family == socket.AF_INET and args[0].type == socket.SOCK_STREAM):
                return
        raise RuntimeError("Only the original Windows Proactor self-pipe IPC is permitted")

    sys.addaudithook(audit)
