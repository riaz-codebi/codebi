"""Ephemeral HTTPS CONNECT proxy: exact host, public DNS pinning, TLS stays end to end."""
from contextlib import contextmanager
import select
import socket
import socketserver
import threading
import time

from commerce_hub.connector_contracts import ConnectorLoginError
from commerce_hub.vendors.shiloh_tack.login import public_addresses


class TunnelHandler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream=None
        try:
            self.connection.settimeout(15)
            line=self.rfile.readline(8193)
            if len(line)>8192:return
            parts=line.decode('ascii').strip().split()
            size=len(line)
            while True:
                header=self.rfile.readline(8193);size+=len(header)
                if size>8192:return
                if header in (b'\r\n',b'\n',b''):break
            # No HTTP forwarding, alternate ports, IP literals, userinfo or redirects
            # to a different authority. Redirect CONNECTs are checked here as well.
            if len(parts)!=3 or parts[0]!='CONNECT' or parts[1].lower()!=self.server.authority or parts[2] not in ('HTTP/1.0','HTTP/1.1'):
                self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');return
            if self.server.stopping.is_set() or time.monotonic()>self.server.deadline:return
            for address in self.server.addresses:
                try:upstream=socket.create_connection((address,443),timeout=15);break
                except OSError:continue
            if upstream is None:
                self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');return
            self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');self.wfile.flush()
            while not self.server.stopping.is_set() and time.monotonic()<self.server.deadline:
                ready,_,_=select.select([self.connection,upstream],[],[],1)
                for source in ready:
                    data=source.recv(65536)
                    if not data:return
                    with self.server.budget_lock:
                        self.server.byte_count+=len(data)
                        if self.server.byte_count>self.server.max_bytes:
                            self.server.exceeded=True;self.server.stopping.set();return
                    (upstream if source is self.connection else self.connection).sendall(data)
        except (OSError,ValueError,UnicodeError):
            pass  # Never log an authority, URL, TLS payload, credentials or cookies.
        finally:
            if upstream is not None:upstream.close()


class TunnelServer(socketserver.ThreadingTCPServer):
    daemon_threads=True
    allow_reuse_address=False


@contextmanager
def pinned_browser_proxy(host,lifetime=120,max_bytes=64*1024*1024):
    addresses=public_addresses(host)  # Reject the complete DNS answer if any IP is not public.
    addresses=sorted(addresses,key=lambda address:':' in address)
    server=TunnelServer(('127.0.0.1',0),TunnelHandler)
    server.authority=host.lower()+':443';server.addresses=addresses
    server.stopping=threading.Event();server.deadline=time.monotonic()+min(14400,max(1,lifetime))
    server.max_bytes=min(512*1024*1024,max(1,max_bytes))
    server.budget_lock=threading.Lock();server.byte_count=0;server.exceeded=False
    thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.2},daemon=True)
    thread.start()
    try:yield {'server':'http://127.0.0.1:'+str(server.server_address[1])},server
    finally:
        server.stopping.set();server.shutdown();server.server_close();thread.join(timeout=2)
