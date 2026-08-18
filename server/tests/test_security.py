"""Tests for server security hardening.

Tests validation logic as pure functions without requiring httpx/TestClient.
Covers: zip-slip rejection, download path constraints, token-scrubbing.
"""
import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

# Import the validation functions under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.main import (
    validate_zip_members,
    validate_download_path,
    scrub_tokens,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
)


# ─── ZIP-SLIP tests ─────────────────────────────────────────────────────────


class TestValidateZipMembers:
    """Tests for validate_zip_members: path traversal, caps, absolute paths."""

    def _make_zip(self, members: dict[str, bytes]) -> zipfile.ZipFile:
        """Create an in-memory ZipFile with given {filename: content} entries."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        buf.seek(0)
        return zipfile.ZipFile(buf, 'r')

    def test_normal_zip_passes(self, tmp_path):
        zf = self._make_zip({'src/main.py': b'print("hello")', 'README.md': b'# hi'})
        # Should not raise
        validate_zip_members(zf, tmp_path)

    def test_dotdot_traversal_rejected(self, tmp_path):
        zf = self._make_zip({'../../etc/passwd': b'pwned'})
        with pytest.raises(HTTPException) as exc_info:
            validate_zip_members(zf, tmp_path)
        assert exc_info.value.status_code == 400
        assert 'traversal' in exc_info.value.detail.lower()

    def test_absolute_path_rejected(self, tmp_path):
        zf = self._make_zip({'/etc/shadow': b'root:x:0'})
        with pytest.raises(HTTPException) as exc_info:
            validate_zip_members(zf, tmp_path)
        assert exc_info.value.status_code == 400
        assert 'absolute' in exc_info.value.detail.lower()

    def test_nested_dotdot_rejected(self, tmp_path):
        zf = self._make_zip({'foo/bar/../../../etc/passwd': b'pwned'})
        with pytest.raises(HTTPException) as exc_info:
            validate_zip_members(zf, tmp_path)
        assert exc_info.value.status_code == 400

    def test_member_count_cap(self, tmp_path):
        """ZIP with more than MAX_ZIP_MEMBERS entries is rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for i in range(MAX_ZIP_MEMBERS + 1):
                zf.writestr(f'file_{i}.txt', b'x')
        buf.seek(0)
        zf = zipfile.ZipFile(buf, 'r')
        with pytest.raises(HTTPException) as exc_info:
            validate_zip_members(zf, tmp_path)
        assert exc_info.value.status_code == 400
        assert 'too many entries' in exc_info.value.detail.lower()

    def test_uncompressed_size_cap(self, tmp_path):
        """ZIP whose total uncompressed size exceeds limit is rejected."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            # Create a single large entry that exceeds the limit
            # ZipInfo.file_size is what we check, so a highly compressible payload works
            data = b'\x00' * (MAX_ZIP_UNCOMPRESSED_BYTES + 1)
            zf.writestr('big.bin', data)
        buf.seek(0)
        zf = zipfile.ZipFile(buf, 'r')
        with pytest.raises(HTTPException) as exc_info:
            validate_zip_members(zf, tmp_path)
        assert exc_info.value.status_code == 400
        assert 'size exceeds' in exc_info.value.detail.lower()

    def test_backslash_absolute_rejected(self, tmp_path):
        """Windows-style absolute path with backslash."""
        zf = self._make_zip({'\\Windows\\System32\\config': b'pwned'})
        with pytest.raises(HTTPException) as exc_info:
            validate_zip_members(zf, tmp_path)
        assert exc_info.value.status_code == 400


# ─── Download path constraint tests ─────────────────────────────────────────


class TestValidateDownloadPath:
    """Tests for validate_download_path: naming, traversal, containment."""

    def test_valid_codescribe_zip(self):
        """A properly-named file that exists in temp dir passes."""
        temp_dir = tempfile.gettempdir()
        # Create a real file to test against
        test_file = Path(temp_dir) / 'codescribe-docs-myproject.zip'
        test_file.write_bytes(b'PK\x03\x04fake')
        try:
            result = validate_download_path('codescribe-docs-myproject.zip')
            assert result == test_file.resolve()
        finally:
            test_file.unlink(missing_ok=True)

    def test_wrong_prefix_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('evil-docs-project.zip')
        assert exc_info.value.status_code == 400

    def test_wrong_suffix_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('codescribe-docs-project.tar.gz')
        assert exc_info.value.status_code == 400

    def test_dotdot_traversal_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('..%2F..%2Fetc%2Fpasswd')
        assert exc_info.value.status_code == 400

    def test_path_separator_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('sub/codescribe-docs-project.zip')
        assert exc_info.value.status_code == 400

    def test_dotdot_in_name_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('../codescribe-docs-project.zip')
        assert exc_info.value.status_code == 400

    def test_nonexistent_file_404(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('codescribe-docs-nonexistent-xyz.zip')
        assert exc_info.value.status_code == 404

    def test_bare_zip_name_no_prefix(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('project.zip')
        assert exc_info.value.status_code == 400

    def test_empty_string_rejected(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_download_path('')
        assert exc_info.value.status_code == 400


# ─── Token scrubbing tests ───────────────────────────────────────────────────


class TestScrubTokens:
    """Tests for scrub_tokens: removes GitHub PATs, OAuth tokens, clone URLs."""

    def test_classic_pat_scrubbed(self):
        msg = 'Error cloning: ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789'
        result = scrub_tokens(msg)
        assert 'ghp_' not in result
        assert '[REDACTED]' in result

    def test_fine_grained_pat_scrubbed(self):
        fake_pat = 'github_pat_' + 'A' * 82
        msg = f'Auth failed with token {fake_pat}'
        result = scrub_tokens(msg)
        assert 'github_pat_' not in result
        assert '[REDACTED]' in result

    def test_oauth_token_scrubbed(self):
        msg = 'Token: gho_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789'
        result = scrub_tokens(msg)
        assert 'gho_' not in result
        assert '[REDACTED]' in result

    def test_clone_url_token_scrubbed(self):
        msg = 'Failed to clone https://x-access-token:ghp_abc123XYZ789012345678901234567890@github.com/user/repo.git'
        result = scrub_tokens(msg)
        assert 'x-access-token' not in result
        assert 'ghp_' not in result
        assert '[REDACTED]' in result

    def test_no_token_unchanged(self):
        msg = 'Repository not found: user/repo'
        result = scrub_tokens(msg)
        assert result == msg

    def test_multiple_tokens_all_scrubbed(self):
        msg = 'Tried ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789 then gho_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789'
        result = scrub_tokens(msg)
        assert result.count('[REDACTED]') == 2
        assert 'ghp_' not in result
        assert 'gho_' not in result

    def test_token_in_exception_string(self):
        """Simulates what happens when str(exception) contains a clone URL."""
        fake_error = "Cmd('git') failed: stderr: 'fatal: could not read from remote repo https://x-access-token:gho_Secret12345678901234567890123456@github.com/u/r.git'"
        result = scrub_tokens(fake_error)
        assert 'gho_' not in result
        assert 'Secret' not in result
