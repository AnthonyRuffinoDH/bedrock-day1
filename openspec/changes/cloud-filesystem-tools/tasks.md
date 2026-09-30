# Tasks

## 1. Remove workshop scaffolding

- [ ] 1.1 Delete `RETURN_POLICIES` dict, `PRODUCTS` dict, `get_return_policy` tool, and `get_product_info` tool from `app/CustomerSupport/main.py`. Verify: `grep -c 'RETURN_POLICIES\|PRODUCTS\|get_return_policy\|get_product_info' app/CustomerSupport/main.py` returns 0.

## 2. Implement filesystem tools

- [ ] 2.1 Add `_FS_ALLOWED_DIR` module-level constant that reads `FS_ALLOWED_DIR` env var, defaulting to `os.getcwd()`, resolved via `os.path.realpath`. Add `_resolve_safe_path(requested_path) -> tuple[str, str | None]` helper that resolves and validates paths against the allowed directory. Verify: import the module without errors (`python -c "from main import _resolve_safe_path"`).
- [ ] 2.2 Add `@tool` function `read_file(path: str) -> str` that reads and returns file contents, using `_resolve_safe_path` for validation. Returns error strings for out-of-bounds paths and missing files. Verify: call with a valid file path and an out-of-bounds path and confirm correct results.
- [ ] 2.3 Add `@tool` function `list_directory(path: str) -> str` that lists directory entries with `[FILE]`/`[DIR]` prefixes. Verify: call on the app directory and confirm output shows typed entries.
- [ ] 2.4 Add `@tool` function `search_files(path: str, pattern: str) -> str` that recursively searches for files matching a glob pattern using `pathlib.Path.rglob`. Verify: search for `*.py` in the app directory and confirm matching paths are returned.
- [ ] 2.5 Add `@tool` function `get_file_info(path: str) -> str` that returns file size, modification time, and type (file/directory). Verify: call on a known file and confirm size, time, and type are present in output.

## 3. Update tool profiles and system prompt

- [ ] 3.1 Replace `_domain_tools` with `_filesystem_tools = [read_file, list_directory, search_files, get_file_info]`. Update `TOOL_PROFILES["primary"]` lambda to use `_filesystem_tools`. Verify: `grep '_filesystem_tools' app/CustomerSupport/main.py` matches both the list definition and the profile reference.
- [ ] 3.2 Update `SYSTEM_PROMPT` `<support_guidelines>` section to reference filesystem exploration capabilities instead of product/return-policy tools. Verify: `grep -c 'product\|return.policy' app/CustomerSupport/main.py` returns 0 (no remaining references outside comments).

## 4. Deploy and verify

- [ ] 4.1 Run `agentcore validate` to confirm the updated code passes validation. Verify: command exits 0.
- [ ] 4.2 Deploy with `agentcore deploy -y -v` and verify the filesystem tools are available by invoking the agent (via `generate_curl.py` or direct curl) with a prompt like "list the files in your working directory". Verify: response includes directory listing output. Note: deploy takes ~3 minutes.
