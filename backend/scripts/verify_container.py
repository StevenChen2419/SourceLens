"""Offline Docker smoke test. No host credentials, files, ports, or network are shared."""

import json
import subprocess
import sys


CHECK = r'''
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

assert os.getuid() == 10001, "Runtime must be non-root"
for root, directories, files in os.walk('/'):
    directories[:] = [name for name in directories if str(Path(root) / name) not in ('/proc', '/sys', '/dev')]
    for name in directories + files:
        path = Path(root) / name
        assert not (name == '.env' or name.startswith('.env.') or name.lower().endswith('.pdf')), str(path)
        assert name not in ('.azure', '.aws', '.ssh', '.git', 'evaluation', 'results'), str(path)
        assert name not in ('credentials', 'credentials.json', 'id_rsa', 'id_ed25519', 'accessTokens.json', 'msal_token_cache.json'), str(path)
        if path.is_file() and path.suffix.lower() in ('.pem', '.key'):
            # Public CA bundles are dependencies, not private credentials.
            assert b'PRIVATE KEY-----' not in path.read_bytes(), str(path)
print('PASS: non-root; no dotenv/PDF/evaluation artifacts or standard credential files', flush=True)

import tiktoken
for name in ('cl100k_base', 'o200k_base'):
    assert tiktoken.get_encoding(name).encode('Offline tokenizer check')
print('PASS: both tokenizer caches work with --network none', flush=True)

# Validate that the actual container default command starts with production settings.
process = subprocess.Popen(['python', '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1'])
try:
    for attempt in range(50):
        if process.poll() is not None:
            raise RuntimeError('Server exited before becoming healthy')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=1) as response:
                assert response.status == 200
                assert json.load(response) == {'status': 'ok'}
            print('PASS: production /health returns 200 with expected JSON, without Azure', flush=True)
            break
        except OSError:
            time.sleep(0.2)
    else:
        raise RuntimeError('Health did not become available')
finally:
    process.terminate()
    process.wait(timeout=10)
'''


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else 'knowledgeops-backend:readiness'
    config = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]['Config']
    assert config['User'] == '10001:10001'
    assert config['Cmd'] == ['python', '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1']
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--cpus', '0.5', '--memory', '1g']
    values = {
        'KNOWLEDGEOPS_CORS_ORIGINS': '["https://frontend.example.com"]',
        'AZURE_STORAGE_ACCOUNT_URL': 'https://storage.example.com',
        'AZURE_SEARCH_ENDPOINT': 'https://search.example.com',
        'AZURE_EMBEDDING_ENDPOINT': 'https://models.example.com/openai/v1/',
        'AZURE_EMBEDDING_DEPLOYMENT': 'example-embedding',
        'AZURE_GENERATION_ENDPOINT': 'https://models.example.com/openai/v1/',
        'AZURE_GENERATION_DEPLOYMENT': 'example-generation',
    }
    for key, value in values.items():
        command.extend(['--env', f'{key}={value}'])
    command.extend(['--entrypoint', 'python', image, '-'])
    subprocess.run(command, input=CHECK, text=True, check=True)


if __name__ == '__main__':
    main()
