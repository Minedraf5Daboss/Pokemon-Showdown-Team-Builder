"""Local JSON API and CLI. Start with python team_api.py --serve."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from models import build_pool, FORMAT_ID
from team_pipeline import catalog, run_request
from team_chat import run_chat, tool_definitions


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        data = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, {'status': 'ok', 'schema_version': 1})
        if self.path == '/v1/profiles':
            return self.reply(200, catalog())
        if self.path == '/v1/tools':
            return self.reply(200, {'schema_version': 1, 'tools': tool_definitions()})
        if self.path == '/v1/pool':
            return self.reply(200, {'format': FORMAT_ID, 'pokemon': [
                {'name': c.pokemon_sets[0].name, 'sets': [
                    {'set_id': s.set_id, 'label': s.label, 'moves': list(s.moves), 'ability': s.ability, 'item': s.held_Item}
                    for s in c.pokemon_sets]} for c in build_pool()]})
        return self.reply(404, {'error': 'Unknown endpoint'})

    def do_POST(self):
        if self.path not in ('/v1/build', '/v1/chat'):
            return self.reply(404, {'error': 'Unknown endpoint'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                return self.reply(413, {'error': 'Body must be between 1 and 65536 bytes'})
            self.connection.settimeout(15)
            payload = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError, TimeoutError):
            return self.reply(400, {'error': 'Invalid JSON body'})
        try:
            result = run_chat(payload) if self.path == '/v1/chat' else run_request(payload)
            return self.reply(503 if result['status'] == 'service_error' else 200, result)
        except Exception:
            # Do not return credentials or raw provider errors to clients.
            return self.reply(503, {'status': 'service_error', 'message': 'Provider or data unavailable. Check server configuration and retry.'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--serve', action='store_true')
    group.add_argument('--prompt')
    group.add_argument('--request-file', type=Path, help='JSON envelope containing request or prompt')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    if args.serve:
        server = HTTPServer(('127.0.0.1', args.port), Handler)
        print(f'Team API listening on http://127.0.0.1:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    payload = {'prompt': args.prompt} if args.prompt is not None else json.loads(args.request_file.read_text(encoding='utf-8-sig'))
    result = run_request(payload)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'built' else 2


if __name__ == '__main__':
    raise SystemExit(main())
