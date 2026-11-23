"""Admission controls for the local, single-user PDF application."""
import os
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse


class LocalRequestGuard:
    def __init__(self, app, reports_only=True):
        self.app = app
        self.reports_only = reports_only
        self.slots = BoundedSemaphore(2)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("local_request_checked"):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        try:
            address = urlsplit("//"+host)
            port = address.port
            local = address.hostname in {"localhost","127.0.0.1","::1"} and not (
                address.username or address.password or address.path or address.query or address.fragment)
            if port == 0:
                local = False
            origin = headers.get("origin")
            same_origin = not origin or origin == scope["scheme"]+"://"+host
            length = int(headers.get("content-length","0"))
        except ValueError:
            return await PlainTextResponse("Invalid request",400)(scope,receive,send)
        if not local or not same_origin or headers.get("sec-fetch-site")=="cross-site":
            return await PlainTextResponse("Local access only",403)(scope,receive,send)
        write = scope["method"] not in {"GET","HEAD","OPTIONS"}
        limit = int(os.getenv("NORMCONTROL_MAX_REQUEST_BYTES",128*1024*1024))
        if write and (length<0 or length>limit):
            return await PlainTextResponse("Request too large",413)(scope,receive,send)
        if write and not self.slots.acquire(blocking=False):
            return await PlainTextResponse("Busy",429,headers={"Retry-After":"5"})(scope,receive,send)
        remaining = limit
        async def bounded_receive():
            nonlocal remaining
            message = await receive()
            remaining -= len(message.get("body",b""))
            if remaining < 0:
                raise HTTPException(413,"Request too large")
            return message

        async def guarded_send(message):
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                out["X-Content-Type-Options"] = "nosniff"
                out["Referrer-Policy"] = "no-referrer"
                csp = "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
                if self.reports_only or scope["path"].startswith("/normcontrol/"):
                    csp += "; default-src 'none'; style-src 'unsafe-inline'; img-src 'self'"
                out["Content-Security-Policy"] = csp
            await send(message)
        try:
            await self.app(dict(scope,local_request_checked=True),bounded_receive if write else receive,guarded_send)
        finally:
            if write:
                self.slots.release()
