"""Drive GET child. All exceptions and output exclude credential/provider text."""
import base64
import http.client
import json
import socket
import ssl
import sys
import time

from ...domain import strict_json_loads
from .contract import DriveError, GetRequest, require
from .http import DOUBLE_TOKEN


def main():
    connection = None
    try:
        line = sys.stdin.buffer.readline(16*1024+1)
        require(len(line) <= 16*1024)
        task = strict_json_loads(line)
        require(type(task) is dict and set(task) == {'request','token','deadline','test_stall','test_port'})
        r = task['request']
        require(type(r) is dict and set(r) == {'path','params','limit','content_type'})
        request = GetRequest(r['path'],tuple(tuple(p) for p in r['params']),r['limit'],r['content_type'])
        token = task['token']
        require(type(token) is str and 0 < len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token))
        deadline = task['deadline']
        require(type(deadline) in (int,float) and 0 < deadline-time.monotonic() <= 30)
        port = task['test_port']
        if port is not None:
            require(type(port) is int and 1 <= port <= 65535 and token == DOUBLE_TOKEN)
            require(task['test_stall'] in (None,'dns','tls','send'))
            if task['test_stall']:
                time.sleep(60)  # Parent deadline test; no claim of real DNS/TLS failure.
            connection = http.client.HTTPConnection('127.0.0.1',port,timeout=deadline-time.monotonic())
        else:
            require(task['test_stall'] is None)
            connection = http.client.HTTPSConnection('www.googleapis.com',443,
                        timeout=deadline-time.monotonic(),context=ssl.create_default_context())
        connection.request('GET',request.target,headers={'Authorization':'Bearer '+token,
                           'Accept':request.content_type,'Accept-Encoding':'identity'})
        client_socket = connection.sock
        client_socket.settimeout(max(0.001,deadline-time.monotonic()))
        response = connection.getresponse()
        # http.client does not follow redirects or inherit proxy environment configuration.
        require(response.getheader('Content-Encoding','identity').lower() == 'identity')
        content_type = response.getheader('Content-Type','').split(';')[0].strip().lower()
        require(content_type in ('application/json','text/plain'))
        charset = response.getheader('Content-Type','').lower().partition('charset=')[2].strip(' "')
        require(not charset or charset in ('utf-8','utf8'))
        length = response.getheader('Content-Length')
        require(length is None or (length.isascii() and length.isdigit() and int(length) <= request.limit))
        body = bytearray()
        while True:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            client_socket.settimeout(remaining)
            chunk = response.read1(min(4096,request.limit+1-len(body)))
            if not chunk:
                break
            body.extend(chunk)
            require(len(body) <= request.limit)
            if length is not None and len(body) == int(length):
                break
        require(length is None or len(body) == int(length))
        print(json.dumps({'status':response.status,'body':base64.b64encode(body).decode('ascii'),
                          'content_type':content_type}),flush=True)
        return 0
    except (TimeoutError,socket.timeout):
        code = 'drive_timeout'
    except (OSError,http.client.HTTPException):
        code = 'drive_transport_error'
    except (DriveError,ValueError,TypeError,KeyError,UnicodeError,AttributeError,OverflowError):
        code = 'drive_schema_invalid'
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps({'error':code}),flush=True)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
