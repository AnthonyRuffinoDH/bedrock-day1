# Spec Delta

## Purpose

Provides cloud-side HTTP tools that run natively in the AgentCore container, giving the agent the ability to fetch web content via GET and HEAD requests, with a nested content-extraction agent that distills raw responses into clean, relevant information.

## ADDED Requirements

### Requirement: HTTP GET with content extraction

The agent SHALL have a tool to perform an HTTP GET request to a given URL, optionally with custom headers. The raw response SHALL be passed to a nested, sessionless, tool-less agent along with the current thread precontext, which SHALL return distilled content to the primary agent. If the request fails, the tool SHALL return an error message.

#### Scenario: Successful GET with extraction

- **WHEN** the agent calls the HTTP GET tool with a valid, reachable URL
- **THEN** the tool SHALL fetch the response body, pass it with thread precontext to a content-extraction agent, and return the extracted content

#### Scenario: GET request failure

- **WHEN** the agent calls the HTTP GET tool with an unreachable URL or the request times out
- **THEN** the tool SHALL return an error message describing the failure

#### Scenario: Response exceeds size limit

- **WHEN** the HTTP response body exceeds the configured maximum size
- **THEN** the tool SHALL truncate the response and include a note that it was truncated before passing it to the extraction agent

### Requirement: HTTP HEAD for metadata

The agent SHALL have a tool to perform an HTTP HEAD request to a given URL, optionally with custom headers. The tool SHALL return response status code and headers without fetching the body.

#### Scenario: Successful HEAD request

- **WHEN** the agent calls the HTTP HEAD tool with a valid URL
- **THEN** the tool SHALL return the response status code and headers

#### Scenario: HEAD request failure

- **WHEN** the agent calls the HTTP HEAD tool with an unreachable URL or the request times out
- **THEN** the tool SHALL return an error message describing the failure

### Requirement: Configurable timeout and response size

HTTP tools SHALL respect a configurable request timeout and a configurable maximum response size. Both SHALL be settable via environment variables with sensible defaults.

#### Scenario: Default configuration

- **WHEN** no environment variables override timeout or size
- **THEN** HTTP tools SHALL use a default timeout of 15 seconds and a default maximum response size of 100KB

#### Scenario: Custom configuration

- **WHEN** environment variables set custom timeout and size values
- **THEN** HTTP tools SHALL use those values instead of the defaults

### Requirement: Nested content-extraction agent

HTTP GET responses SHALL be processed by a nested agent that receives the raw response content and thread precontext, and returns a distilled summary of the relevant information. The extraction agent SHALL have no tools and no session state.

#### Scenario: Extraction agent receives context

- **WHEN** an HTTP GET response is passed to the extraction agent
- **THEN** the extraction agent SHALL receive the raw content and recent thread context to inform what information is relevant

#### Scenario: Extraction agent has no tools

- **WHEN** the extraction agent is constructed
- **THEN** it SHALL have no tools and no session manager — it processes only the text it receives
