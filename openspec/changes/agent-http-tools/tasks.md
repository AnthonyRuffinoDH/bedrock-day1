# Tasks

## 1. Herocore Identity

- [x] 1.1 Replace SYSTEM_PROMPT with Herocore identity prompt including: identity/directive, default_tools (filesystem, HTTP, memory), client_tools awareness, EXPLORE-ANALYZE-PROPOSE procedure, preserved memory_guidelines and memory_reconciliation, and future_context. Verify by reading the SYSTEM_PROMPT constant and confirming all sections are present.
- [x] 1.2 Prompt references agent__http_get and agent__http_head in the default_tools section. Verify these tool names appear in the prompt text.

## 2. HTTP Tools Implementation

- [x] 2.1 Add httpx import and HTTP configuration env vars (HTTP_TOOL_TIMEOUT default 15, HTTP_MAX_RESPONSE_SIZE default 102400) at module level after the filesystem tools section. Verify by checking the constants are defined and read from os.environ.
- [x] 2.2 Define WEB_EXTRACTION_PROMPT as a module-level constant. The prompt instructs a sessionless agent to receive raw web content and thread precontext and return only information relevant to the current conversation. Verify the constant exists and mentions both raw content and thread context.
- [x] 2.3 Implement http_head(url, headers) as a plain function using httpx.Client with configured timeout. Return formatted status code and headers. On failure return an error message. Verify by calling the function logic mentally against the spec scenarios (success, failure, timeout).
- [x] 2.4 Implement http_get(url, headers) as a plain function using httpx.Client with configured timeout and max response size truncation. Pass the (possibly truncated) response body plus thread precontext to a nested sessionless, tool-less Agent with WEB_EXTRACTION_PROMPT. Return the extraction agent's output. On failure return an error message. Verify the function handles: successful extraction, truncation note, and request failure.

## 3. Tool Registration

- [x] 3.1 Create _http_tools list via _ns_tool(AGENT_NS, ...) for http_get and http_head, following the same pattern as _filesystem_tools. Verify agent__http_get and agent__http_head appear in the list.
- [x] 3.2 Add _http_tools to TOOL_PROFILES["primary"] lambda. Verify by checking the lambda includes _http_tools in its concatenation.

## 4. Validate and Deploy

- [x] 4.1 Run agentcore validate and confirm it passes with no errors.
- [x] 4.2 Run agentcore deploy -y -v and confirm the deployment succeeds. Note: this takes ~3 minutes.
- [x] 4.3 Verify the deployed agent responds to an invocation via agentcore invoke --bearer-token, confirming the new tools are registered without errors.
