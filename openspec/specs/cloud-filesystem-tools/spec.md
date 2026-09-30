# Spec: cloud-filesystem-tools

## Purpose

Provides cloud-side filesystem exploration tools that run natively in the AgentCore container, giving the agent direct read-only access to files within a configurable allowed directory.

## Requirements

### Requirement: Read file contents
The agent SHALL have a tool to read the complete contents of a file by path. The tool SHALL return the file's text content. If the file does not exist or is outside the allowed directory, the tool SHALL return an error message.

#### Scenario: Successful file read
- **WHEN** the agent calls the read file tool with a path to an existing file within the allowed directory
- **THEN** the tool SHALL return the full text contents of the file

#### Scenario: File outside allowed directory
- **WHEN** the agent calls the read file tool with a path that resolves outside the allowed directory
- **THEN** the tool SHALL return an error indicating the path is not permitted

#### Scenario: File does not exist
- **WHEN** the agent calls the read file tool with a path to a non-existent file
- **THEN** the tool SHALL return an error indicating the file was not found

### Requirement: List directory contents
The agent SHALL have a tool to list the contents of a directory, showing entries with type indicators (file or directory). The tool SHALL only operate within the allowed directory.

#### Scenario: Successful directory listing
- **WHEN** the agent calls the list directory tool with a path to an existing directory within the allowed directory
- **THEN** the tool SHALL return a list of entries, each prefixed with [FILE] or [DIR]

#### Scenario: Directory outside allowed directory
- **WHEN** the agent calls the list directory tool with a path outside the allowed directory
- **THEN** the tool SHALL return an error indicating the path is not permitted

### Requirement: Search files by pattern
The agent SHALL have a tool to recursively search for files and directories matching a glob pattern within a starting directory. The search SHALL be constrained to the allowed directory.

#### Scenario: Pattern matches found
- **WHEN** the agent calls the search tool with a starting path and a glob pattern that matches files
- **THEN** the tool SHALL return the full paths of all matching entries

#### Scenario: No matches
- **WHEN** the agent calls the search tool with a pattern that matches no files
- **THEN** the tool SHALL return a message indicating no matches were found

### Requirement: Get file metadata
The agent SHALL have a tool to retrieve metadata about a file or directory, including size, modification time, and type (file or directory). The tool SHALL only operate within the allowed directory.

#### Scenario: File metadata retrieved
- **WHEN** the agent calls the file info tool with a path to an existing file
- **THEN** the tool SHALL return the file's size, last modified time, and type

#### Scenario: Path outside allowed directory
- **WHEN** the agent calls the file info tool with a path outside the allowed directory
- **THEN** the tool SHALL return an error indicating the path is not permitted

### Requirement: Configurable allowed directory
All filesystem tools SHALL be scoped to a single allowed directory, configurable via environment variable. Paths that resolve outside this directory (including via symlinks or `..` traversal) SHALL be rejected. The default allowed directory SHALL be the container's working directory.

#### Scenario: Default allowed directory
- **WHEN** no environment variable overrides the allowed directory
- **THEN** all filesystem tools SHALL operate relative to and constrained within the container's working directory

#### Scenario: Custom allowed directory
- **WHEN** the allowed directory environment variable is set to a specific path
- **THEN** all filesystem tools SHALL operate relative to and constrained within that path
