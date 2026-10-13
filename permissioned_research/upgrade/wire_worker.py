"""Owned network worker. No credentials, secrets, retries or arbitrary origins."""
import base64
import http.client
import json
import socket
import sys
import time

from ..domain import ResearchError, strict_json_loads
from ..transport import _SendBoundedConnection


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    connection = None
    possibly_sent = False
    try:
        line = sys.stdin.buffer.readline(96 * 1024 + 1)
        if len(line) > 96 * 1024:
            raise ValueError()
        task = strict_json_loads(line)
        if task['host'] != '127.0.0.1' or type(task['port']) is not int or not 1 <= task['port'] <= 65535:
            raise ValueError()
        if task['test_stall'] in {'dns', 'tls', 'send'}:
            # An owned blocking phase tests the parent deadline, not remote TLS.
            time.sleep(60)
        wire = json.dumps(task['request'], ensure_ascii=False, separators=(',', ':')).encode()
        connection = _SendBoundedConnection(task['host'], task['port'], task['send_deadline'])
        possibly_sent = True
        connection.request('POST', '/v1/chat/completions', body=wire,
                           headers={'Content-Type': 'application/json', 'Authorization': 'Bearer LOCAL_DOUBLE_NOT_A_CREDENTIAL'})
        emit({'event': 'sent'})  # Body transmission complete, before headers/body collection.
        remaining = task['deadline'] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        client_socket = connection.sock
        client_socket.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            emit({'event': 'http_error', 'status': response.status})
            return 0
        if (response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json' or
                response.getheader('Content-Encoding', 'identity') != 'identity'):
            raise ValueError()
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > task['response_limit']):
            raise ValueError()
        body = bytearray()
        while True:
            remaining = task['deadline'] - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            client_socket.settimeout(remaining)
            chunk = response.read1(min(4096, task['response_limit'] + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > task['response_limit']:
                raise ValueError()
            if length is not None and len(body) == int(length):
                break
        if length is not None and len(body) != int(length):
            raise http.client.IncompleteRead(bytes(body))
        emit({'event': 'response', 'body': base64.b64encode(body).decode('ascii')})
        return 0
    except (TimeoutError, socket.timeout, ResearchError):
        emit({'event': 'error', 'error': 'provider_timeout', 'possibly_sent': possibly_sent})
    except (OSError, http.client.HTTPException):
        emit({'event': 'error', 'error': 'gateway_response_lost', 'possibly_sent': possibly_sent})
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        emit({'event': 'error', 'error': 'gateway_response_invalid', 'possibly_sent': possibly_sent})
    finally:
        if connection is not None:
            connection.close()
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
