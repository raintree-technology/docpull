# Publishing docpull to PyPI

Your package is built and ready for publication! Follow these instructions to publish to PyPI.

## Prerequisites

✅ Already completed:
- Package metadata configured
- Build tools installed (build, twine)
- Distributions built in `dist/` directory
- Local installation tested successfully

## Distribution Files

Your built distributions are in the `dist/` directory:
- `docpull-1.0.0-py3-none-any.whl` (25KB) - Wheel distribution
- `docpull-1.0.0.tar.gz` (19KB) - Source distribution

## Step 1: Create PyPI Account

If you don't have accounts yet:
1. **TestPyPI** (optional, for testing): https://test.pypi.org/account/register/
2. **Production PyPI**: https://pypi.org/account/register/

## Step 2: Create API Tokens

For security, use API tokens instead of passwords:

### For TestPyPI (optional):
1. Go to https://test.pypi.org/manage/account/token/
2. Click "Add API token"
3. Name it (e.g., "docpull-upload")
4. Set scope to "Entire account" or specific to docpull
5. Copy the token (starts with `pypi-`)

### For Production PyPI:
1. Go to https://pypi.org/manage/account/token/
2. Click "Add API token"
3. Name it (e.g., "docpull-upload")
4. Set scope to "Entire account" (change to project-specific after first upload)
5. Copy the token (starts with `pypi-`)

## Step 3: Configure Credentials (Optional)

You can save tokens in `~/.pypirc` to avoid entering them each time:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-YOUR_PRODUCTION_TOKEN_HERE

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-YOUR_TEST_TOKEN_HERE
```

**Important:** Keep this file secure with `chmod 600 ~/.pypirc`

## Step 4: Upload to TestPyPI (Optional but Recommended)

Test the upload process first:

```bash
# Upload to TestPyPI
/Users/zach/.local/bin/twine upload --repository testpypi dist/*

# Or if you didn't configure ~/.pypirc:
/Users/zach/.local/bin/twine upload --repository testpypi dist/* \
  --username __token__ --password pypi-YOUR_TEST_TOKEN
```

Verify the upload at: https://test.pypi.org/project/docpull/

Test installation from TestPyPI:
```bash
# Create a test environment
python3 -m venv test-env
source test-env/bin/activate

# Install from TestPyPI
pip install --index-url https://test.pypi.org/simple/ docpull

# Test it
docpull --version

# Clean up
deactivate
rm -rf test-env
```

## Step 5: Upload to Production PyPI

Once you're confident everything works:

```bash
# Upload to production PyPI
/Users/zach/.local/bin/twine upload dist/*

# Or without ~/.pypirc:
/Users/zach/.local/bin/twine upload dist/* \
  --username __token__ --password pypi-YOUR_PRODUCTION_TOKEN
```

## Step 6: Verify Publication

1. Visit https://pypi.org/project/docpull/
2. Check that all information displays correctly
3. Test installation:
   ```bash
   pip install docpull
   docpull --version
   ```

## Post-Publication

### Update Token Scope
After first successful upload, create a project-specific token:
1. Go to https://pypi.org/manage/project/docpull/settings/
2. Create new token with "Project: docpull" scope
3. Update your `~/.pypirc` with the new token

### Future Releases
For subsequent releases:
1. Update version in `pyproject.toml` and `setup.py`
2. Clean old builds: `rm -rf dist/ build/ *.egg-info`
3. Rebuild: `/Users/zach/.local/bin/pyproject-build`
4. Upload: `/Users/zach/.local/bin/twine upload dist/*`

## Troubleshooting

### "File already exists" error
- PyPI doesn't allow re-uploading same version
- Increment version number and rebuild

### Authentication failed
- Double-check token starts with `pypi-`
- Username must be `__token__` (not your PyPI username)
- Ensure token hasn't expired

### Package name already taken
- If "docpull" is taken, choose a different name
- Update `name` in both `pyproject.toml` and `setup.py`
- Rebuild before uploading

## Important Notes

1. **Email Address**: Your package uses `support@raintree.technology` for contact information.

2. **Version Numbers**: Once published, you cannot delete or modify a release. Always test on TestPyPI first.

3. **Standards Compliance**: Your package now uses modern SPDX license format (PEP 639) and is fully compliant with 2025 PyPI best practices.

4. **Irreversible**: Publishing to PyPI is permanent. Make sure you're ready!

## Quick Command Reference

```bash
# Clean previous builds
rm -rf dist/ build/ *.egg-info

# Build
/Users/zach/.local/bin/pyproject-build

# Check distribution
/Users/zach/.local/bin/twine check dist/*

# Upload to TestPyPI
/Users/zach/.local/bin/twine upload --repository testpypi dist/*

# Upload to PyPI
/Users/zach/.local/bin/twine upload dist/*
```

---

**Ready to publish?** Start with TestPyPI (Step 4) to verify everything works, then proceed to production PyPI (Step 5).
