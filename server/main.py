"""This module is the main entry point for the FastAPI application, handling GitHub authentication, repository interactions, and project processing."""
import os
import re
import tempfile
import zipfile
import shutil
from pathlib import Path
import requests
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from git import Repo
from github import Github, GithubException
from .tasks import process_project
from dotenv import load_dotenv
load_dotenv()

GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID')
GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET')

# --- Security constants ---
# ZIP upload limits to prevent zip-bomb denial of service
MAX_ZIP_MEMBERS = 5000  # max number of entries in the archive
MAX_ZIP_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB total uncompressed

# Token pattern for scrubbing from error messages
_TOKEN_PATTERN = re.compile(
    r'(ghp_[A-Za-z0-9]{36}'           # GitHub PATs (classic)
    r'|github_pat_[A-Za-z0-9_]{82}'   # GitHub PATs (fine-grained)
    r'|gho_[A-Za-z0-9]{36}'           # GitHub OAuth tokens
    r'|x-access-token:[^\s@]+)'       # tokenized clone URLs
)


def scrub_tokens(text: str) -> str:
    """Remove any token-shaped strings from text before surfacing to clients."""
    return _TOKEN_PATTERN.sub('[REDACTED]', str(text))


def validate_download_path(file_path: str) -> Path:
    """Validate that file_path is a codescribe ZIP directly inside the temp dir.

    Raises HTTPException on any violation.
    Returns the resolved full path.
    """
    # Reject path separators and traversal
    if '/' in file_path or '\\' in file_path or '..' in file_path:
        raise HTTPException(status_code=400, detail='Invalid file path.')

    # Require codescribe naming scheme
    if not file_path.startswith('codescribe-docs-') or not file_path.endswith('.zip'):
        raise HTTPException(status_code=400, detail='Invalid file path.')

    temp_dir = Path(tempfile.gettempdir())
    full_path = (temp_dir / file_path).resolve()

    # Confirm it resolves directly inside the temp dir (no symlink escape)
    if full_path.parent != temp_dir.resolve():
        raise HTTPException(status_code=400, detail='Invalid file path.')

    if not full_path.exists():
        raise HTTPException(status_code=404, detail='File not found or expired.')

    return full_path


def validate_zip_members(zip_ref: zipfile.ZipFile, extract_root: Path) -> None:
    """Validate all ZIP members for zip-slip, count, and size caps.

    Raises HTTPException on any violation.
    """
    members = zip_ref.infolist()

    # Count cap
    if len(members) > MAX_ZIP_MEMBERS:
        raise HTTPException(
            status_code=400,
            detail=f'ZIP archive contains too many entries (limit: {MAX_ZIP_MEMBERS}).'
        )

    # Size cap (sum of uncompressed sizes)
    total_size = sum(m.file_size for m in members)
    if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f'ZIP archive uncompressed size exceeds limit ({MAX_ZIP_UNCOMPRESSED_BYTES // (1024*1024)} MB).'
        )

    resolved_root = extract_root.resolve()
    for member in members:
        # Reject absolute paths
        if member.filename.startswith('/') or member.filename.startswith('\\'):
            raise HTTPException(
                status_code=400,
                detail='ZIP archive contains absolute paths — rejected.'
            )
        # Reject '..' components
        if '..' in Path(member.filename).parts:
            raise HTTPException(
                status_code=400,
                detail='ZIP archive contains path traversal — rejected.'
            )
        # Resolve and verify containment
        target = (extract_root / member.filename).resolve()
        if not (target == resolved_root or str(target).startswith(str(resolved_root) + os.sep)):
            raise HTTPException(
                status_code=400,
                detail='ZIP archive contains path traversal — rejected.'
            )


app = FastAPI()

# CORS configuration — use explicit origins, not wildcard
_default_origins = [
    'http://localhost',
    'http://localhost:8000',
    'http://127.0.0.1',
    'http://127.0.0.1:8000',
]
_env_origins = os.getenv('ALLOWED_ORIGINS', '')
_extra_origins = [o.strip() for o in _env_origins.split(',') if o.strip()] if _env_origins else []
origins = _default_origins + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.mount('/static', StaticFiles(directory='static'), name='static')


@app.get('/')
async def read_root():
    return FileResponse('static/index.html')


@app.get('/login/github')
async def login_github():
    return RedirectResponse(
        f'https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&scope=repo',
        status_code=302,
    )


@app.get('/auth/github/callback')
async def auth_github_callback(code: str, request: Request):
    params = {'client_id': GITHUB_CLIENT_ID, 'client_secret': GITHUB_CLIENT_SECRET, 'code': code}
    headers = {'Accept': 'application/json'}
    base_url = str(request.base_url).rstrip('/')
    try:
        response = requests.post('https://github.com/login/oauth/access_token', params=params, headers=headers)
        response.raise_for_status()
        response_json = response.json()
        if 'error' in response_json:
            error_description = response_json.get('error_description', 'Unknown error.')
            return RedirectResponse(f'{base_url}/?error={error_description}')
        token = response_json.get('access_token')
        if not token:
            return RedirectResponse(f'{base_url}/?error=Authentication failed, no token received.')
        # Deliver token in URL fragment — fragments never leave the browser,
        # never hit server logs or proxy logs, never appear in Referer headers.
        return RedirectResponse(f'{base_url}/#token={token}')
    except requests.exceptions.RequestException as e:
        return RedirectResponse(f'{base_url}/?error=Failed to connect to GitHub.')


@app.get('/api/github/repos')
async def get_github_repos(request: Request):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Unauthorized')
    token = auth_header.split(' ')[1]
    try:
        g = Github(token)
        user = g.get_user()
        repos = [{'full_name': repo.full_name, 'default_branch': repo.default_branch} for repo in user.get_repos(type='owner')]
        return repos
    except GithubException as e:
        raise HTTPException(status_code=e.status, detail=scrub_tokens(f'GitHub API error: {e.data.get("message", "Unknown error")}'))
    except Exception as e:
        raise HTTPException(status_code=500, detail=scrub_tokens(f'Failed to fetch repos: {e}'))


@app.get('/api/github/branches')
async def get_github_repo_branches(request: Request, repo_full_name: str):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Unauthorized')
    token = auth_header.split(' ')[1]
    try:
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        branches = [branch.name for branch in repo.get_branches()]
        return branches
    except GithubException as e:
        raise HTTPException(status_code=e.status, detail=scrub_tokens(f"GitHub API error: {e.data.get('message', 'Could not fetch branches.')}"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=scrub_tokens(f'An unexpected error occurred while fetching branches: {e}'))


@app.get('/api/github/tree')
async def get_github_repo_tree(request: Request, repo_full_name: str, branch: str):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Unauthorized')
    token = auth_header.split(' ')[1]
    temp_dir = tempfile.mkdtemp(prefix='codescribe-tree-')
    try:
        repo_url = f'https://x-access-token:{token}@github.com/{repo_full_name}.git'
        Repo.clone_from(repo_url, temp_dir, branch=branch, depth=1)
        repo_path = Path(temp_dir)
        tree = []
        for root, dirs, files in os.walk(repo_path):
            if '.git' in dirs:
                dirs.remove('.git')
            current_level = tree
            rel_path = Path(root).relative_to(repo_path)
            if str(rel_path) != '.':
                for part in rel_path.parts:
                    parent = next((item for item in current_level if item['name'] == part), None)
                    if not parent:
                        break
                    current_level = parent.get('children', [])
            for d in sorted(dirs):
                current_level.append({'name': d, 'children': []})
            for f in sorted(files):
                current_level.append({'name': f})
        return tree
    except Exception as e:
        raise HTTPException(status_code=500, detail=scrub_tokens(f'Failed to clone or process repo tree: {e}'))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get('/api/github/branch-exists')
async def check_branch_exists(request: Request, repo_full_name: str, branch_name: str):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Unauthorized')
    token = auth_header.split(' ')[1]
    try:
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        repo.get_branch(branch=branch_name)
        return {'exists': True}
    except GithubException as e:
        if e.status == 404:
            return {'exists': False}
        raise HTTPException(status_code=e.status, detail=scrub_tokens(f"GitHub API error: {e.data.get('message', 'Unknown error')}"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=scrub_tokens(f'An unexpected error occurred: {e}'))


@app.post('/process-zip')
async def process_zip_endpoint(description: str = Form(...), readme_note: str = Form(''), zip_file: UploadFile = File(...), exclude_patterns: str = Form('')):
    exclude_list = [p.strip() for p in exclude_patterns.splitlines() if p.strip()]
    temp_dir = tempfile.mkdtemp(prefix='codescribe-zip-')
    project_path = Path(temp_dir)
    zip_location = project_path / zip_file.filename
    with open(zip_location, 'wb+') as f:
        shutil.copyfileobj(zip_file.file, f)

    with zipfile.ZipFile(zip_location, 'r') as zip_ref:
        # Validate all members before extraction
        validate_zip_members(zip_ref, project_path)
        zip_ref.extractall(project_path)

    os.remove(zip_location)
    stream_headers = {'Content-Type': 'text/plain', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'}
    placeholder_repo_name = f'zip-upload/{Path(zip_file.filename).stem}'
    return StreamingResponse(
        process_project(
            project_path=project_path, description=description, readme_note=readme_note,
            is_temp=True, exclude_list=exclude_list, repo_full_name=placeholder_repo_name,
        ),
        headers=stream_headers, media_type='text/plain',
    )


@app.post('/process-github')
async def process_github_endpoint(request: Request, repo_full_name: str = Form(...), base_branch: str = Form(...), new_branch_name: str = Form(...), description: str = Form(...), readme_note: str = Form(''), exclude_patterns: str = Form(''), exclude_paths: List[str] = Form([])):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Unauthorized')
    token = auth_header.split(' ')[1]
    try:
        g = Github(token)
        repo = g.get_repo(repo_full_name)
        existing_branches = [b.name for b in repo.get_branches()]
        if new_branch_name in existing_branches:
            raise HTTPException(status_code=409, detail=f"Branch '{new_branch_name}' already exists. Please use a different name.")
    except GithubException as e:
        # Map status codes honestly instead of blanket 404
        status = e.status if e.status else 500
        if status == 401:
            detail = 'GitHub authentication failed. Token may be expired or invalid.'
        elif status == 403:
            detail = 'Access denied. Token lacks required permissions for this repository.'
        elif status == 404:
            detail = f"Repository '{repo_full_name}' not found or not accessible with current token."
        else:
            detail = scrub_tokens(f'GitHub API error ({status}): {e.data.get("message", "Unknown error")}')
        raise HTTPException(status_code=status, detail=detail)
    except HTTPException:
        raise  # Re-raise our own 409
    except Exception as e:
        raise HTTPException(status_code=500, detail=scrub_tokens(f'An error occurred while checking branches: {e}'))

    regex_list = [p.strip() for p in exclude_patterns.splitlines() if p.strip()]
    exclude_list = regex_list + exclude_paths
    temp_dir = tempfile.mkdtemp(prefix='codescribe-git-')
    project_path = Path(temp_dir)
    repo_url = f'https://x-access-token:{token}@github.com/{repo_full_name}.git'
    try:
        Repo.clone_from(repo_url, project_path, branch=base_branch)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=scrub_tokens(f'Failed to clone repository: {e}'))

    stream_headers = {'Content-Type': 'text/plain', 'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'}
    return StreamingResponse(
        process_project(
            project_path=project_path, description=description, readme_note=readme_note,
            is_temp=True, new_branch_name=new_branch_name, repo_full_name=repo_full_name,
            github_token=token, exclude_list=exclude_list,
        ),
        headers=stream_headers, media_type='text/plain',
    )


@app.get('/download/{file_path}')
async def download_file(file_path: str):
    full_path = validate_download_path(file_path)
    return FileResponse(path=full_path, filename=file_path, media_type='application/zip')
